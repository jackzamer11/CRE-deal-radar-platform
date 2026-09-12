"""Attach the pre-existing activity entries to contacts and companies.

Every entry written before contact threads existed has a null contact_id. This
resolves each one to a person and a company, so a thread that starts today
carries the history that came before it.

WHAT IT WILL AND WILL NOT TOUCH
-------------------------------
It sets, and only sets:  contact_id, company_stamp_id, source_message_id,
direction, responded — plus each resolved contact's stage.

It never rewrites action_taken, outcome, or an existing note. Jack's prose is
the record; the backfill only says who and which company it was about. The one
exception is surgical and deliberate: an ``[outlook:<messageId>]`` marker inside
`notes` moves to the indexed source_message_id column and is stripped from the
note text, leaving the rest of the note untouched. That marker sharing a
user-editable field is what caused old emails to relog.

RUN IT AGAINST A COPY FIRST
---------------------------
From backend/, with the venv active. Do these in order and read the report in
between — nothing here should meet the live database until it has been
rehearsed on the backup.

  1. Report against the backup, writes nothing:
       python -m scripts.backfill_contacts --db-path deal_radar.backup-20260911-174558.db

  2. Read backfill_report.json and the printed summary.

  3. Write against the backup:
       python -m scripts.backfill_contacts --db-path deal_radar.backup-20260911-174558.db --confirm

  4. Confirm it looks right (open the app pointed at the backup, or re-run
     report mode against it — a second report should find nothing left to do).

  5. Only then, against live:
       python -m scripts.backfill_contacts                 # report
       python -m scripts.backfill_contacts --confirm       # write

RESOLUTION, IN ORDER
--------------------
  1. An email address in the prose wins absolutely. Exact, case-insensitive
     match against an existing contact, or a new contact keyed to that address.
     High confidence, no model judgment involved.
  2. Entries with no address go to the model in batches, which reads the prose
     and returns a name, a company and a confidence. Low confidence is the
     correct answer when it is unsure — it is told never to guess.
  3. An extracted name is matched against the contacts already resolved by
     address before anything new is created, so "Miriam" and "Miriam Miller"
     land on one record.
  4. Two similar names are never merged. They are reported as a suspected
     duplicate for Jack to look at.

Company resolution follows the same rules as the from-email path: domain match
first, then a loose name match against existing companies, and free-mail domains
(gmail, outlook, yahoo, icloud, aol, proton and the rest) create no company at
all. Where no company resolves, company_stamp_id stays null — none is invented.

Write mode applies high-confidence links only, is idempotent (a second run
changes nothing), and requires --confirm. Neither mode runs on startup.

Without ANTHROPIC_API_KEY the script runs address-matching only and says so
plainly at the top and bottom of the report. It does not crash, and it does not
pretend the model step happened.
"""
import argparse
import json
import os
import re
import sys
from collections import namedtuple
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._dbpath import (  # noqa: E402
    load_env, make_session, require_contact_schema, resolve_db_path,
)

REPORT_PATH = Path(__file__).resolve().parent / "backfill_report.json"

# Entries per model call. Small enough that one failure costs little, large
# enough that 300-odd entries is ~16 calls rather than 300.
BATCH_SIZE = 20

EXTRACTION_MODEL = "claude-sonnet-4-6"

# A plain address, anywhere in the prose. Deliberately the first thing tried:
# an address is an identity, and no model judgment can improve on it.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# The dedup marker the old email automation wrote into `notes`.
OUTLOOK_MARKER_RE = re.compile(r"\s*\[outlook:\s*([^\]]+?)\s*\]\s*")

# Prose that means they came to Jack rather than the other way round.
INBOUND_RE = re.compile(
    r"\b(replied|reply|responded|response from|received (?:an? )?(?:email|call|voicemail)"
    r"|inbound|got (?:an? )?(?:email|call)|reached out to (?:me|us)"
    r"|called (?:me|us) back|emailed (?:me|us)|inquiry from|came back)\b",
    re.IGNORECASE,
)

# Two proposed names this alike are flagged for review. Never merged.
DUPLICATE_RATIO = 0.86

# The log lines these read, as they actually appear:
#   "Emailed Gabriel Sloane (gsloane@cianbro.com), LoopNet inquiry on ..."
#   "Emailed Laura Pagliarulo re SolaREIT"
#   "Called Nina Karnik, Sonina Properties re SolaREIT"
#   "Emailed listing agent Bobby Hornsby about the space"
#
# Anchored at the start of the line and using horizontal whitespace only
# ([ \t] rather than \s), so a match can never run past the end of the line and
# swallow the outcome or the notes underneath it.
VERB_RE = re.compile(
    r"^[ \t]*(?:emailed|called|e-mailed|messaged|texted|met(?:[ \t]+with)?)[ \t]+",
    re.IGNORECASE,
)

# The name runs up to the first delimiter: a bracket, a comma, or "re"/"at".
# Lazy, so "Keith Brown, REACS, Inc." yields Keith Brown.
NAME_TO_DELIMITER_RE = re.compile(
    r"^([A-Za-z][\w'\-.]*(?:[ \t]+[A-Za-z][\w'\-.]*){0,3}?)"
    r"(?=[ \t]*(?:[(,;:]|\bre\b|\bat\b|\bfor\b|\bon\b|$))",
)

# Fallback for a name followed by ordinary prose rather than a delimiter —
# "Bobby Hornsby about the space". Capitals are what separates the name from the
# sentence that follows it, so this one is deliberately case-sensitive.
NAME_CAPITALISED_RE = re.compile(
    r"^([A-Z][\w'\-.]*(?:[ \t]+[A-Z][\w'\-.]*){1,3})",
)

# Placeholders the old outreach tooling wrote where it had no name. None of
# these is a person, so none of them becomes a contact name.
NAME_PLACEHOLDERS = {
    "contact", "unknown", "team", "info", "admin", "the team", "n a", "na",
    "someone", "them", "him", "her", "back", "and emailed", "and called",
}

# Mailboxes that belong to a function rather than a person. Deriving a name
# from one gives two unrelated companies a contact called "Admin", which then
# reads as a duplicate pair. The address itself is the honest name for these —
# it says "a mailbox at this company", not "a person called Admin".
ROLE_MAILBOXES = {
    "admin", "administrator", "info", "information", "contact", "contactus",
    "hello", "hi", "office", "mail", "email", "enquiries", "inquiries",
    "accounting", "accounts", "accountspayable", "ap", "ar", "billing",
    "finance", "payroll", "hr", "jobs", "careers", "recruiting",
    "sales", "support", "help", "helpdesk", "service", "customerservice",
    "team", "staff", "reception", "frontdesk", "leasing", "rentals",
    "property", "management", "marketing", "press", "media", "webmaster",
    "noreply", "no-reply", "donotreply", "success", "general", "main",
}


# A role in front of the name — "Emailed listing agent Bobby Hornsby" — is not
# part of what the person is called.
ROLE_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:listing\s+agent|leasing\s+agent|selling\s+agent|agent"
    r"|listing\s+broker|broker|landlord|owner|tenant\s+rep|property\s+manager"
    r"|pm|ceo|cfo|coo|president|principal|partner|director|manager)\s+",
    re.IGNORECASE,
)


# ── Small helpers ────────────────────────────────────────────────────────────

# The six columns the resolvers read. Report mode never loads a full ORM row:
# it reads these as plain tuples, so a batch costs a handful of strings rather
# than an ActivityLog instance per entry, and nothing it touches ends up in the
# session's identity map.
EntryRow = namedtuple(
    "EntryRow", "id action_taken outcome notes log_date stage",
)


def _load_batch(db, ids) -> List["EntryRow"]:
    """One batch of entries, as plain rows. Read-only by construction."""
    from app.models.activity import ActivityLog

    return [
        EntryRow(*row) for row in
        db.query(
            ActivityLog.id, ActivityLog.action_taken, ActivityLog.outcome,
            ActivityLog.notes, ActivityLog.log_date, ActivityLog.stage,
        ).filter(ActivityLog.id.in_(ids)).all()
    ]


def _text_of(entry) -> str:
    """The prose the resolvers read. Never written back."""
    return "\n".join(filter(None, [
        entry.action_taken or "",
        entry.outcome or "",
        entry.notes or "",
    ]))


def _norm_name(name: Optional[str]) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return re.sub(r"\s+", " ", n).strip()


def _is_inbound(text: str) -> bool:
    """Prose heuristic for the address path. Conservative on purpose: a false
    inbound flips a contact's stage, so anything ambiguous stays outbound."""
    if re.match(r"^\s*(emailed|called|sent|cold emailed|followed up)", text, re.IGNORECASE):
        return False
    return bool(INBOUND_RE.search(text))


def _name_from_prose(action_taken: Optional[str]) -> Optional[str]:
    """The person named at the start of a log line, or None.

    Reads action_taken only — never the concatenated prose, so a match cannot
    run off the end of the line and swallow the outcome. Returns None for the
    placeholders the old tooling wrote when it had no name ("Emailed contact",
    "Emailed Unknown re Amentum"), so those become no contact rather than a
    contact called "contact".
    """
    if not action_taken:
        return None
    verb = VERB_RE.match(action_taken)
    if not verb:
        return None

    # Drop a role sitting in front of the name — "listing agent Bobby Hornsby",
    # "the owner Jerald Meyer". A role is not what someone is called, and
    # leaving it in would make two records for one person later.
    rest = ROLE_PREFIX_RE.sub("", action_taken[verb.end():].lstrip())

    m = NAME_TO_DELIMITER_RE.match(rest) or NAME_CAPITALISED_RE.match(rest)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;:-")
    if not name or _norm_name(name) in NAME_PLACEHOLDERS:
        return None
    # A name does not start lowercase. "Emailed CEO of DataCoreAI" leaves "of
    # DataCoreAI" once the role is stripped — a sentence fragment, not a person.
    if name[0].islower():
        return None
    return name


def _name_from_email(email: Optional[str]) -> Optional[str]:
    """Derive a display name from the local part, as from-email already does:
    "joe.smith@acme.com" -> "Joe Smith".

    A role mailbox keeps its full address instead. "admin@clinic.com" is a
    mailbox, not somebody called Admin, and naming two of them "Admin" would
    both lose the information and manufacture a duplicate pair.
    """
    if not email or "@" not in email:
        return None
    local = email.split("@")[0]
    if local.lower().replace(".", "").replace("_", "").replace("-", "") in ROLE_MAILBOXES:
        return email
    words = local.replace(".", " ").replace("_", " ").replace("-", " ").split()
    derived = " ".join(w.capitalize() for w in words if w)
    return derived or None


def _contact_key(email: Optional[str], name: Optional[str]) -> Optional[str]:
    """Stable identity for a proposed contact across report and write mode."""
    if email:
        return f"email:{email.strip().lower()}"
    n = _norm_name(name)
    return f"name:{n}" if n else None


# ── Resolution ───────────────────────────────────────────────────────────────

def _resolve_by_address(db, entry) -> Optional[dict]:
    """Address path. An address in the prose is an identity, full stop."""
    from app.services.contact_service import normalize_email, resolve_contact_by_email

    text = _text_of(entry)
    # An [outlook:<AAA@host>] marker looks exactly like an address. Strip it
    # before scanning, or the dedup marker resolves as the counterpart and the
    # entry attaches to a contact invented out of a message id.
    text = OUTLOOK_MARKER_RE.sub(" ", text)
    found = EMAIL_RE.findall(text)
    if not found:
        return None

    email = normalize_email(found[0])
    if not email:
        return None

    existing = resolve_contact_by_email(db, email)
    # A name in front of the address — "Gabriel Sloane (gsloane@cianbro.com)" —
    # names a new contact, but is never used to MATCH one: two people called
    # Mike Johnson are two people.
    name = _name_from_prose(entry.action_taken) or _name_from_email(email)

    return {
        "entry_id": entry.id,
        "source": "address",
        "confidence": "high",
        "email": email,
        "contact_name": (existing.name if existing else name),
        "existing_contact_id": existing.id if existing else None,
        "company_name": None,
        "inbound": _is_inbound(text),
        "log_date": entry.log_date.isoformat() if entry.log_date else None,
        "stage": entry.stage or "Sent",
    }


_TOOL = {
    "name": "record_entry_attributions",
    "description": "Who each broker log entry was about, one result per entry.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "integer"},
                        "contact_name": {
                            "type": ["string", "null"],
                            "description": "The person's name exactly as written. Null if no person is named.",
                        },
                        "company_name": {
                            "type": ["string", "null"],
                            "description": "The company the conversation was about. Null if none is named.",
                        },
                        "email": {
                            "type": ["string", "null"],
                            "description": "An email address if one appears. Null otherwise.",
                        },
                        "inbound": {
                            "type": "boolean",
                            "description": "True only if this clearly records them contacting Jack.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "low"],
                            "description": "high only when the person is named unambiguously.",
                        },
                    },
                    "required": ["entry_id", "contact_name", "company_name",
                                 "email", "inbound", "confidence"],
                },
            },
        },
        "required": ["results"],
    },
}

_SYSTEM = """You read a commercial real-estate broker's own activity log and say who each entry was about.

Rules, in order of importance:

1. LOW CONFIDENCE IS THE CORRECT ANSWER WHEN YOU ARE UNSURE. It is not a
   failure. An entry marked low is reviewed by a human; an entry you guessed
   wrong silently attaches a conversation to the wrong person and is worse than
   no answer at all.
2. NEVER GUESS TO FORCE A RESOLUTION. If no person is named, return
   contact_name: null and confidence: low. Do not infer a name from a company,
   a job title, a property address, or a role such as "CEO" or "the owner".
3. Extract only what is written. Do not correct spellings, expand initials, or
   complete a partial name into a fuller one you think you recognise.
4. contact_name is high confidence only when an actual person is named
   unambiguously — "Emailed Keith Brown re D.R. Horton" is high for Keith
   Brown. "Emailed contact", "CannonDesign outreach", "cold emailed CEO of
   DataCoreAI" and "Emailed Unknown re Amentum" all name no person: null, low.
5. company_name is the company the conversation was about. In "Emailed Keith
   Brown, REACS, Inc. re D.R. Horton - Northern Virginia" the subject company
   is D.R. Horton - Northern Virginia and Keith Brown's own firm is REACS, Inc.
   — prefer the company the conversation is about (the "re" clause).
6. inbound is true only when the entry clearly records them contacting Jack —
   a reply, a returned call, an inbound inquiry. Jack emailing or calling out
   is not inbound. If unclear, false.

Return exactly one result per entry id given, using the same ids."""


def _extract_batch(entries, client) -> List[dict]:
    """One model call for up to BATCH_SIZE entries. Raises on failure."""
    payload = [
        {
            "entry_id": e.id,
            "date": e.log_date.isoformat() if e.log_date else None,
            "action_taken": e.action_taken or "",
            "outcome": e.outcome or "",
            "notes": e.notes or "",
        }
        for e in entries
    ]
    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=4000,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_entry_attributions"},
        messages=[{
            "role": "user",
            "content": (
                "Attribute each of these broker log entries. Return one result "
                "per entry id, and mark anything you are unsure of as low.\n\n"
                + json.dumps(payload, indent=1)
            ),
        }],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL["name"]:
            return block.input.get("results", []) or []
    raise RuntimeError("Model returned no structured attributions.")


def _from_model(entry, result: dict, by_name: Dict[str, dict]) -> dict:
    """Turn one model result into a proposal, matching known names first."""
    from app.services.contact_service import normalize_email

    name = (result.get("contact_name") or "").strip() or None
    if name and _norm_name(name) in NAME_PLACEHOLDERS:
        name = None
    email = normalize_email(result.get("email")) if result.get("email") else None
    if not name and email:
        name = _name_from_email(email)
    confidence = "high" if result.get("confidence") == "high" else "low"
    if not name and not email:
        confidence = "low"

    # Rule 3: an extracted name that matches someone already pinned down by
    # their address is that person — "Miriam" and "Miriam Miller" are one
    # record, not two. Exact normalized match only; similarity never merges.
    existing_contact_id = None
    if name and not email:
        known = by_name.get(_norm_name(name))
        if known:
            email = known.get("email")
            existing_contact_id = known.get("existing_contact_id")

    return {
        "entry_id": entry.id,
        "source": "model",
        "confidence": confidence,
        "email": email,
        "contact_name": name,
        "existing_contact_id": existing_contact_id,
        "company_name": (result.get("company_name") or "").strip() or None,
        "inbound": bool(result.get("inbound")),
        "log_date": entry.log_date.isoformat() if entry.log_date else None,
        "stage": entry.stage or "Sent",
    }


# ── Report mode ──────────────────────────────────────────────────────────────

def _outlook_marker(entry) -> Optional[dict]:
    """An [outlook:<id>] marker to migrate out of `notes`, if there is one."""
    if not entry.notes:
        return None
    m = OUTLOOK_MARKER_RE.search(entry.notes)
    if not m:
        return None
    message_id = m.group(1).strip()
    if not message_id:
        return None
    # Strip only the marker. Everything else in the note is left exactly as it
    # was, including spacing between the words either side of it.
    stripped = OUTLOOK_MARKER_RE.sub(" ", entry.notes).strip()
    return {"source_message_id": message_id, "notes": stripped or None}


def _unresolved(entry_id: int, source: str) -> dict:
    """An entry nothing could attribute. Reported as such — never dropped."""
    return {
        "entry_id": entry_id,
        "source": source,
        "confidence": "unresolved",
        "email": None, "contact_name": None, "existing_contact_id": None,
        "company_name": None, "inbound": False, "log_date": None,
        "stage": "Sent",
    }


def build_report(db, verbose: bool = True) -> dict:
    """Resolve every unattached entry. Writes nothing to the database."""
    from app.models.activity import ActivityLog

    entry_ids = [
        row[0] for row in
        db.query(ActivityLog.id)
        .filter(ActivityLog.contact_id.is_(None))
        .order_by(ActivityLog.id.asc())
        .all()
    ]

    client = None
    model_available = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if model_available:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Anthropic client unavailable ({exc}) — address matching only")
            model_available = False

    proposals: List[dict] = []
    markers: Dict[int, dict] = {}
    needs_model: List[int] = []
    by_name: Dict[str, dict] = {}
    model_error: Optional[str] = None

    # ── Pass 1: addresses. Batched so entry rows never all sit in memory.
    for start in range(0, len(entry_ids), BATCH_SIZE):
        chunk = entry_ids[start:start + BATCH_SIZE]
        for entry in _load_batch(db, chunk):
            marker = _outlook_marker(entry)
            if marker:
                markers[entry.id] = marker
            proposal = _resolve_by_address(db, entry)
            if proposal:
                proposals.append(proposal)
                if proposal["contact_name"]:
                    by_name.setdefault(_norm_name(proposal["contact_name"]), proposal)
            else:
                needs_model.append(entry.id)

    if verbose:
        print(f"  {len(proposals)} entries carry an email address")
        print(f"  {len(needs_model)} entries need the prose read")

    # ── Pass 2: the model, on what the addresses could not settle.
    if needs_model and model_available:
        done = 0
        for start in range(0, len(needs_model), BATCH_SIZE):
            chunk = needs_model[start:start + BATCH_SIZE]
            rows = _load_batch(db, chunk)
            by_id = {e.id: e for e in rows}
            try:
                results = _extract_batch(rows, client)
            except Exception as exc:  # noqa: BLE001
                # Keep what was resolved and stop cleanly. Nothing was written
                # to the database in this mode, and the partial report is
                # flagged so write mode refuses it.
                model_error = f"{type(exc).__name__}: {exc}"
                for entry_id in needs_model[start:]:
                    proposals.append(_unresolved(entry_id, "model-call-failed"))
                break
            answered = set()
            for result in results:
                entry = by_id.get(result.get("entry_id"))
                if entry is not None:
                    proposals.append(_from_model(entry, result, by_name))
                    answered.add(entry.id)
            # An entry the model returned nothing for is unresolved, not
            # missing. Anything that drops off the list here is invisible to
            # the person reviewing the report.
            for entry_id in chunk:
                if entry_id not in answered:
                    proposals.append(_unresolved(entry_id, "model-returned-nothing"))
            done += len(chunk)
            if verbose:
                print(f"  read {done}/{len(needs_model)} entries", end="\r")
        if verbose:
            print()
    elif needs_model:
        for entry_id in needs_model:
            proposals.append(_unresolved(entry_id, "skipped-no-api-key"))

    # ── Group into contacts, and resolve each one's company.
    contacts = _group_contacts(db, proposals)
    duplicates = _suspected_duplicates(contacts)
    covered = {p["entry_id"] for p in proposals}

    return {
        "generated_on": date.today().isoformat(),
        "model_available": model_available,
        "model_error": model_error,
        "partial": model_error is not None,
        "total_entries": len(entry_ids),
        "entries_seen": len(covered),
        "entries_missing": sorted(set(entry_ids) - covered),
        "proposals": proposals,
        "contacts": contacts,
        "markers": {str(k): v for k, v in markers.items()},
        "suspected_duplicates": duplicates,
    }


def _group_contacts(db, proposals: List[dict]) -> List[dict]:
    """One record per proposed contact, with its entries and its company."""
    from app.services.contact_service import (
        email_domain_of, is_free_mail, normalize_email,
    )
    from app.models.company import Company

    grouped: Dict[str, dict] = {}
    for p in proposals:
        if p["confidence"] == "unresolved":
            continue
        key = _contact_key(p.get("email"), p.get("contact_name"))
        if not key:
            p["confidence"] = "unresolved"
            continue
        p["contact_key"] = key
        row = grouped.setdefault(key, {
            "key": key,
            "name": p.get("contact_name"),
            "email": p.get("email"),
            "existing_contact_id": p.get("existing_contact_id"),
            "entry_ids": [],
            "high_confidence_entry_ids": [],
            "company_name": None,
            "company_id": None,
            "company_action": "none",
            "stage": "Sent",
            "latest_log_date": None,
            "responded": False,
        })
        if not row["name"] and p.get("contact_name"):
            row["name"] = p["contact_name"]
        if not row["email"] and p.get("email"):
            row["email"] = p["email"]
        if row["existing_contact_id"] is None and p.get("existing_contact_id"):
            row["existing_contact_id"] = p["existing_contact_id"]
        row["entry_ids"].append(p["entry_id"])
        if p["confidence"] == "high":
            row["high_confidence_entry_ids"].append(p["entry_id"])
        if p.get("inbound"):
            row["responded"] = True
        if not row["company_name"] and p.get("company_name"):
            row["company_name"] = p["company_name"]
        # Stage comes from the most recent entry that has one.
        if p.get("log_date") and (row["latest_log_date"] or "") <= p["log_date"]:
            row["latest_log_date"] = p["log_date"]
            row["stage"] = p.get("stage") or "Sent"

    # ── Company resolution: domain, then name, free-mail excluded entirely.
    companies = db.query(Company).filter(Company.name.isnot(None)).all()
    by_domain = {c.email_domain: c for c in companies if c.email_domain}

    for row in grouped.values():
        domain = email_domain_of(row["email"]) if row["email"] else None
        if domain and not is_free_mail(domain):
            hit = by_domain.get(domain)
            if hit:
                row.update(company_id=hit.id, company_name=hit.name,
                           company_action="matched-domain")
                continue
            site = next(
                (c for c in companies if c.website and domain in c.website.lower()),
                None,
            )
            if site:
                row.update(company_id=site.id, company_name=site.name,
                           company_action="matched-website")
                continue
            stem = domain.rsplit(".", 1)[0].split(".")[-1]
            derived = " ".join(
                w.capitalize() for w in stem.replace("-", " ").replace("_", " ").split()
            ) or domain
            named = _match_company_name(derived, companies)
            if named:
                row.update(company_id=named.id, company_name=named.name,
                           company_action="matched-name")
            else:
                row.update(company_id=None, company_name=derived,
                           company_action="create", company_domain=domain)
            continue

        # No usable domain: a free-mail address, or no address at all. Try the
        # company name from the prose; invent nothing if that misses.
        if row["company_name"]:
            named = _match_company_name(row["company_name"], companies)
            if named:
                row.update(company_id=named.id, company_name=named.name,
                           company_action="matched-name")
            else:
                row["company_action"] = "unmatched-name"
        else:
            row["company_action"] = "none"

    return sorted(grouped.values(), key=lambda r: -len(r["entry_ids"]))


def _match_company_name(name: str, companies) -> Optional[object]:
    """Loose name match, both directions — the same rule as from-email."""
    from app.services.contact_service import _normalize_company_name

    target = _normalize_company_name(name)
    if not target:
        return None
    for cand in companies:
        cand_norm = _normalize_company_name(cand.name)
        if not cand_norm:
            continue
        if cand_norm == target or target in cand_norm or cand_norm in target:
            return cand
    return None


def _suspected_duplicates(contacts: List[dict]) -> List[dict]:
    """Pairs of proposed names alike enough to be worth Jack's eye.

    Reported only. Never merged: two similar names are two people until a human
    says otherwise.
    """
    named = [c for c in contacts if c.get("name")]
    out = []
    for i, a in enumerate(named):
        for b in named[i + 1:]:
            an, bn = _norm_name(a["name"]), _norm_name(b["name"])
            if an == bn and a["key"] != b["key"]:
                ratio = 1.0
            else:
                ratio = SequenceMatcher(None, an, bn).ratio()
                # A bare first name inside a fuller one is the common case:
                # "Miriam" vs "Miriam Miller".
                if an and bn and (an in bn or bn in an):
                    ratio = max(ratio, 0.9)
            if ratio >= DUPLICATE_RATIO:
                out.append({
                    "a": {"key": a["key"], "name": a["name"], "email": a.get("email"),
                          "entries": len(a["entry_ids"])},
                    "b": {"key": b["key"], "name": b["name"], "email": b.get("email"),
                          "entries": len(b["entry_ids"])},
                    "similarity": round(ratio, 3),
                })
    return sorted(out, key=lambda d: -d["similarity"])


def print_report(report: dict) -> None:
    props = report["proposals"]
    high = [p for p in props if p["confidence"] == "high"]
    low = [p for p in props if p["confidence"] == "low"]
    unresolved = [p for p in props if p["confidence"] == "unresolved"]
    unresolved_ids = {p["entry_id"] for p in unresolved} | set(report["entries_missing"])

    print()
    print("=" * 72)
    print("BACKFILL — REPORT MODE (nothing was written)")
    print("=" * 72)

    if not report["model_available"]:
        print()
        print("ANTHROPIC_API_KEY is not set.")
        print("Address matching ran; the prose was NOT read. Entries with no")
        print("email address in them are listed below as unresolved — they are")
        print("not resolved, and they are not silently skipped. Set the key and")
        print("re-run to resolve them.")

    if report.get("model_error"):
        print()
        print(f"MODEL CALL FAILED PART-WAY: {report['model_error']}")
        print("What resolved before the failure is recorded below and in the")
        print("JSON. Nothing was written. This report is marked partial, and")
        print("write mode will refuse it — re-run report mode to complete it.")

    print()
    print(f"Entries processed          : {report['total_entries']}")
    print(f"  resolved, high confidence: {len(high)}")
    print(f"  resolved, low confidence : {len(low)}")
    print(f"  unresolved               : {len(unresolved_ids)}")
    print()

    contacts = report["contacts"]
    with_high = [c for c in contacts if c["high_confidence_entry_ids"]]
    print(f"Contacts identified        : {len(contacts)} "
          f"({len(with_high)} with at least one high-confidence entry)")
    existing = len([c for c in contacts if c.get("existing_contact_id")])
    print(f"  already in the database  : {existing}")
    print(f"  would be created         : {len(contacts) - existing}")
    print()

    print("CONTACTS AND ENTRY COUNTS")
    print("-" * 72)
    for c in contacts[:60]:
        flag = "" if c["high_confidence_entry_ids"] else "   (low confidence only)"
        who = c["name"] or c["email"] or c["key"]
        mail = f" <{c['email']}>" if c.get("email") else ""
        co = f"  -> {c['company_name']}" if c.get("company_name") else ""
        print(f"  {len(c['entry_ids']):>3} entries  {who}{mail}{co}{flag}")
    if len(contacts) > 60:
        print(f"  … and {len(contacts) - 60} more (all of them are in the JSON)")
    print()

    print("COMPANIES")
    print("-" * 72)
    actions: Dict[str, int] = {}
    for c in contacts:
        actions[c["company_action"]] = actions.get(c["company_action"], 0) + 1
    labels = {
        "matched-domain":  "matched an existing company by email domain",
        "matched-website": "matched an existing company by website",
        "matched-name":    "matched an existing company by name",
        "create":          "would be created (new domain, no name match)",
        "unmatched-name":  "named in the prose but no company matched — left null",
        "none":            "no company resolvable — company_stamp_id stays null",
    }
    for key, count in sorted(actions.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {labels.get(key, key)}")
    named = sorted({c["company_name"] for c in contacts if c.get("company_name")})
    print(f"  {len(named)} distinct companies named across all contacts")
    print()

    if report["markers"]:
        print("OUTLOOK MARKERS TO MIGRATE")
        print("-" * 72)
        for entry_id, m in report["markers"].items():
            print(f"  entry #{entry_id}: -> source_message_id, stripped from notes")
        print()

    if unresolved:
        print("UNRESOLVED — a sample, with the text as written")
        print("-" * 72)
        shown = 0
        for p in unresolved:
            if shown >= 15:
                break
            text = (p.get("action_text") or "")[:80]
            print(f"  #{p['entry_id']:<5} {text}")
            shown += 1
        if len(unresolved) > 15:
            print(f"  … and {len(unresolved) - 15} more in the JSON")
        print()

    if report["suspected_duplicates"]:
        print("SUSPECTED DUPLICATE PEOPLE — review these, nothing was merged")
        print("-" * 72)
        for d in report["suspected_duplicates"][:25]:
            a, b = d["a"], d["b"]
            print(f"  {d['similarity']:.2f}  "
                  f"{a['name']} ({a['entries']} entries){' <' + a['email'] + '>' if a['email'] else ''}"
                  f"  ~  "
                  f"{b['name']} ({b['entries']} entries){' <' + b['email'] + '>' if b['email'] else ''}")
        print()
        print("  Two similar names are never merged automatically. If two rows")
        print("  above are the same person, fix it in the app after the write:")
        print("  open one thread, use \"Move to another contact\" on its entries,")
        print("  then delete the empty contact.")
        print()

    print("=" * 72)
    print(f"Full proposed mapping written to: {REPORT_PATH}")
    print("Review it, then re-run with --confirm to apply high-confidence links.")
    print("=" * 72)


# ── Write mode ───────────────────────────────────────────────────────────────

def apply_report(db, report: dict) -> dict:
    """Apply the high-confidence links from a report. Idempotent.

    One transaction: either the whole backfill lands or none of it does. An
    entry that already has a contact is left exactly as it is, which is what
    makes a second run a no-op.
    """
    from app.models.activity import ActivityLog
    from app.models.company import Company
    from app.models.contact import Contact
    from app.services.contact_service import _next_company_id

    stats = {
        "entries_linked": 0, "entries_already_linked": 0,
        "contacts_created": 0, "contacts_reused": 0,
        "companies_created": 0, "stamps_set": 0,
        "markers_migrated": 0, "markers_skipped": 0,
        "responded_set": 0, "directions_set": 0,
        "low_confidence_skipped": 0,
    }

    high_by_entry = {
        p["entry_id"]: p for p in report["proposals"] if p["confidence"] == "high"
    }
    stats["low_confidence_skipped"] = len(
        [p for p in report["proposals"] if p["confidence"] != "high"]
    )

    for row in report["contacts"]:
        entry_ids = [e for e in row["high_confidence_entry_ids"] if e in high_by_entry]
        if not entry_ids:
            continue   # low-confidence-only contact: nothing is created for it

        # ── The company first, so a new contact can point at it.
        company = None
        if row.get("company_id"):
            company = db.query(Company).filter(Company.id == row["company_id"]).first()
        elif row["company_action"] == "create" and row.get("company_domain"):
            company = db.query(Company).filter(
                Company.email_domain == row["company_domain"]
            ).first()
            if company is None:
                company = Company(
                    company_id=_next_company_id(db),
                    name=row["company_name"],
                    industry="Unknown",
                    email_domain=row["company_domain"],
                    auto_created=True,
                    triaged=False,
                    company_type=None,   # no guess — Jack sets it
                )
                db.add(company)
                db.flush()
                stats["companies_created"] += 1

        # ── Then the contact. An address already on record wins; nothing new
        #    is created for someone who already exists.
        contact = None
        if row.get("existing_contact_id"):
            contact = db.query(Contact).filter(
                Contact.id == row["existing_contact_id"]
            ).first()
        if contact is None and row.get("email"):
            contact = db.query(Contact).filter(Contact.email == row["email"]).first()
        if contact is None and not row.get("email") and row.get("name"):
            # Name-keyed contacts have no address to match on. Reuse one this
            # same backfill created rather than making a second copy.
            contact = (
                db.query(Contact)
                .filter(Contact.name == row["name"], Contact.email.is_(None),
                        Contact.auto_created.is_(True))
                .first()
            )

        if contact is None:
            contact = Contact(
                name=row["name"] or row.get("email") or "Unknown Contact",
                email=row.get("email"),
                company_id=company.id if company else None,
                contact_type="tenant",     # never used to gate anything
                stage=row.get("stage") or "Sent",
                stage_changed_at=None,
                auto_created=True,
                triaged=False,
                responded=bool(row.get("responded")),
            )
            db.add(contact)
            db.flush()
            stats["contacts_created"] += 1
        else:
            stats["contacts_reused"] += 1
            if contact.company_id is None and company is not None:
                contact.company_id = company.id
            if row.get("responded") and not contact.responded:
                contact.responded = True
                stats["responded_set"] += 1

        # ── The entries.
        for entry_id in entry_ids:
            entry = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
            if entry is None:
                continue
            if entry.contact_id is not None:
                stats["entries_already_linked"] += 1
                continue
            entry.contact_id = contact.id
            stats["entries_linked"] += 1
            # Where no company resolves the stamp stays null — none is invented.
            if entry.company_stamp_id is None and company is not None:
                entry.company_stamp_id = company.id
                stats["stamps_set"] += 1
            # Only ever set inbound. An entry already marked outbound by hand is
            # not overwritten by a prose heuristic.
            proposal = high_by_entry.get(entry_id, {})
            if proposal.get("inbound") and (entry.direction or "outbound") != "inbound":
                entry.direction = "inbound"
                stats["directions_set"] += 1

    # ── Outlook markers. Independent of contact resolution: an entry that
    #    resolved to nobody still needs its dedup marker out of `notes`.
    for entry_id, marker in report.get("markers", {}).items():
        entry = db.query(ActivityLog).filter(ActivityLog.id == int(entry_id)).first()
        if entry is None or entry.source_message_id:
            continue
        clash = db.query(ActivityLog).filter(
            ActivityLog.source_message_id == marker["source_message_id"]
        ).first()
        if clash is not None:
            # The id is unique. Another entry already owns it — leave the note
            # alone rather than destroying evidence of a duplicate.
            stats["markers_skipped"] += 1
            continue
        entry.source_message_id = marker["source_message_id"]
        entry.notes = marker["notes"]
        stats["markers_migrated"] += 1

    db.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill contacts from activity entries.")
    ap.add_argument("--confirm", action="store_true",
                    help="Apply the report's high-confidence links. Without it, nothing is written.")
    ap.add_argument("--db-path", default=None,
                    help="Operate on this SQLite file instead of the configured one. "
                         "Rehearse against the backup before touching live.")
    args = ap.parse_args()

    # ANTHROPIC_API_KEY lives in backend/.env, same as it does for the app.
    load_env()

    db_path = resolve_db_path(args.db_path)
    print(f"Database: {db_path}", flush=True)
    require_contact_schema(db_path)
    db = make_session(db_path)

    try:
        if args.confirm:
            if not REPORT_PATH.exists():
                print(f"\nNo report at {REPORT_PATH}.")
                print("Run report mode first — write mode applies exactly what was reviewed.")
                return 1
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
            if report.get("partial"):
                print("\nThat report is marked partial — a model call failed while it")
                print("was being built, so it does not cover every entry.")
                print("Re-run report mode to complete it before writing.")
                return 1

            print(f"Applying {REPORT_PATH} (generated {report.get('generated_on')})\n")
            stats = apply_report(db, report)

            print("=" * 72)
            print("BACKFILL — WRITE MODE")
            print("=" * 72)
            print(f"  entries linked to a contact  : {stats['entries_linked']}")
            print(f"  entries already linked (skip): {stats['entries_already_linked']}")
            print(f"  contacts created             : {stats['contacts_created']}")
            print(f"  contacts reused              : {stats['contacts_reused']}")
            print(f"  companies created            : {stats['companies_created']}")
            print(f"  company stamps set           : {stats['stamps_set']}")
            print(f"  directions set to inbound    : {stats['directions_set']}")
            print(f"  contacts marked responded    : {stats['responded_set']}")
            print(f"  outlook markers migrated     : {stats['markers_migrated']}")
            print(f"  outlook markers skipped      : {stats['markers_skipped']}")
            print(f"  low-confidence entries skipped: {stats['low_confidence_skipped']}")
            print("=" * 72)
            if stats["entries_linked"] == 0 and stats["entries_already_linked"]:
                print("Nothing changed — this report has already been applied.")
            return 0

        print("Building the report (nothing will be written)…\n")
        report = build_report(db)

        # The unresolved sample prints the text as written, so carry it.
        from app.models.activity import ActivityLog
        unresolved_ids = [
            p["entry_id"] for p in report["proposals"] if p["confidence"] == "unresolved"
        ][:15]
        if unresolved_ids:
            texts = {
                e.id: (e.action_taken or "")
                for e in db.query(ActivityLog).filter(ActivityLog.id.in_(unresolved_ids)).all()
            }
            for p in report["proposals"]:
                if p["entry_id"] in texts:
                    p["action_text"] = texts[p["entry_id"]]

        REPORT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print_report(report)
        return 1 if report.get("partial") else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
