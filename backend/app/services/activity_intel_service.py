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

And one about whose facts they are. Jack's notes are not all conversations with
tenants — a lot of what he learns comes from brokers and landlords. The
extractor is told who the person on the entry is, and says who each
requirement belongs to (the entry's company, a tenant the note names, or a
client it does not). Nothing a broker said is thrown away, and none of it is
filed as if the brokerage were the tenant: see services/requirement_subject.py.
"""

import os
from typing import Callable, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, object_session

from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.models.observation import Observation
from app.services.document_extraction_service import (
    EXTRACTION_MODEL,
    MissingAPIKeyError,
)
from app.services.requirement_subject import (
    ABOUT_DISMISSED,
    ABOUT_ENTRY,
    ABOUT_MARKET,
    REQUIREMENT_KINDS,
    entry_company,
    subject_for,
)

# Rows that carry no conversation of their own. Kept in step with
# contact_service.STAGE_CHANGE_ACTION — not imported, because that module pulls
# in the email-ingest layer and this one must stay importable on its own.
STAGE_CHANGE_ACTION = "STAGE_CHANGE"

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

# What a broker or landlord said about space and the market. Stored against the
# person who said it (about="market") and shown on their thread. Deliberately
# inert: it drives no Intel card, no scoring, and never reaches outreach copy —
# a broker's asking rent is an unverified figure, and the property side is
# dormant. It is kept so nothing Jack is told is lost.
MARKET_FIELDS: Dict[str, str] = {
    "mkt_property": "A building, address or suite the note is about (as written).",
    "mkt_available_sf": "Space available at that property, as stated (SF or range).",
    "mkt_asking_rent": "Asking rent quoted for that space, as stated.",
    "mkt_concessions": "Concessions or landlord terms stated (free rent, TI offered, etc.).",
    "mkt_notes": "Anything else a broker or landlord said about the space or market.",
}

# Everything the extractor is asked for.
EXTRACTED_FIELDS: Dict[str, str] = {**REQUIREMENT_FIELDS, **MARKET_FIELDS}

# Fields cleared automatically instead of queueing for human review.
# These are basic facts read straight out of Jack's own notes — contact details,
# stated SF, submarket, timing — so they are cheap to spot-check and low-stakes
# if wrong. Currently every mined field auto-approves; pull a field name out of
# this set to send it back to manual review.
#
# IMPORTANT: this applies only to facts mined from activity notes. Facts
# extracted from lease PDFs always go to the Review queue, even where the field
# name overlaps (e.g. expiration_date) — a lease abstract is a legal document,
# not a call note.
AUTO_APPROVE_FIELDS = set(REQUIREMENT_FIELDS) | set(MARKET_FIELDS)

# Fields holding a date. A date is what decides WHO gets called and WHEN, so it
# only clears itself when the note stated it exactly ("lease ends 2027-03-01").
# Anything the tenant hedged ("~February 2027", "end of next year") is a
# suggestion, not a fact, and goes to Review with a normalized date to confirm.
DATE_FIELDS = {"expiration_date"}


def _should_auto_approve(field: str, value: Optional[str]) -> bool:
    """Whether a mined fact can clear itself instead of queueing for review."""
    if field not in AUTO_APPROVE_FIELDS:
        return False
    if field in DATE_FIELDS:
        # Imported here: the signal engine owns date parsing, and importing at
        # module scope would make the two services import each other.
        from app.services.intel_signal_service import parse_expiry

        return parse_expiry(value).precision == "exact"
    return True

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

_REQUIREMENT_FOR_SCHEMA = {
    "type": "object",
    "description": "Whose requirement the req_*, expiration and contact fields describe.",
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(REQUIREMENT_KINDS),
            "description": (
                "entry_company: the company this entry is filed under is the "
                "tenant. named_tenant: a tenant the note names (give tenant_name). "
                "unnamed_client: a tenant the note refers to without naming it "
                "('a tenant', 'my client')."
            ),
        },
        "tenant_name": {
            "type": ["string", "null"],
            "description": "The tenant's name as the note gives it, for named_tenant.",
        },
    },
    "required": ["kind", "tenant_name"],
    "additionalProperties": False,
}

_TOOL = {
    "name": "record_tenant_facts",
    "description": (
        "Record tenant requirements and market information stated in a "
        "broker's activity note."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "requirement_for": _REQUIREMENT_FOR_SCHEMA,
            **{f: _FIELD_SCHEMA for f in EXTRACTED_FIELDS},
        },
        "required": ["requirement_for", *EXTRACTED_FIELDS],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You extract commercial-real-estate facts from the shorthand activity notes "
    "of Jack Zamer, a TENANT-REPRESENTATION broker in Northern Virginia. The "
    "notes are messy, abbreviated, and written for his own memory. Each note "
    "comes with context: who the other person is and which side of the table "
    "they sit on.\n"
    "CRITICAL RULE: Record ONLY what the note explicitly states. If a field is "
    "not stated, return null for it. Never infer, estimate, calculate, or guess "
    "— a missing value is normal and expected.\n"
    "The req_* fields, expiration_date and contact_* fields describe a TENANT: "
    "what a tenant wants or said. Do not record Jack's own actions, opinions or "
    "reminders as requirements.\n"
    "Say whose requirement it is in requirement_for. A COUNTERPARTY (landlord "
    "broker, landlord, property manager) is never the tenant: when Jack writes "
    "to one about 'a tenant seeking 500-600 sqft', that is his client's "
    "requirement — unnamed_client, or named_tenant if the note names the "
    "client. A roundup entry about one deal is filed under that deal's company, "
    "so there the entry's company is the tenant.\n"
    "The mkt_* fields hold what a broker or landlord said about SPACE and the "
    "MARKET: a building, available space, asking rent, concessions. Never put a "
    "tenant's requirement in mkt_* fields, or market information in req_* "
    "fields.\n"
    "For each field you find, give a confidence 0-1 and a short verbatim snippet "
    "quoting the note. For null values, set confidence and snippet to null."
)

_SIDE_LABEL = {
    "tenant": "TENANT",
    "counterparty": "COUNTERPARTY (landlord broker, landlord or property manager)",
    "unconfirmed": "UNCONFIRMED (not yet known whether tenant or counterparty)",
    "owner": "OWNER",
}


def build_log_text(log: ActivityLog) -> str:
    """Assemble the readable text of an activity log (read-only).

    Context first: who the entry is with and which side they sit on, which way
    the email went, the note's date (so "in 8 months" resolves against when it
    was said, not when it is read), and where a roundup entry came from.
    """
    parts = [
        f"Type: {log.action_type}",
        f"Date: {log.log_date}",
    ]
    if log.direction:
        parts.append(
            "Direction: " + (
                "inbound (they wrote to Jack)" if log.direction == "inbound"
                else "outbound (Jack wrote to them)"
            )
        )
    contact = log.contact
    if contact is not None:
        side = _SIDE_LABEL.get(contact.contact_type or "", contact.contact_type or "unknown")
        works_at = f" at {contact.company.name}" if contact.company is not None else ""
        parts.append(f"Person on this entry: {contact.name}{works_at} — {side}")
    company = entry_company(log)
    if company is not None:
        parts.append(f"Entry is filed under company: {company.name}")
    if log.source_note:
        parts.append(f"Source: {log.source_note}")
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

    # What the email check recorded from the FULL email. The entry's own text
    # is a one-line summary; the email check read the whole message, signature
    # and all, and wrote down facts ("Fatimah Wilson is representing One Life
    # One Love, looking for 2,000-2,500 SF by December") and discovery values.
    # Reading them here is how Intel sees what the email said, rather than
    # only what the summary kept.
    discovery = [
        (label, getattr(log, col)) for col, label in _DISCOVERY_LABELS.items()
        if getattr(log, col, None) not in (None, "")
    ]
    if discovery:
        parts.append("Recorded from the email: " + "; ".join(
            f"{label}: {value}" for label, value in discovery
        ))
    for text in _entry_fact_texts(log):
        parts.append(f"Fact recorded from the email: {text}")
    return "\n".join(parts)


# ActivityLog discovery columns, as the extractor reads them.
_DISCOVERY_LABELS = {
    "disc_current_sf": "current SF",
    "disc_current_rent_psf": "current rent $/SF",
    "disc_lease_expiry": "current lease expiry",
    "disc_decision_timeline": "decision timeline",
    "disc_buildout_needs": "buildout needs",
    "disc_decision_maker": "decision maker",
}


def _entry_fact_texts(log: ActivityLog) -> List[str]:
    """Active contact facts the email check wrote from this entry. Read
    through the entry's own session; a log with none attached has none."""
    session = object_session(log)
    if session is None or log.id is None:
        return []
    from app.models.contact import ContactFact

    return [
        text for (text,) in
        session.query(ContactFact.fact_text)
        .filter(ContactFact.source_entry_id == log.id, ContactFact.is_active.is_(True))
        .order_by(ContactFact.id.asc())
        .all()
        if text
    ]


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

    guide = "\n".join(f"- {f}: {d}" for f, d in EXTRACTED_FIELDS.items())
    response = client.messages.create(
        model=EXTRACTION_MODEL,
        # 19 fields plus requirement_for; 1500 was sized for 14.
        max_tokens=2500,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_tenant_facts"},
        messages=[{
            "role": "user",
            "content": (
                f"Fields:\n{guide}\n\n"
                "Extract the tenant facts and market information stated in this "
                "broker note, and say whose requirement it is. Return null for "
                "anything not explicitly stated.\n\n"
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
    """Blank/"null"/"none" strings become real None so nothing fabricated is stored.

    requirement_for comes back under its own key, as {"kind", "tenant_name"}.
    """
    out: Dict[str, Dict[str, object]] = {}
    subject = raw.get("requirement_for")
    if isinstance(subject, dict):
        out["requirement_for"] = {
            "kind": subject.get("kind"),
            "tenant_name": subject.get("tenant_name"),
        }
    for field in EXTRACTED_FIELDS:
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


def read_entry(log: ActivityLog, extractor=None) -> Dict[str, Dict[str, object]]:
    """Ask the model what an entry states. Reads only — writes nothing.

    Kept apart from the writes on purpose: SQLite allows one writer at a time,
    and a model call takes seconds. Called while a write is open, it held the
    whole database for that long, and every other save — Jack attaching a
    requirement, the evening email check — failed with "database is locked".
    """
    fn = extractor or _extract_facts_via_llm
    return fn(build_log_text(log))


def mine_activity_log(
    log: ActivityLog,
    db: Session,
    extractor: Optional[Callable[[str], Dict[str, Dict[str, object]]]] = None,
    *,
    parsed: Optional[Dict[str, Dict[str, object]]] = None,
) -> List[Observation]:
    """Mine one activity log into observations. Never modifies the log itself.

    Only fields the note actually states are written — a null field records
    nothing, so the Review queue isn't flooded with empty rows. Pass `parsed`
    (from read_entry) to keep the model call outside any open write.
    """
    if parsed is None:
        parsed = read_entry(log, extractor)

    # Attach to the company when the log is linked to one, so facts enrich the
    # company record; otherwise keep them addressable by the log itself. Where
    # a fact finally belongs is decided when Intel runs — the entry's stamp at
    # that moment, or `about` below — so this is only where it is stored.
    if log.company_id:
        entity_type, entity_id = "company", log.company_id
    else:
        entity_type, entity_id = "activity_log", log.id

    # Whose requirement the note states. Absent (an extractor that predates it)
    # means the entry's company, exactly as before — and subject_for still
    # refuses to file it under a counterparty's own firm.
    subject = parsed.get("requirement_for") or {}
    kind = subject.get("kind")
    about, about_name = subject_for(
        log, kind if kind in REQUIREMENT_KINDS else None, subject.get("tenant_name"),
    )

    # What is already on file for this tenant, to catch a note that says
    # something different. Only for a requirement filed under the entry's own
    # company — a held or named one has no settled tenant to compare against.
    tenant = entry_company(log) if about == ABOUT_ENTRY else None
    on_file = _facts_on_file(db, tenant.id, log.id) if tenant is not None else {}

    # What Jack already decided about this entry's facts survives a re-read: a
    # fact he kept is not written twice, and anything new follows his answer.
    kept, answer = _jacks_answers(db, log.id)

    created: List[Observation] = []
    for field in EXTRACTED_FIELDS:
        row = parsed.get(field) or {}
        value = row.get("value")
        if value is None:
            continue
        is_market = field in MARKET_FIELDS
        if (field, _same(value)) in kept:
            continue
        auto = _should_auto_approve(field, str(value))
        earlier = on_file.get(field)
        contradicts = (
            earlier is not None and field in CONFLICT_FIELDS
            and _contradicts(field, earlier.value, str(value))
        )
        if contradicts:
            auto = False   # a contradiction is Jack's call, never automatic
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
            about=ABOUT_MARKET if is_market else about,
            about_name=None if is_market else about_name,
            conflicts_with_id=earlier.id if contradicts else None,
        )
        if answer is not None and not is_market:
            _follow_answer(obs, answer)
        db.add(obs)
        created.append(obs)
    return created


def _same(value) -> str:
    """Comparison form of a stated value: case and spacing do not matter."""
    return " ".join(str(value or "").casefold().split())


def _jacks_answers(db: Session, log_id: int):
    """Facts Jack kept on this entry, and his answer about whose they are.

    Returns ({(field, value) he kept}, the fact carrying his answer or None).
    His answer is an attachment (a company or a person) or a dismissal.
    """
    rows = (
        db.query(Observation)
        .filter(
            Observation.source_doc == f"activity_log:{log_id}",
            Observation.superseded_by_id.is_(None),
            Observation.verified_by == "human",
        )
        .all()
    )
    kept = {(o.field, _same(o.value)) for o in rows}
    answer = next(
        (o for o in rows
         if o.assigned_company_id or o.assigned_contact_id or o.about == ABOUT_DISMISSED),
        None,
    )
    return kept, answer


def _follow_answer(obs: Observation, answer: Observation) -> None:
    """A new fact from an entry Jack already answered goes where he said."""
    if answer.about == ABOUT_DISMISSED and not (
        answer.assigned_company_id or answer.assigned_contact_id
    ):
        obs.about = ABOUT_DISMISSED
        return
    obs.assigned_company_id = answer.assigned_company_id
    obs.assigned_contact_id = answer.assigned_contact_id


def reconcile_with_jacks_answers(db: Session) -> Dict[str, int]:
    """Repair facts re-read before re-reading respected Jack's answers.

    A copy of a fact he kept is superseded by the one he kept (history, not
    deleted); anything else new from that entry follows his answer. Idempotent.
    """
    answered = (
        db.query(Observation.source_doc)
        .filter(
            Observation.superseded_by_id.is_(None),
            Observation.verified_by == "human",
            or_(Observation.assigned_company_id.isnot(None),
                Observation.assigned_contact_id.isnot(None),
                Observation.about == ABOUT_DISMISSED),
            Observation.source_doc.like("activity_log:%"),
        )
        .distinct()
        .all()
    )
    copies = followed = 0
    for (source,) in answered:
        log_id = int(source.split(":", 1)[1])
        kept_rows = (
            db.query(Observation)
            .filter(Observation.source_doc == source, Observation.superseded_by_id.is_(None),
                    Observation.verified_by == "human")
            .all()
        )
        by_value = {(o.field, _same(o.value)): o for o in kept_rows}
        _, answer = _jacks_answers(db, log_id)
        for obs in (
            db.query(Observation)
            .filter(
                Observation.source_doc == source,
                Observation.superseded_by_id.is_(None),
                or_(Observation.verified_by.is_(None), Observation.verified_by != "human"),
                or_(Observation.about.is_(None), Observation.about != ABOUT_MARKET),
                Observation.assigned_company_id.is_(None),
                Observation.assigned_contact_id.is_(None),
            )
            .all()
        ):
            if obs.about == ABOUT_DISMISSED:
                continue
            twin = by_value.get((obs.field, _same(obs.value)))
            if twin is not None:
                obs.superseded_by_id = twin.id
                copies += 1
            elif answer is not None:
                _follow_answer(obs, answer)
                followed += 1
    db.commit()
    return {"copies_retired": copies, "followed_answer": followed}


# Fields where a different value means the tenant changed their story — a
# contradiction for Jack to settle. Lists like must-haves only ever add, so a
# new item is a new fact, not a conflict.
CONFLICT_FIELDS = {
    "req_sf_min", "req_sf_max", "req_budget_max_psf", "req_lease_term_years",
    "expiration_date",
}

# Two numbers this close are the same statement ("$38" and "38.00/SF").
_SAME_NUMBER = 0.02
# Two lease dates this close are the same date, pinned down.
_SAME_DATE_DAYS = 45


def _first_number(text: Optional[str]) -> Optional[float]:
    import re

    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _contradicts(field: str, old: Optional[str], new: Optional[str]) -> bool:
    """Whether two stated values disagree. Unreadable is never a contradiction:
    only values that can be compared, and differ, go to Review."""
    if field == "expiration_date":
        from app.services.intel_signal_service import parse_expiry

        a, b = parse_expiry(old).date, parse_expiry(new).date
        return bool(a and b) and abs((a - b).days) > _SAME_DATE_DAYS
    a, b = _first_number(old), _first_number(new)
    if a is None or b is None:
        return False
    return abs(a - b) > _SAME_NUMBER * max(abs(a), abs(b))


def _facts_on_file(db: Session, company_id: int, exclude_log_id: int) -> Dict[str, Observation]:
    """The newest settled fact per field for one tenant, from other notes.

    Settled: not superseded, not itself waiting on a contradiction, and filed
    under this company — by its note's company, or by Jack attaching it.
    """
    log_ids = [
        lid for (lid,) in
        db.query(ActivityLog.id)
        .filter(
            or_(ActivityLog.company_stamp_id == company_id,
                ActivityLog.company_id == company_id),
            ActivityLog.id != exclude_log_id,
        )
        .all()
    ]
    sources = [f"activity_log:{lid}" for lid in log_ids]
    conditions = [Observation.assigned_company_id == company_id]
    if sources:
        conditions.append(Observation.source_doc.in_(sources))
    rows = (
        db.query(Observation)
        .filter(
            or_(*conditions),
            Observation.superseded_by_id.is_(None),
            Observation.field.in_(sorted(CONFLICT_FIELDS)),
            or_(Observation.about.is_(None), Observation.about.in_(("entry", "named"))),
            or_(Observation.conflicts_with_id.is_(None), Observation.human_verified.is_(True)),
        )
        .order_by(Observation.id.asc())
        .all()
    )
    newest: Dict[str, Observation] = {}
    for obs in rows:
        newest[obs.field] = obs   # ascending id: the last one is the newest
    return newest


def remine_activity_log(
    log: ActivityLog,
    db: Session,
    extractor: Optional[Callable[[str], Dict[str, Dict[str, object]]]] = None,
) -> Dict[str, int]:
    """Re-extract a log after its text was edited, so the facts stay in sync.

    Machine-derived facts from the previous version are replaced — they were
    read off a version of the note that no longer exists, and leaving them would
    duplicate or contradict the new ones. **Facts a human verified or corrected
    are kept**: that is Jack's judgement, not a re-derivable projection. The
    activity log itself is never modified here.
    """
    # The model first, with nothing written yet — see read_entry.
    parsed = read_entry(log, extractor)

    source = f"activity_log:{log.id}"
    stale = (
        db.query(Observation)
        .filter(Observation.source_doc == source, Observation.verified_by != "human")
        .all()
    )
    kept = (
        db.query(Observation)
        .filter(Observation.source_doc == source, Observation.verified_by == "human")
        .count()
    )
    for obs in stale:
        db.delete(obs)

    db.query(IntelActivityExtraction).filter(
        IntelActivityExtraction.activity_log_id == log.id
    ).delete(synchronize_session=False)

    created = mine_activity_log(log, db, parsed=parsed)
    db.add(IntelActivityExtraction(
        activity_log_id=log.id,
        status="done" if created else "empty",
        fields_found=len(created),
    ))
    db.commit()
    return {"replaced": len(stale), "kept_human_verified": kept, "extracted": len(created)}


def purge_log_intel(db: Session, log_id: int) -> Dict[str, int]:
    """Remove everything the intelligence layer derived from one activity log.

    Called when the log is deleted: its extracted facts, the extraction record,
    and any signals/opportunities keyed to it are derived data with no meaning
    once the source is gone, so they go too. Does not commit — the caller
    deletes the log in the same transaction. Other entities are untouched.
    """
    from app.models.intel import IntelOpportunity, IntelSignal

    source = f"activity_log:{log_id}"
    obs = db.query(Observation).filter(Observation.source_doc == source).delete(
        synchronize_session=False)
    extr = db.query(IntelActivityExtraction).filter(
        IntelActivityExtraction.activity_log_id == log_id).delete(synchronize_session=False)
    opps = db.query(IntelOpportunity).filter(
        IntelOpportunity.entity_type == "activity_log",
        IntelOpportunity.entity_id == log_id).delete(synchronize_session=False)
    sigs = db.query(IntelSignal).filter(
        IntelSignal.entity_type == "activity_log",
        IntelSignal.entity_id == log_id).delete(synchronize_session=False)
    return {"observations": obs, "extractions": extr, "opportunities": opps, "signals": sigs}


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
            # Activity-note facts only. Lease-document facts keep going to
            # Review even when the field name is shared (expiration_date).
            Observation.source_doc.like("activity_log:%"),
        )
        .all()
    )
    by_field: Dict[str, int] = {}
    approved = 0
    for obs in rows:
        # A hedged date is not a fact — leave it queued for Jack to confirm.
        if not _should_auto_approve(obs.field, obs.value):
            continue
        obs.human_verified = True
        obs.verified_by = "auto"
        approved += 1
        by_field[obs.field] = by_field.get(obs.field, 0) + 1
    db.commit()
    return {"approved": approved, **by_field}


def requeue_fuzzy_dates(db: Session) -> Dict[str, int]:
    """Send auto-approved but imprecise dates back to the Review queue.

    Backfill for rows written before auto-approval became value-aware: ten real
    dates like "~February 2027" had cleared themselves, so nothing ever asked
    Jack to pin them down. Flips ONLY the verification flag — value, snippet,
    confidence and provenance are untouched, so there is nothing to supersede —
    and never touches a row a human verified. Idempotent.
    """
    from app.services.intel_signal_service import parse_expiry

    rows = (
        db.query(Observation)
        .filter(
            Observation.field.in_(sorted(DATE_FIELDS)),
            Observation.human_verified.is_(True),
            Observation.verified_by == "auto",
            Observation.superseded_by_id.is_(None),
            Observation.source_doc.like("activity_log:%"),
        )
        .all()
    )
    requeued = unreadable = 0
    for obs in rows:
        parsed = parse_expiry(obs.value)
        if parsed.precision == "exact":
            continue
        if not parsed:
            unreadable += 1
        obs.human_verified = False
        obs.verified_by = None
        requeued += 1
    db.commit()
    return {"requeued": requeued, "unreadable": unreadable, "checked": len(rows)}


def is_copy(log: ActivityLog) -> bool:
    """A row that repeats a conversation logged elsewhere, or is not one.

    A stage-change divider has no text of its own. One email writes a row per
    participant: the first carries the bare message id and is the one mined;
    every other direct recipient's row ("<id>#p12") and every Cc copy
    (participation) holds the same words. Mining those tripled the facts.
    """
    if log.action_type == STAGE_CHANGE_ACTION or log.participation:
        return True
    return "#p" in (log.source_message_id or "")


def is_mineable(log: ActivityLog) -> bool:
    """Whether a mining run should read this entry now.

    Archived entries wait rather than being marked done: unarchiving one puts
    it straight back in line.
    """
    return not log.archived and not is_copy(log)


def mineable_filters():
    """is_mineable() as SQL, for counts that must not load every row."""
    return (
        ActivityLog.archived.isnot(True),
        ActivityLog.participation.isnot(True),
        ActivityLog.action_type != STAGE_CHANGE_ACTION,
        or_(ActivityLog.source_message_id.is_(None),
            ~ActivityLog.source_message_id.contains("#p")),
    )


def _drop_machine_facts(db: Session, log_id: int) -> int:
    """Delete the machine-derived facts sourced to one entry. Facts Jack
    verified, corrected, attached or dismissed are his and are kept."""
    return (
        db.query(Observation)
        .filter(
            Observation.source_doc == f"activity_log:{log_id}",
            or_(Observation.verified_by.is_(None), Observation.verified_by != "human"),
        )
        .delete(synchronize_session=False)
    )


def clear_stale_failures(db: Session) -> int:
    """Drop failure records for entries that were later read successfully.

    Two reads of one entry can overlap (the button and the background reader);
    one can lose a lock and record a failure while the other succeeds. The
    failure then means nothing and only confuses the count. Does not commit.
    """
    read_ok = [
        lid for (lid,) in
        db.query(IntelActivityExtraction.activity_log_id)
        .filter(IntelActivityExtraction.status != "failed")
        .distinct()
        .all()
    ]
    if not read_ok:
        return 0
    removed = 0
    for i in range(0, len(read_ok), 500):
        removed += (
            db.query(IntelActivityExtraction)
            .filter(
                IntelActivityExtraction.status == "failed",
                IntelActivityExtraction.activity_log_id.in_(read_ok[i:i + 500]),
            )
            .delete(synchronize_session=False)
        )
    return removed


def sweep_copies(db: Session) -> Dict[str, int]:
    """Clear facts mined from copies before copies were skipped, and mark every
    copy as handled so it never counts as waiting.

    Idempotent. Facts Jack touched are kept.
    """
    done = {
        lid for (lid,) in
        db.query(IntelActivityExtraction.activity_log_id)
        .filter(IntelActivityExtraction.status == "skipped")
        .all()
    }
    purged = marked = 0
    for log in db.query(ActivityLog).all():
        if not is_copy(log) or log.id in done:
            continue
        purged += _drop_machine_facts(db, log.id)
        db.query(IntelActivityExtraction).filter(
            IntelActivityExtraction.activity_log_id == log.id,
        ).delete(synchronize_session=False)
        db.add(IntelActivityExtraction(activity_log_id=log.id, status="skipped", fields_found=0))
        marked += 1
    db.commit()
    return {"facts_purged": purged, "copies_marked": marked}


def queue_remine(db: Session, *, contact_ids=(), company_ids=()) -> int:
    """Put entries back in line for the next mining run.

    Called when Jack changes who someone is. Whose requirement a note states
    depends on whether the person on it is a tenant or a counterparty, so their
    entries are read again — the next run replaces the machine facts and keeps
    whatever Jack verified. Only the "already mined" marker is removed here; no
    API call happens until he presses Mine. Does not commit.
    """
    contact_ids, company_ids = list(contact_ids), list(company_ids)
    if not contact_ids and not company_ids:
        return 0
    conditions = []
    if contact_ids:
        conditions.append(ActivityLog.contact_id.in_(contact_ids))
    if company_ids:
        conditions.append(ActivityLog.company_stamp_id.in_(company_ids))
    log_ids = [lid for (lid,) in db.query(ActivityLog.id).filter(or_(*conditions)).all()]
    if not log_ids:
        return 0
    return (
        db.query(IntelActivityExtraction)
        .filter(
            IntelActivityExtraction.activity_log_id.in_(log_ids),
            IntelActivityExtraction.status != "skipped",
        )
        .delete(synchronize_session=False)
    )


def mine_all_activity_logs(
    db: Session,
    limit: Optional[int] = None,
    force: bool = False,
    extractor: Optional[Callable[[str], Dict[str, Dict[str, object]]]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """Mine every not-yet-processed activity log. Idempotent by default.

    Copies and dividers are never read (see is_copy); archived entries wait.
    An entry read before — queued again because its text changed or its
    person was reclassified — has its old machine facts replaced, not doubled.

    Returns counts: {processed, facts, skipped, failed}.
    """
    sweep_copies(db)
    clear_stale_failures(db)
    reconcile_with_jacks_answers(db)

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
    logs = [l for l in query.all() if l.id not in done_ids and is_mineable(l)]
    skipped = len(done_ids)
    if limit is not None:
        logs = logs[:limit]

    processed = facts = failed = 0
    total = len(logs)
    for idx, log in enumerate(logs, start=1):
        found = _mine_one(db, log, extractor)
        if found is None:
            failed += 1
        else:
            processed += 1
            facts += found
        if progress:
            progress(idx, total)

    return {"processed": processed, "facts": facts, "skipped": skipped, "failed": failed}


def _mine_one(db: Session, log: ActivityLog, extractor=None) -> Optional[int]:
    """Read one entry, replacing its old machine facts, and mark it done.

    Returns the number of facts written, or None when it failed — a failed
    entry is marked so the next run retries it. A missing API key is not a
    per-entry failure and is raised to the caller.
    """
    try:
        # The model first, with no write open; then every write in one short
        # transaction. See read_entry for why the order matters.
        db.commit()
        parsed = read_entry(log, extractor)
        _drop_machine_facts(db, log.id)
        created = mine_activity_log(log, db, parsed=parsed)
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
        return len(created)
    except MissingAPIKeyError:
        db.rollback()
        raise
    except Exception as exc:  # one bad note must not abort the batch
        db.rollback()
        try:
            db.add(IntelActivityExtraction(
                activity_log_id=log.id, status="failed", fields_found=0, error=str(exc)[:500],
            ))
            db.commit()
        except Exception:   # noqa: BLE001 — e.g. still locked: leave it unmarked
            db.rollback()   # unmarked means the next run simply tries it again
        return None


# ── Reading new entries as they arrive ───────────────────────────────────────
# Jack should not have to press Mine for every email the 7pm check logs. A new
# entry is read right after it is saved, in the background, so logging never
# waits on the model. If that fails — no credits, no network — the entry is
# simply left for the Mine button, exactly as before.
#
# DEAL_RADAR_AUTO_MINE=0 switches it off (the test suite does, so tests never
# reach the API). With no API key set it is off too.

def auto_mine_enabled() -> bool:
    flag = os.environ.get("DEAL_RADAR_AUTO_MINE", "1").strip().lower()
    return flag not in ("0", "false", "no", "off") and bool(os.environ.get("ANTHROPIC_API_KEY"))


def mine_new_entries(entry_ids, extractor=None, session_factory=None) -> Dict[str, int]:
    """Read freshly logged entries. Runs after the response, in its own session."""
    if session_factory is None:
        from app.database import SessionLocal as session_factory
    db = session_factory()
    done = failed = 0
    try:
        for lid in entry_ids:
            log = db.query(ActivityLog).filter(ActivityLog.id == lid).first()
            if log is None or not is_mineable(log):
                continue
            already = (
                db.query(IntelActivityExtraction)
                .filter(IntelActivityExtraction.activity_log_id == lid,
                        IntelActivityExtraction.status != "failed")
                .first()
            )
            if already is not None:
                continue
            try:
                result = _mine_one(db, log, extractor)
            except MissingAPIKeyError:
                break   # nothing to do until a key is set; the button will say so
            if result is None:
                failed += 1
            else:
                done += 1
    except Exception as exc:   # never let a background read take anything down
        import logging
        logging.getLogger(__name__).warning("background mining stopped: %s", exc)
    finally:
        db.close()
    return {"mined": done, "failed": failed}


def schedule_mining(background_tasks, entry_ids) -> None:
    """Queue new entries to be read once the response has gone out."""
    ids = [lid for lid in entry_ids if lid]
    if ids and auto_mine_enabled():
        background_tasks.add_task(mine_new_entries, ids)
