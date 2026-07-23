"""Mine structured tenant facts out of freeform ActivityLog text.

Jack's activity logs are humanized notes ("market search for properties between
300 and 700 sqft ... Alexandria, access is important - elevator"). This service
reads them, sends the text to Anthropic with **structured JSON output**, and
writes each stated fact as one row in `observations` — the same machine-usable
form lease extraction produces, feeding the Review queue and signal engine.

Two hard rules:
  1. **ActivityLog is READ-ONLY here.** Nothing in this module writes to, edits,
     or deletes an activity log. Facts are written to `observations` only.
  2. **Only STATED facts.** The prompt forbids inferring or guessing; a field the
     note doesn't state comes back null and is simply not recorded.
"""

import os
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.models.observation import Observation
from app.services.document_extraction_service import (
    EXTRACTION_MODEL,
    MissingAPIKeyError,
)

# Tenant-requirement fields we mine from conversation notes. These are STATED
# requirements (the tenant said them), never inferred from headcount math —
# they are deliberately kept distinct from the `sig_*` inferred signals.
REQUIREMENT_FIELDS: Dict[str, str] = {
    "req_sf_min": "Minimum square footage the tenant wants (number only).",
    "req_sf_max": "Maximum square footage the tenant wants (number only).",
    "req_submarkets": "Target submarkets/locations named (comma-separated).",
    "req_budget_max_psf": "Budget ceiling in $/SF or total rent, as stated.",
    "req_lease_term_years": "Desired lease term length or option preference.",
    "req_must_haves": "Must-have features (comma-separated).",
    "req_access_needs": "Access needs: ground floor, elevator, parking, ADA, etc.",
    "req_buildout_willingness": "What the tenant said about buildout//build-to-suit willingness.",
    "req_ti_expectation": "Tenant-improvement allowance expectations, as stated.",
    "req_timing": "When they need space or will decide, as stated.",
    "req_space_type": "Type of space (office, medical, shared workplace, retail...).",
    "expiration_date": "Current lease expiration date, if the tenant stated one.",
    "contact_name": "Name of the tenant-side contact person.",
    "contact_email": "Email address of the tenant-side contact.",
}

# Fields cleared automatically instead of queueing for human review.
# These are low-risk: a submarket or space type is either stated in the note or
# it isn't, they're cheap to spot-check later, and a wrong one is harmless
# compared with a wrong SF range, budget, or lease date. Everything else stays
# manual. Auto-approved rows are stamped verified_by="auto" so they remain
# distinguishable from facts Jack actually confirmed.
AUTO_APPROVE_FIELDS = {"req_submarkets", "req_space_type"}

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "null"],
                  "description": "The stated value, or null if the note does not state it."},
        "confidence": {"type": ["number", "null"], "description": "0.0-1.0, null if value is null."},
        "snippet": {"type": ["string", "null"],
                    "description": "Verbatim quote from the note, or null."},
    },
    "required": ["value", "confidence", "snippet"],
    "additionalProperties": False,
}

_TOOL = {
    "name": "record_tenant_facts",
    "description": "Record tenant requirements stated in a broker's activity note.",
    "input_schema": {
        "type": "object",
        "properties": {f: _FIELD_SCHEMA for f in REQUIREMENT_FIELDS},
        "required": list(REQUIREMENT_FIELDS),
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You extract commercial-real-estate tenant requirements from a broker's "
    "shorthand activity notes. The notes are messy, abbreviated, and written for "
    "the broker's own memory.\n"
    "CRITICAL RULE: Record ONLY what the note explicitly states. If a field is "
    "not stated, return null for it. Never infer, estimate, calculate, or guess "
    "— a missing value is normal and expected.\n"
    "Only record a requirement if it describes what the TENANT wants or said. Do "
    "not record the broker's own actions, opinions, or internal reminders as "
    "tenant requirements.\n"
    "For each field you find, give a confidence 0-1 and a short verbatim snippet "
    "quoting the note. For null values, set confidence and snippet to null."
)


def build_log_text(log: ActivityLog) -> str:
    """Assemble the readable text of an activity log (read-only)."""
    parts = [
        f"Type: {log.action_type}",
        f"Date: {log.log_date}",
    ]
    if log.subject:
        parts.append(f"Subject: {log.subject}")
    if log.action_taken:
        parts.append(f"Action taken: {log.action_taken}")
    if log.outcome:
        parts.append(f"Outcome: {log.outcome}")
    if log.notes:
        parts.append(f"Notes: {log.notes}")
    if log.follow_up_action:
        parts.append(f"Follow-up: {log.follow_up_action}")
    return "\n".join(parts)


def _extract_facts_via_llm(text: str, client=None) -> Dict[str, Dict[str, object]]:
    """Call Anthropic with structured output; return {field: {value, confidence, snippet}}."""
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add it to backend/.env or your "
                "environment before mining activity logs."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

    guide = "\n".join(f"- {f}: {d}" for f, d in REQUIREMENT_FIELDS.items())
    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_tenant_facts"},
        messages=[{
            "role": "user",
            "content": (
                f"Fields:\n{guide}\n\n"
                "Extract the tenant facts stated in this broker note. Return null "
                "for anything not explicitly stated.\n\n"
                f"--- BROKER NOTE ---\n{text}"
            ),
        }],
    )

    tool_input = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_tenant_facts":
            tool_input = block.input
            break
    if tool_input is None:
        raise RuntimeError("Model did not return structured tenant facts.")
    return _normalize(tool_input)


def _normalize(raw: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """Blank/"null"/"none" strings become real None so nothing fabricated is stored."""
    out: Dict[str, Dict[str, object]] = {}
    for field in REQUIREMENT_FIELDS:
        entry = raw.get(field) or {}
        if not isinstance(entry, dict):
            entry = {}
        value = entry.get("value")
        if isinstance(value, str):
            s = value.strip()
            value = s if s and s.lower() not in ("null", "none", "n/a", "not stated") else None
        out[field] = {
            "value": value,
            "confidence": entry.get("confidence") if value is not None else None,
            "snippet": entry.get("snippet") if value is not None else None,
        }
    return out


def mine_activity_log(
    log: ActivityLog,
    db: Session,
    extractor: Optional[Callable[[str], Dict[str, Dict[str, object]]]] = None,
) -> List[Observation]:
    """Mine one activity log into observations. Never modifies the log itself.

    Only fields the note actually states are written — a null field records
    nothing, so the Review queue isn't flooded with empty rows.
    """
    fn = extractor or _extract_facts_via_llm
    parsed = fn(build_log_text(log))

    # Attach to the company when the log is linked to one, so facts enrich the
    # company record; otherwise keep them addressable by the log itself.
    if log.company_id:
        entity_type, entity_id = "company", log.company_id
    else:
        entity_type, entity_id = "activity_log", log.id

    created: List[Observation] = []
    for field in REQUIREMENT_FIELDS:
        row = parsed.get(field) or {}
        value = row.get("value")
        if value is None:
            continue
        auto = field in AUTO_APPROVE_FIELDS
        obs = Observation(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            value=str(value),
            confidence=row.get("confidence"),
            source_doc=f"activity_log:{log.id}",
            source_page=None,
            source_snippet=row.get("snippet"),
            human_verified=auto,
            verified_by="auto" if auto else None,
        )
        db.add(obs)
        created.append(obs)
    return created


def auto_approve_existing(db: Session) -> Dict[str, int]:
    """Clear already-queued facts in the auto-approve fields.

    Only flips the verification flag — the value, snippet, confidence, and
    provenance are untouched, so there is nothing to supersede. Rows already
    verified (by a human or a previous run) are left alone.
    """
    rows = (
        db.query(Observation)
        .filter(
            Observation.field.in_(sorted(AUTO_APPROVE_FIELDS)),
            Observation.human_verified.is_(False),
            Observation.superseded_by_id.is_(None),
        )
        .all()
    )
    by_field: Dict[str, int] = {}
    for obs in rows:
        obs.human_verified = True
        obs.verified_by = "auto"
        by_field[obs.field] = by_field.get(obs.field, 0) + 1
    db.commit()
    return {"approved": len(rows), **by_field}


def mine_all_activity_logs(
    db: Session,
    limit: Optional[int] = None,
    force: bool = False,
    extractor: Optional[Callable[[str], Dict[str, Dict[str, object]]]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """Mine every not-yet-processed activity log. Idempotent by default.

    Returns counts: {processed, facts, skipped, failed}.
    """
    # Only logs that actually succeeded are "done". Failed ones (e.g. a transient
    # API error or an exhausted credit balance) must be retried on the next run,
    # otherwise a temporary outage would permanently skip them.
    done_ids = set()
    if not force:
        done_ids = {
            row.activity_log_id
            for row in db.query(IntelActivityExtraction.activity_log_id)
            .filter(IntelActivityExtraction.status != "failed")
            .all()
        }

    query = db.query(ActivityLog).order_by(ActivityLog.id.asc())
    logs = [l for l in query.all() if l.id not in done_ids]
    skipped = len(done_ids)
    if limit is not None:
        logs = logs[:limit]

    processed = facts = failed = 0
    total = len(logs)
    for idx, log in enumerate(logs, start=1):
        try:
            created = mine_activity_log(log, db, extractor=extractor)
            # Clear any earlier failed attempt so counts reflect reality.
            db.query(IntelActivityExtraction).filter(
                IntelActivityExtraction.activity_log_id == log.id,
                IntelActivityExtraction.status == "failed",
            ).delete(synchronize_session=False)
            db.add(IntelActivityExtraction(
                activity_log_id=log.id,
                status="done" if created else "empty",
                fields_found=len(created),
            ))
            db.commit()
            processed += 1
            facts += len(created)
        except MissingAPIKeyError:
            db.rollback()
            raise
        except Exception as exc:  # one bad note must not abort the batch
            db.rollback()
            db.add(IntelActivityExtraction(
                activity_log_id=log.id, status="failed", fields_found=0, error=str(exc)[:500],
            ))
            db.commit()
            failed += 1
        if progress:
            progress(idx, total)

    return {"processed": processed, "facts": facts, "skipped": skipped, "failed": failed}
