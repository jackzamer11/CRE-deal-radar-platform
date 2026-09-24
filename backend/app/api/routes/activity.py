import base64
import binascii
import os
import re
from typing import List, Optional, Tuple
from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload
from pydantic import BaseModel, field_validator

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_ingest import (
    ActivityAttachment, PENDING_UPDATE_FIELDS,
)
from app.services.attachment_storage import (
    NO_FILE, OVERSIZE, STORED, attachment_file_exists, max_attachment_bytes,
    relative_stored_path, resolve_attachment_path, resolve_stored_path,
    sanitize_file_name, store_attachment, store_attachment_bytes,
    stored_path_exists,
)
from app.services.contact_service import (
    STAGE_CHANGE_ACTION, apply_inbound_stage_rules, create_contact, create_fact,
    email_domain_of, mark_engaged, normalize_email, resolve_companies_by_names,
    resolve_company_for_email, resolve_or_create_contact,
)
from app.services.contactless_service import (
    company_key_column, contactless_filters, move_company_entries_to_contact,
    move_entry_to_contact, needs_contact_count, not_archived,
)
from app.services.email_ingest_service import (
    StatedValueError, coerce_value, is_own_literal_address, queue_company_update,
    name_key, record_address_override, resolve_contact_for_address,
    resolve_contacts_for_addresses, resolve_contacts_for_names,
    write_company_value,
)

router = APIRouter(prefix="/activity", tags=["activity"])

# Pipeline stages — current state only; an entry can move in any direction.
VALID_STAGES = ["Sent", "Replied", "Interested", "In Play", "Not Interested", "Dormant"]
# Stages that require a revisit reminder when selected.
REVISIT_STAGES = {"Dormant", "Not Interested"}

# "Emailed <Name> re <Company>" / "Called <Name> re <Company>" (case-insensitive).
# Captures the contact name between the verb and an optional " re <Company>" tail.
_CONTACT_RE = re.compile(r"^\s*(?:emailed|called)\s+(.+?)(?:\s+re\s+.+)?\s*$", re.IGNORECASE)


def parse_contact_name(action_taken: Optional[str]) -> Optional[str]:
    """Extract the contact name from a standard outreach log line.

    Handles "Emailed <Name> re <Company>" and "Called <Name> re <Company>"
    (the format written by the Outreach Draft modal). Returns None for any line
    that does not match, or whose name is the generic "contact" placeholder, so
    callers can fall back to displaying the entry as-is.
    """
    if not action_taken:
        return None
    m = _CONTACT_RE.match(action_taken)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    if not name or name.lower() == "contact":
        return None
    return name


# An action type implies a channel when the caller does not name one, so the
# existing Outreach Draft modal and outreach_agent.py keep producing sensible
# rows without being changed.
_ACTION_CHANNEL = {
    "EMAIL":   "email",
    "CALL":    "call",
    "MEETING": "meeting",
}


def _channel_for(action_type: Optional[str]) -> str:
    return _ACTION_CHANNEL.get((action_type or "").upper(), "other")


class ActivityAssign(BaseModel):
    """Attach an entry to a contact. contact_id may be null to detach."""
    contact_id: Optional[int] = None
    company_stamp_id: Optional[int] = None


class ActivityOut(BaseModel):
    id: int
    log_date: date
    opportunity_id: Optional[int]
    property_id: Optional[int]
    company_id: Optional[int]
    action_type: str
    action_taken: str
    outcome: Optional[str]
    follow_up_date: Optional[date]
    follow_up_action: Optional[str]
    created_by: str

    # Pipeline tracking
    stage:           str = "Sent"
    next_touch_date: Optional[date] = None

    outreach_type:  Optional[str] = None
    target_type:    Optional[str] = None
    contact_method: Optional[str] = None
    subject:        Optional[str] = None
    notes:          Optional[str] = None

    # Contact threads
    contact_id:        Optional[int] = None
    company_stamp_id:  Optional[int] = None
    direction:         Optional[str] = "outbound"
    channel:           Optional[str] = "other"
    source_message_id: Optional[str] = None
    sender_email:      Optional[str] = None

    # True when this person was only copied on the email. The timeline renders
    # it distinctly, and every "real correspondence" read filters it out.
    # Optional at the schema level so a row written before the column existed
    # (NULL) reads back as False rather than failing validation.
    participation:     Optional[bool] = False

    # Where a split entry came from when one email carried several deals — a
    # weekly roundup rather than direct correspondence. Null otherwise.
    source_note:       Optional[str] = None

    # Out of the Needs a Contact queue, still everywhere else. Optional at the
    # schema level so a row written before the column existed (NULL) reads back
    # as False rather than failing validation.
    archived:          Optional[bool] = False

    # Set only on a STAGE_CHANGE row — the transition the divider renders.
    stage_from: Optional[str] = None
    stage_to:   Optional[str] = None

    # Discovery capture — displayed only; nothing consumes these.
    disc_current_rent_psf:  Optional[float] = None
    disc_current_sf:        Optional[int]   = None
    disc_lease_expiry:      Optional[date]  = None
    disc_decision_timeline: Optional[str]   = None
    disc_buildout_needs:    Optional[str]   = None
    disc_decision_maker:    Optional[str]   = None

    # Denormalized
    property_address: Optional[str] = None
    company_name: Optional[str] = None
    company_stamp_name: Optional[str] = None
    opportunity_ref: Optional[str] = None
    contact_name: Optional[str] = None

    class Config:
        from_attributes = True


def _to_out(log: ActivityLog) -> "ActivityOut":
    """Build an ActivityOut with denormalized + parsed fields from an ORM row."""
    item = ActivityOut.model_validate(log)
    # Legacy rows created before the stage column existed read back as None.
    if not item.stage:
        item.stage = "Sent"
    item.participation = bool(item.participation)
    item.archived = bool(item.archived)
    if log.property:
        item.property_address = log.property.address
    if log.company:
        item.company_name = log.company.name
    if log.opportunity:
        item.opportunity_ref = log.opportunity.opportunity_id
    if log.stamped_company:
        item.company_stamp_name = log.stamped_company.name
    # A linked Contact is the truth. The regex parse remains the fallback for
    # the entries that pre-date contacts and have not been backfilled yet.
    if log.contact is not None:
        item.contact_name = log.contact.name
    else:
        item.contact_name = parse_contact_name(log.action_taken)
    return item


# _to_out() touches log.property / log.company / log.opportunity on every row,
# so list endpoints eager-load them — otherwise each row costs three extra
# queries (352 rows was ~132 queries and ~100ms before this).
#
# Built per call, not at import: joinedload() forces SQLAlchemy to configure the
# mappers, so a module-level tuple would break any importer that pulls in this
# router before every model is registered.
def _eager():
    return (
        joinedload(ActivityLog.property),
        joinedload(ActivityLog.company),
        joinedload(ActivityLog.opportunity),
        joinedload(ActivityLog.contact),
        joinedload(ActivityLog.stamped_company),
    )


class ActivityCreate(BaseModel):
    log_date: Optional[date] = None
    opportunity_id: Optional[int] = None
    property_id: Optional[int] = None
    company_id: Optional[int] = None
    action_type: str
    action_taken: str
    outcome: Optional[str] = None
    stage: Optional[str] = "Sent"
    next_touch_date: Optional[date] = None
    follow_up_date: Optional[date] = None
    follow_up_action: Optional[str] = None
    notes: Optional[str] = None
    # Outreach-specific fields (optional)
    outreach_type:  Optional[str] = None
    target_type:    Optional[str] = None
    contact_method: Optional[str] = None
    subject:        Optional[str] = None

    # Contact threads — every one optional, so every existing caller (the
    # Outreach Draft modal, the email automation, outreach_agent.py) keeps
    # working unchanged.
    contact_id:        Optional[int] = None
    company_stamp_id:  Optional[int] = None
    direction:         Optional[str] = None
    channel:           Optional[str] = None
    source_message_id: Optional[str] = None

    # Discovery capture. Stored and displayed; deliberately not wired into
    # scoring or generation — a tenant's claim is not verified data.
    disc_current_rent_psf:  Optional[float] = None
    disc_current_sf:        Optional[int]   = None
    disc_lease_expiry:      Optional[date]  = None
    disc_decision_timeline: Optional[str]   = None
    disc_buildout_needs:    Optional[str]   = None
    disc_decision_maker:    Optional[str]   = None


@router.get("/", response_model=List[ActivityOut])
def list_activity(
    since: Optional[date] = None,
    action_type: Optional[str] = None,
    stage: Optional[str] = None,
    q: Optional[str] = Query(
        None, description="Free text over the entry AND its linked contact and company",
    ),
    limit: int = Query(1000, le=1000),
    db: Session = Depends(get_db),
):
    """The flat activity feed, optionally filtered and searched.

    `q` searches the entry's own prose AND the names of the contact and company
    linked to it. Both halves are required, because entries are no longer
    written the way they used to be: the ingestion task writes a clean summary
    and puts the person and the company in structured links rather than
    repeating them in the sentence. Searching "Corcoran" against `action_taken`
    alone would return nothing while nine entries sat linked to them.

    One query. The contact and company are joined, not looked up per row.
    """
    query = db.query(ActivityLog).options(*_eager())
    if since:
        query = query.filter(ActivityLog.log_date >= since)
    if action_type:
        query = query.filter(ActivityLog.action_type == action_type)
    if stage:
        query = query.filter(ActivityLog.stage == stage)

    term = (q or "").strip()
    if term:
        like = f"%{term.lower()}%"
        # Aliased so the two company paths (the legacy free link and the stamp)
        # can both be searched without colliding.
        stamped = aliased(Company)
        linked = aliased(Company)
        query = (
            query
            .outerjoin(Contact, Contact.id == ActivityLog.contact_id)
            .outerjoin(stamped, stamped.id == ActivityLog.company_stamp_id)
            .outerjoin(linked, linked.id == ActivityLog.company_id)
            .filter(or_(
                func.lower(ActivityLog.action_taken).like(like),
                func.lower(ActivityLog.outcome).like(like),
                func.lower(ActivityLog.notes).like(like),
                func.lower(ActivityLog.subject).like(like),
                func.lower(Contact.name).like(like),
                func.lower(Contact.email).like(like),
                func.lower(stamped.name).like(like),
                func.lower(linked.name).like(like),
            ))
        )

    logs = query.order_by(ActivityLog.created_at.desc()).limit(limit).all()
    return [_to_out(log) for log in logs]


@router.get("/re-engage", response_model=List[ActivityOut])
def list_re_engage(db: Session = Depends(get_db)):
    """People whose next_touch_date is today or past due.

    Powers the Daily Briefing "Re-engage Today" section. Returns [] when nothing
    is due — never 500s on empty data.

    Two sources, deliberately:

      1. Contact.next_touch_date — the new home for the next-touch date, now
         that stage and timing belong to a person rather than to an entry.
      2. ActivityLog.next_touch_date — the legacy entry-level date.

    The union exists because this build creates the contact structure but does
    NOT migrate the 355 existing entries; every person surfacing here today
    carries their date on an entry, and dropping the legacy half would silently
    empty the section until the backfill ships. A contact-sourced row wins over
    an entry-sourced one for the same person, so the legacy half drains itself
    as the backfill attaches contacts — no cutover, no duplicates.
    """
    today = date.today()

    contacts_due = (
        db.query(Contact)
        .options(joinedload(Contact.company))
        .filter(
            Contact.next_touch_date.isnot(None),
            Contact.next_touch_date <= today,
        )
        .order_by(Contact.next_touch_date.asc())
        .all()
    )

    logs = (
        db.query(ActivityLog)
        .options(*_eager())
        .filter(
            ActivityLog.next_touch_date.isnot(None),
            ActivityLog.next_touch_date <= today,
            (ActivityLog.stage.is_(None)) | (ActivityLog.stage != "Sent"),
        )
        .order_by(ActivityLog.next_touch_date.asc())
        .all()
    )

    out: List[ActivityOut] = []
    seen_contact_ids = set()

    for contact in contacts_due:
        # The most recent entry gives the row something to link to and open.
        latest = (
            db.query(ActivityLog)
            .filter(
                ActivityLog.contact_id == contact.id,
                ActivityLog.action_type != STAGE_CHANGE_ACTION,
            )
            .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
            .first()
        )
        seen_contact_ids.add(contact.id)
        if latest is not None:
            item = _to_out(latest)
            item.stage = contact.stage or "Sent"
            item.next_touch_date = contact.next_touch_date
            item.contact_name = contact.name
            out.append(item)
            continue
        # A contact with a due date but no entries yet still has to surface —
        # a synthetic row rather than a silent omission. Negative id so it can
        # never collide with a real entry id in the frontend's key or deep link.
        out.append(ActivityOut(
            id=-contact.id,
            log_date=contact.next_touch_date or today,
            opportunity_id=None,
            property_id=None,
            company_id=contact.company_id,
            action_type="NOTE",
            action_taken=f"Follow up with {contact.name}",
            outcome=None,
            follow_up_date=None,
            follow_up_action=None,
            created_by="system",
            stage=contact.stage or "Sent",
            next_touch_date=contact.next_touch_date,
            contact_id=contact.id,
            contact_name=contact.name,
            company_name=contact.company.name if contact.company else None,
        ))

    for log in logs:
        # Skip a legacy entry whose contact already surfaced above, so a
        # backfilled person appears once rather than twice.
        if log.contact_id and log.contact_id in seen_contact_ids:
            continue
        out.append(_to_out(log))

    out.sort(key=lambda r: (r.next_touch_date or today))
    return out


@router.get("/message-ids", response_model=List[str])
def list_message_ids(
    since: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Provider message ids already logged — the email automation's dedup set.

    Reads the indexed source_message_id column only: no full-row scan, no joins,
    and deliberately no limit ceiling. The previous approach scanned
    GET /activity/?limit=1000 and parsed markers out of `notes`, so past 1,000
    entries the oldest markers fell off the end and old emails relogged.

    `since` filters on log_date for a cheaper window; omit it for every id.
    """
    q = db.query(ActivityLog.source_message_id).filter(
        ActivityLog.source_message_id.isnot(None)
    )
    if since:
        q = q.filter(ActivityLog.log_date >= since)
    return [row[0] for row in q.all()]


# ── Needs a contact ──────────────────────────────────────────────────────────
# Everything still waiting on a person. Declared above the /{entry_id} routes —
# entry_id is an int, so a static path below them would parse as an id and 422.

class NeedsContactEntry(ActivityOut):
    """A queue row: the entry plus enough company context to move it.

    `effective_company_*` is the stamp falling back to the legacy free link —
    the same COALESCE the company cards group on, surfaced so the picker can
    open on the right company without the frontend re-deriving it.
    """
    effective_company_id: Optional[int] = None
    effective_company_name: Optional[str] = None


class NeedsContactPage(BaseModel):
    total: int
    limit: int
    offset: int
    entries: List[NeedsContactEntry]


@router.get("/needs-contact", response_model=NeedsContactPage)
def list_needs_contact(
    company_id: Optional[int] = None,
    include_archived: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Every entry with no contact on it, oldest first — the queue to drain.

    Entries with a company and entries without are in the same list on purpose:
    both need exactly one thing from Jack, and splitting them would make the
    badge count something other than "how much is left".

    Oldest first because the oldest is the one most likely to be forgotten, and
    because working forwards means the queue visibly shortens from the top.

    `company_id` narrows to the entries one company is holding — what opening
    its card shows. `total` then means that company's count; with no filter it
    is the badge number, the whole queue rather than this page.

    Archived entries are out by default and come back with
    `include_archived=true` — the "show archived" toggle. The company panel
    passes it, because a card counts what the company is holding including the
    archived ones, and opening the card must not show fewer than the card
    promised.
    """
    base = db.query(ActivityLog).filter(*contactless_filters())
    if not include_archived:
        base = base.filter(not_archived())
    if company_id is not None:
        base = base.filter(company_key_column() == company_id)
        total = base.count()
    elif include_archived:
        total = base.count()
    else:
        total = needs_contact_count(db)
    rows = (
        base.options(*_eager())
        .order_by(ActivityLog.log_date.asc(), ActivityLog.id.asc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )

    entries: List[NeedsContactEntry] = []
    for log in rows:
        item = NeedsContactEntry.model_validate(_to_out(log).model_dump())
        item.effective_company_id = log.company_stamp_id or log.company_id
        # _to_out already denormalized both names; pick the stamp's first, to
        # match which id was chosen above.
        item.effective_company_name = item.company_stamp_name or item.company_name
        entries.append(item)

    return NeedsContactPage(
        total=total, limit=limit, offset=offset, entries=entries,
    )


class NeedsContactCount(BaseModel):
    total: int


@router.get("/needs-contact/count", response_model=NeedsContactCount)
def count_needs_contact(db: Session = Depends(get_db)):
    """Just the badge number — one COUNT, no rows fetched.

    Always excludes archived entries: the badge is "how much is left to do",
    and an archived entry is by definition not left to do.
    """
    return NeedsContactCount(total=needs_contact_count(db))


class ActivityArchived(BaseModel):
    archived: bool


@router.patch("/{entry_id}/archived", response_model=ActivityOut)
def set_activity_archived(
    entry_id: int, payload: ActivityArchived, db: Session = Depends(get_db),
):
    """Archive or unarchive an entry — one endpoint, both directions.

    Archiving takes the entry out of the Needs a Contact queue and the badge
    count and nothing else. It stays in the database, stays searchable through
    /activity/?q=, stays in All Activity, and stays on its company's card. It
    is reversible with the same call and `archived: false`, which is why there
    is no separate unarchive route to forget to keep in step with this one.
    """
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")
    log.archived = bool(payload.archived)
    db.commit()
    db.refresh(log)
    return _to_out(log)


class MoveAllRequest(BaseModel):
    company_id: int
    contact_id: int


class MoveAllResult(BaseModel):
    moved: int
    facts_moved: int
    company_id: int
    contact_id: int


@router.post("/move-all-to-contact", response_model=MoveAllResult)
def move_all_to_contact(payload: MoveAllRequest, db: Session = Depends(get_db)):
    """Move every contactless entry a company holds onto one contact.

    The company card in one action, which is the common case: a company holding
    unassigned entries usually has exactly one person behind all of them.

    Teaches the resolver nothing, and cannot: every entry moved here had no
    contact, and moving an entry off nobody says where it belongs, never who an
    address belongs to. See _reassignment_corrects_identity.
    """
    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    contact = db.query(Contact).filter(Contact.id == payload.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    moved, facts_moved = move_company_entries_to_contact(db, company.id, contact)
    db.commit()
    return MoveAllResult(
        moved=moved, facts_moved=facts_moved,
        company_id=company.id, contact_id=contact.id,
    )


class EmailRecipient(BaseModel):
    """One address on an email, with the display name if the mailbox gave one."""
    email: Optional[str] = None
    name:  Optional[str] = None


class EmailFact(BaseModel):
    """A durable statement the email made about the person.

    Written straight through to their fact list, sourced to this entry. Facts
    are prose about a human being, not data about a company — nothing scores
    off them — so there is nothing to confirm and no queue.
    """
    text: str
    learned_date: Optional[date] = None


class ProposedCompanyUpdate(BaseModel):
    """A value the email STATED about the company. Never written on arrival.

    field is one of PENDING_UPDATE_FIELDS: headcount, growth_rate,
    lease_expiry, sf. source_sentence is the sentence it came from, and it is
    what makes the confirmation answerable — "they said 40" is not reviewable,
    the sentence is.
    """
    field: str
    value: str
    source_sentence: Optional[str] = None


class EmailAttachment(BaseModel):
    """A file that arrived on the email.

    stored_path is where the ingestion task put the download; it is read and
    then discarded — only the filename and the year it was filed under reach a
    column. `inline` marks a signature image or embedded screenshot: those are
    dropped, not filed.
    """
    filename: str
    stored_path: Optional[str] = None
    description: Optional[str] = None
    inline: bool = False


def _bare_fact_strings(value):
    """Tolerate ["they moved offices"] as well as [{"text": "..."}]."""
    if isinstance(value, list):
        return [{"text": v} if isinstance(v, str) else v for v in value]
    return value


def _stringified_proposed_values(value):
    """Accept 40 as readily as "40" — the model may emit either."""
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict) and "value" in item and item["value"] is not None:
                item = {**item, "value": str(item["value"])}
            out.append(item)
        return out
    return value


# The discovery fields describe ONE deal. On a single-deal email they sit at the
# top level of the payload; on a multi-deal email each deal carries its own.
_DISCOVERY_FIELDS = (
    "disc_current_rent_psf", "disc_current_sf", "disc_lease_expiry",
    "disc_decision_timeline", "disc_buildout_needs", "disc_decision_maker",
)


class EmailDeal(BaseModel):
    """One deal inside an email that carries several.

    A weekly leasing-notes email lists seven deals — different tenants,
    buildings, rates and expiries. Each becomes its own entry, stamped to its
    own company. Everything about the EMAIL (sender, direction, message id,
    date, recipients, attachments) stays on the parent payload and applies to
    every deal: they all arrived in the same message.
    """
    company_override: Optional[str] = None
    company_override_id: Optional[int] = None
    action_taken: Optional[str] = None
    # Overrides the email-level source_note for this deal only.
    source_note: Optional[str] = None

    # The person this deal is about, when it is not the sender. Ann's weekly
    # notes mention a Scott Management contact: that deal's entry and its facts
    # belong on THEIR thread, not on Ann's — otherwise her Relationship panel
    # fills with facts about other people's tenants.
    #
    # Resolution is strictly ordered:
    #
    #   contact_email → the same rules as any address on the email (a taught
    #     correction, then the address itself, then created untriaged with the
    #     company from their own domain). The address ALWAYS wins; contact_name
    #     is then only a display name, and an address Jack owns still falls back
    #     to the sender rather than reaching for the name.
    #   contact_name alone → matched case-insensitively against the contacts at
    #     THIS DEAL'S company that hold no email address, and created as one
    #     (null email, untriaged) if none matches. Ann's Brinks note names three
    #     renewal contacts and gives no address for any of them; without this
    #     their facts had nowhere to land. Scoped to the company because a bare
    #     name is a weak identifier — two people called Mike Johnson are two
    #     people — and never matched against a contact who has an address.
    #   neither → the sender.
    contact_email: Optional[str] = None
    contact_name: Optional[str] = None

    disc_current_rent_psf:  Optional[float] = None
    disc_current_sf:        Optional[int]   = None
    disc_lease_expiry:      Optional[date]  = None
    disc_decision_timeline: Optional[str]   = None
    disc_buildout_needs:    Optional[str]   = None
    disc_decision_maker:    Optional[str]   = None

    proposed_company_updates: List[ProposedCompanyUpdate] = []
    facts: List[EmailFact] = []

    @field_validator("facts", mode="before")
    @classmethod
    def _accept_bare_fact_strings(cls, value):
        return _bare_fact_strings(value)

    @field_validator("proposed_company_updates", mode="before")
    @classmethod
    def _stringify_proposed_values(cls, value):
        return _stringified_proposed_values(value)


class ActivityFromEmail(BaseModel):
    """One email, as the scheduled mailbox check interpreted it.

    Every field beyond the original six is optional, so the mailbox task that
    exists today keeps working against this endpoint unchanged.
    """
    from_email: Optional[str] = None
    from_name:  Optional[str] = None
    to_email:   Optional[str] = None
    direction:  str = "outbound"     # outbound | inbound
    subject:    Optional[str] = None
    action_taken: Optional[str] = None
    outcome:      Optional[str] = None
    follow_up_action: Optional[str] = None
    source_message_id: Optional[str] = None
    sent_at: Optional[date] = None

    # A short line recording where the entry came from — "From Ann Waller's
    # leasing notes, September 15, 2026." Applies to every entry the email
    # produces; a deal may override it.
    source_note: Optional[str] = None

    # ── The interpreted record ───────────────────────────────────────────────
    facts: List[EmailFact] = []

    # Discovery capture, straight onto the entry. Displayed, never scored — a
    # claim made in an email is not verified data.
    disc_current_rent_psf:  Optional[float] = None
    disc_current_sf:        Optional[int]   = None
    disc_lease_expiry:      Optional[date]  = None
    disc_decision_timeline: Optional[str]   = None
    disc_buildout_needs:    Optional[str]   = None
    disc_decision_maker:    Optional[str]   = None

    # These NEVER write. They queue for Jack (see models/email_ingest.py) —
    # except against a company created by this very request, where there is
    # nothing to conflict with.
    proposed_company_updates: List[ProposedCompanyUpdate] = []

    # Set on the contact ONLY when the email named a specific day. The task does
    # not send this otherwise, and nothing here infers one.
    next_touch_date: Optional[date] = None

    # Written-to versus copied. See the `participation` column on ActivityLog.
    to_recipients: List[EmailRecipient] = []
    cc_recipients: List[EmailRecipient] = []
    # Accepted and deliberately IGNORED. Declared rather than rejected so a task
    # that sends the full header set gets a clear contract: bcc is skipped
    # entirely — no contact, no entry, not recorded anywhere.
    bcc_recipients: List[EmailRecipient] = []

    attachments: List[EmailAttachment] = []

    # The company the email is clearly about, when it differs from the sender's
    # domain — a broker at Avison Young writing about Collaborative AV. Either
    # the name (resolved loosely, created if unknown) or an id.
    company_override: Optional[str] = None
    company_override_id: Optional[int] = None

    # One email, several deals. When non-empty, the endpoint writes one entry
    # per deal instead of one for the email, and the per-deal fields above
    # (facts, discovery, proposed updates, company override) must be left empty
    # — they belong on the deals, and silently dropping them would lose data.
    # Absent or empty → exactly the single-entry path.
    deals: Optional[List[EmailDeal]] = None

    # Declared only so they can be REFUSED. Naming the person is a per-deal
    # question: a plain email's entry belongs to its sender, so there is no
    # address or name to override. Undeclared, Pydantic dropped these silently
    # and the entry landed on the sender anyway — a lost contact, 200 OK. See
    # _deal_specs.
    contact_email: Optional[str] = None
    contact_name: Optional[str] = None

    @field_validator("facts", mode="before")
    @classmethod
    def _accept_bare_fact_strings(cls, value):
        return _bare_fact_strings(value)

    @field_validator("proposed_company_updates", mode="before")
    @classmethod
    def _stringify_proposed_values(cls, value):
        return _stringified_proposed_values(value)


class EmailEntryResult(BaseModel):
    """One entry an email produced — one per deal, or one for a plain email."""
    id: int
    source_message_id: Optional[str] = None
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    action_taken: str
    source_note: Optional[str] = None
    # The deal named a contact_email that could not be used — an address Jack
    # owns, or not an address at all — so the entry went to the sender.
    contact_email_ignored: Optional[str] = None
    facts_written: int = 0
    pending_updates_created: int = 0
    company_values_written: List[str] = []
    participant_entry_ids: List[int] = []
    participation_entry_ids: List[int] = []


class ActivityFromEmailResult(ActivityOut):
    """The entry that was created, plus what else the payload produced.

    Extends ActivityOut rather than replacing it, so a caller reading `id` off
    the response — the mailbox task does exactly that — is unaffected. On a
    multi-deal email the top-level entry is the FIRST deal's, the counts are
    totals across every deal, and `entries` breaks them down per deal.
    """
    facts_written: int = 0
    pending_updates_created: int = 0
    company_values_written: List[str] = []
    attachments_saved: int = 0
    attachments_missing: List[str] = []
    # Recorded but deliberately not written: over settings.MAX_ATTACHMENT_BYTES.
    # Separate from attachments_missing because they are different problems —
    # one is a file that should be there, the other never could be.
    attachments_oversize: List[str] = []
    # One entry per additional participant: the other To recipients (direct) and
    # every Cc recipient (participation).
    participant_entry_ids: List[int] = []
    participation_entry_ids: List[int] = []
    # Addresses that resolved to nobody because Jack owns them.
    skipped_own_addresses: List[str] = []
    # Every primary entry written, in deal order. One element for a plain email.
    entries: List[EmailEntryResult] = []


class _Participant:
    """One person an email reaches, and how directly."""
    __slots__ = ("email", "name", "participation")

    def __init__(self, email: str, name: Optional[str], participation: bool):
        self.email = email
        self.name = name
        self.participation = participation


def _collect_participants(
    payload: ActivityFromEmail, direction: str,
) -> Tuple[List[_Participant], List[str]]:
    """Everyone this email reaches, in thread-owning order, plus skipped addresses.

    Order, and why:
      1. The sender owns the entry — on an inbound mail that is the counterpart,
         and the first row carries the bare provider message id.
      2. To recipients are direct participants: real correspondence.
      3. Cc recipients are participation: on their timeline as history, counting
         toward nothing.

    Deduplicated on the normalized address, and a direct listing always wins
    over a copied one — someone on both the To and the Cc line was written to.
    Bcc never enters this function's inputs at all.

    An address Jack owns resolves to nobody and is reported back rather than
    silently dropped.
    """
    ordered: List[_Participant] = []
    seen: dict = {}
    skipped: List[str] = []

    def add(raw_email: Optional[str], name: Optional[str], participation: bool):
        normalized = normalize_email(raw_email)
        if not normalized:
            return
        if is_own_literal_address(normalized):
            if normalized not in skipped:
                skipped.append(normalized)
            return
        if normalized in seen:
            # Already listed. A direct listing outranks a copied one.
            if not participation:
                seen[normalized].participation = False
            return
        person = _Participant(normalized, (name or "").strip() or None, participation)
        seen[normalized] = person
        ordered.append(person)

    # The sender. On an outbound mail this is Jack and drops out via the guard,
    # leaving the first To recipient owning the entry — which is exactly what
    # this endpoint did before recipients existed.
    add(payload.from_email, payload.from_name, False)

    to_list = list(payload.to_recipients)
    if not to_list and payload.to_email:
        # The original single-recipient shape. Still supported, unchanged.
        to_list = [EmailRecipient(email=payload.to_email)]
    for recipient in to_list:
        add(recipient.email, recipient.name, False)

    for recipient in payload.cc_recipients:
        add(recipient.email, recipient.name, True)

    return ordered, skipped


def _participant_message_id(base: Optional[str], index: int, contact_id: Optional[int]) -> Optional[str]:
    """The provider id for one participant's row.

    Only the first row carries the bare id, because the column is UNIQUE and one
    email produces one row per participant. The rest carry it suffixed with the
    contact id, which keeps them traceable to the same message while leaving the
    dedup check — which looks for the bare id — correct.
    """
    if not base or index == 0:
        return base
    return f"{base}#p{contact_id or index}"


def _deal_message_id(base: Optional[str], deal_index: int) -> Optional[str]:
    """The provider id for one deal's entry, when an email carries several.

    The same pattern as participant rows, one level up: the first deal keeps the
    bare id — so GET /message-ids still returns it and a re-POST is still caught
    by the bare-id check before anything is written — and every later deal is
    suffixed "#d2", "#d3"... Its participant rows then suffix that again.
    """
    if not base or deal_index == 0:
        return base
    return f"{base}#d{deal_index + 1}"


class _DealSpec:
    """What one entry is about. A plain email is a single spec built from the
    top-level fields; a multi-deal email is one spec per deal."""
    __slots__ = (
        "company_override", "company_override_id", "action_taken", "source_note",
        "discovery", "proposed_company_updates", "facts",
        "contact_email", "contact_name",
    )

    def __init__(self, source, source_note: Optional[str]):
        self.company_override = (source.company_override or "").strip() or None
        self.company_override_id = source.company_override_id
        self.action_taken = (source.action_taken or "").strip()
        self.source_note = (source_note or "").strip() or None
        self.discovery = {f: getattr(source, f) for f in _DISCOVERY_FIELDS}
        self.proposed_company_updates = list(source.proposed_company_updates)
        self.facts = list(source.facts)
        # Only a deal names its own contact; a plain email has none.
        self.contact_email = normalize_email(getattr(source, "contact_email", None))
        self.contact_name = (getattr(source, "contact_name", None) or "").strip() or None


def _deal_specs(payload: ActivityFromEmail) -> List[_DealSpec]:
    """The entries this email becomes. Raises 400 on an ambiguous payload."""
    if not payload.deals:
        # The mirror of the stray check below. Without deals there is one entry
        # and it belongs to the sender, so naming a contact on it means the
        # caller meant to send a deal. Refused rather than dropped: the old
        # silent drop returned 200 and lost the contact.
        misplaced = [
            name for name, present in (
                ("contact_email", bool((payload.contact_email or "").strip())),
                ("contact_name", bool((payload.contact_name or "").strip())),
            ) if present
        ]
        if misplaced:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{', '.join(misplaced)} belongs on a deal — a plain email's "
                    "entry belongs to its sender. Send it inside deals."
                ),
            )
        return [_DealSpec(payload, payload.source_note)]

    # With deals present, per-deal fields at the top level have no deal to
    # belong to. Refused rather than silently dropped.
    stray = [
        name for name, present in (
            ("facts", bool(payload.facts)),
            ("proposed_company_updates", bool(payload.proposed_company_updates)),
            ("company_override", bool((payload.company_override or "").strip())),
            ("company_override_id", payload.company_override_id is not None),
            ("contact_email", bool((payload.contact_email or "").strip())),
            ("contact_name", bool((payload.contact_name or "").strip())),
        ) if present
    ] + [f for f in _DISCOVERY_FIELDS if getattr(payload, f) is not None]
    if stray:
        raise HTTPException(
            status_code=400,
            detail=(
                "When deals is present these fields belong on each deal, not on "
                f"the email: {', '.join(stray)}"
            ),
        )
    return [
        _DealSpec(deal, deal.source_note or payload.source_note)
        for deal in payload.deals
    ]


def _resolve_deal_companies(
    db: Session, specs: List[_DealSpec], sender_company_fn,
) -> List[Tuple[Optional[Company], bool]]:
    """(company, created) per deal, resolved in ONE pass however many deals.

    Ids are fetched in one query, names are matched against one read of the
    company table, and the sender-domain fallback runs at most once. Order of
    precedence per deal is unchanged from the single-entry path: an id, then a
    name, then the sender's domain.
    """
    ids = {s.company_override_id for s in specs if s.company_override_id is not None}
    by_id = {}
    if ids:
        by_id = {c.id: c for c in db.query(Company).filter(Company.id.in_(ids)).all()}
        missing = sorted(ids - set(by_id))
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"company_override_id {missing[0]} not found",
            )

    names = [
        s.company_override for s in specs
        if s.company_override_id is None and s.company_override
    ]
    by_name = resolve_companies_by_names(db, names) if names else {}

    sender = None
    out: List[Tuple[Optional[Company], bool]] = []
    for spec in specs:
        if spec.company_override_id is not None:
            out.append((by_id[spec.company_override_id], False))
        elif spec.company_override:
            out.append(by_name[spec.company_override])
        else:
            if sender is None:
                sender = sender_company_fn()
            out.append(sender)
    return out


@router.post("/from-email", response_model=ActivityFromEmailResult)
def create_activity_from_email(
    payload: ActivityFromEmail, db: Session = Depends(get_db),
):
    """Log an interpreted email: the entry, its participants, and what it said.

    Everything happens in ONE transaction. Resolve the company, resolve or
    create a contact per participant, write an entry on each of their threads,
    write the facts and the discovery capture, queue anything the email stated
    about the company, file the attachments. A failure anywhere rolls all of it
    back — a half-written email is worse than an unwritten one, because nothing
    downstream can tell it is half-written.

    **One email, several deals.** A weekly leasing-notes email carries seven
    deals for seven tenants. With `deals` present, each becomes its own entry
    stamped to its own company, with its own discovery capture, facts and
    stated values. The email-level fields (sender, direction, message id, date,
    recipients, attachments, source_note) apply to every one. All seven write
    in the same single transaction: a failure on the fifth writes none, and the
    message id stays free for the next run.

    What writes and what waits, and why the line sits there:

    * **Facts write.** A fact is prose about a person. Nothing scores off it,
      and it is visible and editable on the thread.
    * **Discovery fields write.** They live on the entry, inert by design.
    * **Stated company values do NOT write.** headcount, growth, lease expiry
      and SF are scoring inputs; a sentence in an email is not verified data.
      They queue with both values and the source sentence — unless the company
      did not exist until this request, in which case there is nothing to
      conflict with and confirmation would be theatre.

    The automation's own spam and noise filter is the only relevance gate. If an
    email is clean enough to log, its sender is clean enough to become a
    contact — there is deliberately no second filter here.
    """
    direction = (payload.direction or "outbound").lower()
    if direction not in ("outbound", "inbound"):
        raise HTTPException(
            status_code=400,
            detail="direction must be 'outbound' or 'inbound'",
        )

    specs = _deal_specs(payload)

    # Idempotency: a second POST with the same message id is a 409, not a
    # duplicate row and not a 500. Checked against the BARE id, which only the
    # first participant's row of the first deal carries — so an email that
    # produced seven entries is still caught by one lookup.
    if payload.source_message_id:
        existing = db.query(ActivityLog).filter(
            ActivityLog.source_message_id == payload.source_message_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"An entry for message {payload.source_message_id} already "
                    f"exists (activity id {existing.id})."
                ),
            )

    participants, skipped_own = _collect_participants(payload, direction)

    result_facts = 0
    result_pending = 0
    result_written: List[str] = []
    result_attached = 0
    result_missing: List[str] = []
    result_oversize: List[str] = []
    extra_direct: List[int] = []
    extra_participation: List[int] = []
    # Per primary entry, in deal order: (log, EmailEntryResult counts).
    deal_logs: List[ActivityLog] = []
    deal_results: List[EmailEntryResult] = []

    try:
        # ── The company each deal is ABOUT ────────────────────────────────────
        # An override wins over the sender's domain: a broker at Avison Young
        # writing about Collaborative AV is a conversation about Collaborative
        # AV, stamped there, while the broker himself stays under Avison Young.
        def _sender_company():
            if not participants:
                return None, False
            # Free-mail senders get no company — never a company called "Gmail".
            return resolve_company_for_email(
                db, participants[0].email, participants[0].name,
            )

        companies = _resolve_deal_companies(db, specs, _sender_company)

        # ── The people — resolved once, whatever the deal count ───────────────
        # Each participant's company comes from THEIR OWN domain, never from the
        # entry's stamp. A broker at Avison Young copied on a Collaborative AV
        # email lands under Avison Young.
        people = []   # (index, person, contact, own_company)
        for index, person in enumerate(participants):
            own_company, _ = resolve_company_for_email(db, person.email, person.name)
            contact, _created = resolve_contact_for_address(
                db, person.email, person.name, company=own_company,
                # Creation stays in this module's hands: it is the step that
                # can fail mid-transaction, and this is where the transaction
                # is owned.
                create_fn=resolve_or_create_contact,
            )
            if contact is None:
                continue
            if not person.participation:
                # inbound → responded=True, and Sent → Replied only. A contact
                # already at Interested or In Play is never regressed.
                apply_inbound_stage_rules(contact, direction)
            # A copied recipient's stage, responded flag and triage are all left
            # exactly as they were. Ten copies must not make a relationship.
            people.append((index, person, contact, own_company))

        primary_contact = people[0][2] if people else None
        log_date = payload.sent_at or date.today()
        sender_email = normalize_email(payload.from_email)
        # Every row a `deals` payload writes is marked, including recipients'.
        deal_sourced = bool(payload.deals)

        # ── The contact each deal is about — all of them in one pass ──────────
        # The email still belongs to the sender: nothing above changes. This
        # only decides whose thread each deal's entry and facts land on. An
        # address Jack owns resolves to nobody and the deal stays with the
        # sender. A deal contact keeps THEIR OWN company; the entry is stamped
        # to the deal's company. The two are deliberately not reconciled.
        deal_contacts = resolve_contacts_for_addresses(
            db,
            [(spec.contact_email, spec.contact_name) for spec in specs if spec.contact_email],
            # The transaction is owned here, so creation is too.
            create_fn=create_contact,
        )
        # And the ones named without an address — a second single pass, not a
        # lookup per deal. The company is the deal's own, already resolved
        # above, because a bare name is only an identity within one company.
        deal_name_contacts = resolve_contacts_for_names(
            db,
            [
                (spec.contact_name, company)
                for spec, (company, _) in zip(specs, companies)
                if not spec.contact_email and spec.contact_name
            ],
            create_fn=create_contact,
        )

        for deal_index, (spec, (company, company_is_new)) in enumerate(zip(specs, companies)):
            deal_message_id = _deal_message_id(payload.source_message_id, deal_index)
            deal_direct: List[int] = []
            deal_participation: List[int] = []

            summary = spec.action_taken
            if not summary:
                if primary_contact is not None:
                    who = primary_contact.name or people[0][1].email
                    verb = "Received email from" if direction == "inbound" else "Emailed"
                    summary = f"{verb} {who}"
                else:
                    summary = "Received email" if direction == "inbound" else "Sent email"
                summary += f" re {payload.subject}" if payload.subject else ""

            # Address first, always. Only when the deal gave no address does
            # the name get a say — an address that resolves to nobody (one Jack
            # owns) falls back to the sender exactly as it did before, rather
            # than quietly reaching for the name instead.
            if spec.contact_email:
                deal_contact, deal_contact_company = deal_contacts.get(
                    spec.contact_email, (None, None),
                )
            elif spec.contact_name:
                deal_contact = deal_name_contacts.get(
                    (name_key(spec.contact_name),
                     company.id if company is not None else None),
                )
                # A name-keyed contact holds the deal's company already; there
                # is no separate domain to stamp them under.
                deal_contact_company = company if deal_contact is not None else None
            else:
                deal_contact, deal_contact_company = None, None
            ignored_contact_email = (
                spec.contact_email if spec.contact_email and deal_contact is None else None
            )
            if (deal_contact is not None and primary_contact is not None
                    and deal_contact.id == primary_contact.id):
                # The deal names the sender — that is simply today's path.
                deal_contact = None

            primary_log = None
            if deal_contact is not None:
                # This deal's entry is redirected to the person it is about.
                # Their stage and responded flag are untouched — they did not
                # write this email. sender_email records the address that chose
                # this contact, because a reassignment teaches the resolver from
                # that column: correcting this entry must map THIS address, never
                # the sender's.
                primary_log = ActivityLog(
                    log_date       = log_date,
                    company_id     = company.id if company else None,
                    contact_id     = deal_contact.id,
                    company_stamp_id = (company.id if company else None) or (
                        deal_contact_company.id if deal_contact_company else None
                    ),
                    action_type    = "EMAIL",
                    action_taken   = summary,
                    outcome        = payload.outcome,
                    subject        = payload.subject,
                    follow_up_action = payload.follow_up_action,
                    stage          = "Sent",
                    direction      = direction,
                    channel        = "email",
                    source_message_id = deal_message_id,
                    sender_email   = spec.contact_email,
                    participation  = False,
                    deal_sourced   = deal_sourced,
                    contact_method = "email",
                    created_by     = "email-automation",
                    source_note    = spec.source_note,
                    **spec.discovery,
                )
                db.add(primary_log)
                db.flush()

            for index, person, contact, own_company in people:
                if deal_contact is not None and (
                    contact.id == primary_contact.id or contact.id == deal_contact.id
                ):
                    # The sender's row for this deal IS the entry that was
                    # redirected; and the deal contact already has theirs.
                    continue
                is_primary = primary_log is None
                log = ActivityLog(
                    log_date       = log_date,
                    company_id     = company.id if company else None,
                    contact_id     = contact.id,
                    company_stamp_id = (company.id if company else None) or (
                        own_company.id if own_company else None
                    ),
                    action_type    = "EMAIL",
                    action_taken   = summary,
                    outcome        = payload.outcome if is_primary else None,
                    subject        = payload.subject,
                    follow_up_action = payload.follow_up_action if is_primary else None,
                    stage          = "Sent",
                    direction      = direction,
                    channel        = "email",
                    source_message_id = _participant_message_id(
                        deal_message_id, index, contact.id,
                    ),
                    sender_email   = sender_email,
                    participation  = person.participation,
                    deal_sourced   = deal_sourced,
                    contact_method = "email",
                    created_by     = "email-automation",
                    source_note    = spec.source_note,
                    # Discovery capture belongs to the conversation, not to the
                    # people copied on it.
                    **{f: (v if is_primary else None) for f, v in spec.discovery.items()},
                )
                db.add(log)
                db.flush()

                if is_primary:
                    primary_log = log
                elif person.participation:
                    deal_participation.append(log.id)
                else:
                    deal_direct.append(log.id)

            # Nobody to attach to — every address on the mail is one Jack owns,
            # or there were none. The entry is still logged, unattached, rather
            # than filed under a contact called "Jack Zamer".
            if primary_log is None:
                primary_log = ActivityLog(
                    log_date       = log_date,
                    company_id     = company.id if company else None,
                    company_stamp_id = company.id if company else None,
                    action_type    = "EMAIL",
                    action_taken   = summary,
                    outcome        = payload.outcome,
                    subject        = payload.subject,
                    follow_up_action = payload.follow_up_action,
                    stage          = "Sent",
                    direction      = direction,
                    channel        = "email",
                    source_message_id = deal_message_id,
                    sender_email   = sender_email,
                    deal_sourced   = deal_sourced,
                    contact_method = "email",
                    created_by     = "email-automation",
                    source_note    = spec.source_note,
                    **spec.discovery,
                )
                db.add(primary_log)
                db.flush()

            # ── Facts — straight through, sourced to this deal's entry ────────
            deal_facts = 0
            fact_contact = deal_contact or primary_contact
            if fact_contact is not None:
                for fact in spec.facts:
                    text = (fact.text or "").strip()
                    if not text:
                        continue
                    create_fact(
                        db, fact_contact, text,
                        source_entry_id=primary_log.id,
                        learned_date=fact.learned_date or primary_log.log_date,
                        # A roundup mentioning someone is not Jack engaging
                        # them. A plain email's facts triage exactly as before.
                        triage=not deal_sourced,
                    )
                    deal_facts += 1

            # ── Stated company values ─────────────────────────────────────────
            deal_pending = 0
            deal_written: List[str] = []
            for proposed in spec.proposed_company_updates:
                field = (proposed.field or "").strip()
                if field not in PENDING_UPDATE_FIELDS:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Unknown proposed update field '{proposed.field}'. "
                            f"One of: {', '.join(PENDING_UPDATE_FIELDS)}"
                        ),
                    )
                if company is None:
                    # A free-mail sender with no company named: there is nothing
                    # to state a value about. Reported, never guessed at.
                    continue
                try:
                    if company_is_new:
                        # Nothing on file to conflict with, so confirmation
                        # would be theatre. Written directly and marked
                        # conversation-sourced.
                        write_company_value(
                            company, field, coerce_value(field, proposed.value),
                        )
                        deal_written.append(field)
                    elif queue_company_update(
                        db, company, field, proposed.value,
                        source_sentence=proposed.source_sentence,
                        source_entry_id=primary_log.id,
                    ) is not None:
                        deal_pending += 1
                except StatedValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))

            result_facts += deal_facts
            result_pending += deal_pending
            result_written.extend(deal_written)
            extra_direct.extend(deal_direct)
            extra_participation.extend(deal_participation)
            deal_logs.append(primary_log)
            deal_results.append(EmailEntryResult(
                id=primary_log.id,
                source_message_id=primary_log.source_message_id,
                company_id=primary_log.company_stamp_id,
                contact_id=primary_log.contact_id,
                action_taken=summary,
                source_note=spec.source_note,
                contact_email_ignored=ignored_contact_email,
                facts_written=deal_facts,
                pending_updates_created=deal_pending,
                company_values_written=deal_written,
                participant_entry_ids=deal_direct,
                participation_entry_ids=deal_participation,
            ))

        # Only when the email named a specific day. Nothing infers one.
        if primary_contact is not None and payload.next_touch_date is not None:
            primary_contact.next_touch_date = payload.next_touch_date

        # ── Attachments ───────────────────────────────────────────────────────
        # Filed once under <DOCUMENTS_FOLDER>/<year>, and recorded against every
        # deal's entry — the file arrived with all of them. Each row stores the
        # filename and the year, never a path. Nothing here routes into the
        # lease flow.
        for attachment in payload.attachments:
            if attachment.inline:
                # Signature images and embedded screenshots. Dropped entirely
                # on THIS path, not merely left unwritten — locked by
                # test_inline_images_are_excluded. The upload endpoint below
                # records a row instead, because there the caller has already
                # decided the file is worth sending.
                continue
            name = (attachment.filename or "").strip()
            if not name:
                continue

            # Too large to file. The row is still written, marked oversize, and
            # the ceiling is reported — one outsized video must not cost Jack
            # the record of the whole email.
            too_big = False
            if attachment.stored_path:
                try:
                    too_big = os.path.getsize(attachment.stored_path) > max_attachment_bytes()
                except OSError:
                    too_big = False

            if too_big:
                stored_name = sanitize_file_name(name)
                year = (log_date or date.today()).year
                stored_rel = None
            else:
                stored_name, year, stored = store_attachment(
                    name, attachment.stored_path, saved_on=log_date,
                )
                # store_attachment() writes to <folder>/<year>/<stored_name>,
                # so the relative path is those two joined — the absolute one
                # it used is never recorded.
                stored_rel = relative_stored_path(year, stored_name) if stored else None

            for deal_log in deal_logs:
                db.add(ActivityAttachment(
                    activity_log_id=deal_log.id,
                    file_name=stored_name,
                    stored_year=year,
                    stored_path=stored_rel,
                    oversize=too_big,
                    description=(attachment.description or None),
                    saved_date=deal_log.log_date or date.today(),
                ))
            result_attached += 1
            if too_big:
                result_oversize.append(stored_name)
            elif not stored_rel:
                result_missing.append(stored_name)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"An entry for message {payload.source_message_id} already exists.",
        )
    except Exception as exc:  # noqa: BLE001 — never half-create a thread
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not log email: {exc}")

    # One eager query for every entry written, not a lazy load per deal.
    ids = [r.id for r in deal_results]
    loaded = {
        row.id: row
        for row in db.query(ActivityLog).options(*_eager())
        .filter(ActivityLog.id.in_(ids)).all()
    }
    for result in deal_results:
        row = loaded[result.id]
        result.company_name = row.stamped_company.name if row.stamped_company else None
        result.contact_name = row.contact.name if row.contact else None

    out = ActivityFromEmailResult.model_validate(_to_out(loaded[ids[0]]).model_dump())
    out.facts_written = result_facts
    out.pending_updates_created = result_pending
    out.company_values_written = result_written
    out.attachments_saved = result_attached
    out.attachments_missing = result_missing
    out.attachments_oversize = result_oversize
    out.participant_entry_ids = extra_direct
    out.participation_entry_ids = extra_participation
    out.skipped_own_addresses = skipped_own
    out.entries = deal_results
    return out


class ActivityNoteUpdate(BaseModel):
    notes: str


@router.patch("/{entry_id}/notes", response_model=ActivityOut)
def update_activity_notes(
    entry_id: int,
    payload: ActivityNoteUpdate,
    db: Session = Depends(get_db),
):
    """Update (or set) the broker note on an activity log entry."""
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")
    log.notes = payload.notes
    if log.contact is not None:
        mark_engaged(db, log.contact)   # editing a field is engagement
    db.commit()
    db.refresh(log)
    return _to_out(log)


class ActivityEdit(BaseModel):
    """Any correctable field on an entry. Omitted fields are left unchanged.

    Everything the automation writes has to be correctable by hand, so this
    covers the prose, the direction and channel it guessed, the date it stamped
    and the discovery capture.

    Two fields are deliberately absent. contact_id moves through
    PATCH /{id}/assign, and company_stamp_id through PATCH /{id}/company-stamp —
    both are re-attachments rather than edits, and the stamp in particular is
    what keeps a departed contact's history on the old company's page.
    """
    action_type: Optional[str] = None
    action_taken: Optional[str] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None
    follow_up_action: Optional[str] = None
    subject: Optional[str] = None

    # What kind of touch it was, and when.
    direction: Optional[str] = None
    channel: Optional[str] = None
    log_date: Optional[date] = None

    # Discovery capture — stored and displayed, never consumed by scoring or
    # generation.
    disc_current_rent_psf:  Optional[float] = None
    disc_current_sf:        Optional[int]   = None
    disc_lease_expiry:      Optional[date]  = None
    disc_decision_timeline: Optional[str]   = None
    disc_buildout_needs:    Optional[str]   = None
    disc_decision_maker:    Optional[str]   = None
    # None means "omitted" above, so clearing a discovery field needs its own
    # signal. Names the fields to blank out.
    clear_fields: List[str] = []


# Editing any of these changes what the note says, so the intelligence layer
# has to re-read it. Stage/date/channel edits don't affect the extracted facts.
_TEXT_FIELDS = ("action_type", "action_taken", "outcome", "notes",
                "follow_up_action", "subject")

# Correctable but inert to the intelligence layer — changing one saves without
# paying for a re-mine.
_PLAIN_FIELDS = ("direction", "channel", "log_date",
                 "disc_current_rent_psf", "disc_current_sf", "disc_lease_expiry",
                 "disc_decision_timeline", "disc_buildout_needs",
                 "disc_decision_maker")

_CLEARABLE = set(_PLAIN_FIELDS) | {"outcome", "notes", "follow_up_action", "subject"}

VALID_DIRECTIONS = ("outbound", "inbound")
VALID_CHANNELS = ("email", "call", "meeting", "text", "linkedin", "other")


@router.patch("/{entry_id}", response_model=ActivityOut)
def edit_activity(
    entry_id: int,
    payload: ActivityEdit,
    db: Session = Depends(get_db),
):
    """Edit an activity log entry and re-sync the intelligence layer.

    When the text changes, the entry is re-mined so its extracted facts match
    what the note now says. Re-mining is best-effort: if it fails (no API
    credit, network), the edit is still saved and the entry is left queued for
    the next mining run rather than losing the user's change.
    """
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    if payload.direction is not None and payload.direction not in VALID_DIRECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"direction must be one of: {', '.join(VALID_DIRECTIONS)}",
        )
    if payload.channel is not None and payload.channel not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"channel must be one of: {', '.join(VALID_CHANNELS)}",
        )
    bad = [f for f in payload.clear_fields if f not in _CLEARABLE]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot clear: {', '.join(bad)}",
        )

    text_changed = False
    changed = False
    for field in _TEXT_FIELDS:
        value = getattr(payload, field)
        if value is not None and value != getattr(log, field):
            setattr(log, field, value)
            text_changed = changed = True
    for field in _PLAIN_FIELDS:
        value = getattr(payload, field)
        if value is not None and value != getattr(log, field):
            setattr(log, field, value)
            changed = True
    for field in payload.clear_fields:
        if getattr(log, field, None) is not None:
            setattr(log, field, None)
            changed = True
            if field in _TEXT_FIELDS:
                text_changed = True

    # contact_id and company_stamp_id are untouched by every branch above —
    # an edit corrects what an entry says, never who it belongs to.
    if not changed:
        return _to_out(log)

    db.commit()
    db.refresh(log)

    # Only a prose change invalidates the extracted facts; correcting a channel
    # or a date does not, and must not cost an API call.
    if not text_changed:
        return _to_out(log)

    try:
        from app.services.activity_intel_service import remine_activity_log
        remine_activity_log(log, db)
    except Exception as exc:  # noqa: BLE001 — never lose an edit over extraction
        import logging
        logging.getLogger(__name__).warning(
            "activity %s edited but re-mining failed (%s); queued for next run",
            entry_id, exc,
        )
        db.rollback()
        # Drop the "already mined" marker so the next mining run picks this
        # entry up; otherwise a failed re-mine would leave the facts stale
        # forever.
        try:
            from app.models.intel import IntelActivityExtraction
            db.query(IntelActivityExtraction).filter(
                IntelActivityExtraction.activity_log_id == entry_id
            ).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()

    db.refresh(log)
    return _to_out(log)


@router.delete("/{entry_id}")
def delete_activity(entry_id: int, db: Session = Depends(get_db)):
    """Delete an activity log entry and the facts the intelligence layer derived
    from it. Other Deal Radar data (companies, properties, other logs) is never
    touched."""
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    from app.services.activity_intel_service import purge_log_intel
    purged = purge_log_intel(db, entry_id)
    db.delete(log)
    db.commit()
    return {"deleted": entry_id, "intel_removed": purged}


class ActivityStageUpdate(BaseModel):
    stage: str
    next_touch_date: Optional[date] = None


@router.patch("/{entry_id}/stage", response_model=ActivityOut)
def update_activity_stage(
    entry_id: int,
    payload: ActivityStageUpdate,
    db: Session = Depends(get_db),
):
    """Move an activity entry to a new stage (any direction, current-state only).

    - Moving to 'Sent' clears next_touch_date so the entry leaves Re-engage Today.
    - Otherwise next_touch_date is set from the payload (may be null). Dormant /
      Not Interested expect a revisit date from the inline picker, but a missing
      date is tolerated rather than erroring.
    """
    if payload.stage not in VALID_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage '{payload.stage}'. Must be one of: {', '.join(VALID_STAGES)}",
        )
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    log.stage = payload.stage
    if payload.stage == "Sent":
        # Re-engaged — drop the reminder so it disappears from Re-engage Today.
        log.next_touch_date = None
    else:
        log.next_touch_date = payload.next_touch_date
    # Changing a stage or setting a next-touch date is engagement.
    if log.contact is not None:
        mark_engaged(db, log.contact)
    db.commit()
    db.refresh(log)
    return _to_out(log)


@router.post("/", response_model=ActivityOut)
def create_activity(payload: ActivityCreate, db: Session = Depends(get_db)):
    stage = payload.stage if payload.stage in VALID_STAGES else "Sent"

    # Duplicate provider message → a clear 409, never a 500 from the unique
    # index. This is the dedup guarantee the email automation relies on.
    if payload.source_message_id:
        existing = db.query(ActivityLog).filter(
            ActivityLog.source_message_id == payload.source_message_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"An entry for message {payload.source_message_id} already "
                    f"exists (activity id {existing.id})."
                ),
            )

    contact = None
    if payload.contact_id is not None:
        contact = db.query(Contact).filter(Contact.id == payload.contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")

    # Stamp the company the conversation was about. Falls back to the contact's
    # current employer when the caller did not say — and is never changed again.
    company_stamp_id = payload.company_stamp_id
    if company_stamp_id is None:
        company_stamp_id = payload.company_id or (contact.company_id if contact else None)

    # A manual inbound entry advances the stage exactly as an inbound email
    # does: responded=True, Sent -> Replied, never a regression from Interested,
    # In Play, Not Interested or Dormant. Logging a reply by hand used to leave
    # the header reading "They replied" while the pill still said Sent.
    if contact is not None:
        apply_inbound_stage_rules(contact, payload.direction)

    log = ActivityLog(
        log_date       = payload.log_date or date.today(),
        opportunity_id = payload.opportunity_id,
        property_id    = payload.property_id,
        company_id     = payload.company_id,
        contact_id     = payload.contact_id,
        company_stamp_id = company_stamp_id,
        action_type    = payload.action_type,
        action_taken   = payload.action_taken,
        outcome        = payload.outcome,
        stage          = stage,
        next_touch_date = None if stage == "Sent" else payload.next_touch_date,
        follow_up_date = payload.follow_up_date,
        follow_up_action = payload.follow_up_action,
        notes          = payload.notes,
        outreach_type  = payload.outreach_type,
        target_type    = payload.target_type,
        contact_method = payload.contact_method,
        subject        = payload.subject,
        direction      = (payload.direction or "outbound"),
        channel        = (payload.channel or _channel_for(payload.action_type)),
        source_message_id = payload.source_message_id,
        disc_current_rent_psf  = payload.disc_current_rent_psf,
        disc_current_sf        = payload.disc_current_sf,
        disc_lease_expiry      = payload.disc_lease_expiry,
        disc_decision_timeline = payload.disc_decision_timeline,
        disc_buildout_needs    = payload.disc_buildout_needs,
        disc_decision_maker    = payload.disc_decision_maker,
        created_by     = "user",
    )
    db.add(log)

    # Logging a call or a meeting against a contact is engagement — it triages
    # both the contact and their company, with no queue to work through.
    if contact is not None:
        mark_engaged(db, contact)

    try:
        db.commit()
    except IntegrityError:
        # Belt and braces: two concurrent posts can both pass the check above.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"An entry for message {payload.source_message_id} already exists.",
        )
    db.refresh(log)
    return _to_out(log)


# ── Contact-thread endpoints ─────────────────────────────────────────────────

class ActivityCompanyStamp(BaseModel):
    """Move an entry to a different company. company_id may be null to clear."""
    company_id: Optional[int] = None


@router.patch("/{entry_id}/company-stamp", response_model=ActivityOut)
def restamp_activity(
    entry_id: int, payload: ActivityCompanyStamp, db: Session = Depends(get_db),
):
    """Move one entry to a different company.

    Deliberately its own endpoint rather than a field on the ordinary edit.
    company_stamp_id records what a conversation was about at the time it
    happened — it is what keeps a departed contact's history on the old
    company's page while their personal thread stays whole. Changing it rewrites
    which company's timeline the entry appears on, so it is an explicit action
    behind a confirmation, never something an edit form changes in passing.
    """
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    if payload.company_id is not None:
        company = db.query(Company).filter(Company.id == payload.company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

    log.company_stamp_id = payload.company_id
    if log.contact is not None:
        mark_engaged(db, log.contact)
    db.commit()
    db.refresh(log)
    return _to_out(log)


def _reassignment_corrects_identity(db: Session, log: ActivityLog) -> bool:
    """True when moving this entry means its sender's address was filed under
    the wrong person — the only kind of move that should teach the resolver.

    That is the case only when the entry sits on the contact its sender_email
    currently resolves to (a correction Jack already made, then the address
    itself; nothing is created). Everything else corrects placement:

      * a deal entry — redirected to a per-deal contact, or left on the sender
        as a fallback. Ann's roundup covers seven deals; moving the Scott
        Management one says where that deal belongs, not who Ann is.
      * a To-recipient or Cc-participation row — it sits on someone other than
        the sender, and carries the sender's address only as provenance.
    """
    if log.deal_sourced or log.participation or log.contact_id is None:
        return False
    if not log.sender_email:
        return False
    resolved, _ = resolve_contact_for_address(db, log.sender_email, create=False)
    return resolved is not None and resolved.id == log.contact_id


@router.patch("/{entry_id}/assign", response_model=ActivityOut)
def assign_activity(
    entry_id: int, payload: ActivityAssign, db: Session = Depends(get_db),
):
    """Attach an existing entry to a contact.

    Serves three things: retroactive assignment (a March voicemail attached to
    Dana once you learn her name), manual correction of a wrong attachment, and
    "Move to contact" on an entry that has never been on anyone.

    company_stamp_id is set here only if the entry does not already have one:
    the stamp records what the conversation was about at the time, so assigning
    an old entry to a contact who has since changed jobs must not rewrite it.

    Facts sourced to the entry follow it, but only when the entry was
    contactless — see move_entry_to_contact. Moving an entry between two people
    leaves their facts where they are, exactly as before.
    """
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    contact = None
    if payload.contact_id is not None:
        contact = db.query(Contact).filter(Contact.id == payload.contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")

    # Decided BEFORE the entry moves: is this a correction of who the sender's
    # address belongs to, or only of where this entry sits?
    teaches = _reassignment_corrects_identity(db, log)

    if contact is not None:
        move_entry_to_contact(
            db, log, contact,
            fallback_company_stamp_id=payload.company_stamp_id,
        )
    else:
        # Detaching. Nothing to stamp from and nobody to engage.
        log.contact_id = None
        if log.company_stamp_id is None:
            log.company_stamp_id = payload.company_stamp_id or log.company_id
    if contact is not None:
        # The correction teaches the resolver. The address that produced the
        # wrong answer is mapped to the right person, and the next email from it
        # resolves there before any domain matching runs — so Jack never has to
        # make the same correction twice.
        #
        # An address Jack owns is never mapped: that would teach the resolver to
        # file his own mail under a contact, which is the exact thing the guard
        # in email_ingest_service exists to prevent.
        #
        # And only a correction of IDENTITY teaches — see
        # _reassignment_corrects_identity. Moving a roundup deal or a copied
        # row corrects placement, and must never remap the sender.
        if teaches:
            record_address_override(db, log.sender_email, contact, source_entry_id=log.id)
    db.commit()
    db.refresh(log)
    return _to_out(log)


# ══ Attachment files ══════════════════════════════════════════════════════════
# The ingestion task has an attachment's BYTES, not a file on Jack's disk, which
# is why every row written before this endpoint existed resolved to nothing and
# the interface said "(file missing)" for all of them. These three endpoints
# close that: bytes in, a file on disk, a link that opens it, and a check that
# says which rows have lost their file.


class AttachmentUploadRequest(BaseModel):
    """An attachment's bytes, base64-encoded, against the entry it arrived on.

    Base64 rather than multipart because the caller is a mailbox task holding
    bytes in memory, not a browser posting a form.
    """
    entry_id: int
    filename: str
    # Standard base64. A data-URL prefix ("data:...;base64,") is tolerated —
    # the caller should not have to know which one it produced.
    content_base64: str
    year: Optional[int] = None
    description: Optional[str] = None
    # A signature image or embedded screenshot: recorded, never written.
    inline: bool = False


class AttachmentUploadResult(BaseModel):
    """What became of one attachment. Never an error for a file that failed.

    `status` is "stored", "oversize", "inline" or "no_file". The row exists in
    every one of them; only "stored" wrote a file. The caller reports oversize
    to Jack rather than retrying, because a retry would hit the same ceiling.
    """
    id: int
    entry_id: int
    file_name: str
    stored_year: int
    status: str
    stored: bool
    oversize: bool
    missing: bool
    # Present only when a file was written. Relative to the documents folder —
    # the absolute path is never returned, for the same reason it is never
    # stored.
    stored_path: Optional[str] = None
    size_bytes: Optional[int] = None
    max_bytes: int
    detail: Optional[str] = None


@router.post("/attachments/upload", response_model=AttachmentUploadResult)
def upload_attachment(
    payload: AttachmentUploadRequest, db: Session = Depends(get_db),
):
    """Write an attachment's bytes to disk and record it against an entry.

    A bad entry id is the one genuine 404 here: there is nothing to attach to,
    and silently inventing a row would hide the caller's bug. Everything else —
    a file too large, an inline image, a write that fails — records the row and
    reports what happened, because the entry must survive its attachments.
    """
    entry = db.query(ActivityLog).filter(ActivityLog.id == payload.entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    name = (payload.filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="filename is required")

    saved_on = entry.log_date or date.today()
    year = payload.year if payload.year else saved_on.year

    # An inline image is recorded and never written — see AttachmentUploadRequest.
    if payload.inline:
        row = ActivityAttachment(
            activity_log_id=entry.id,
            file_name=sanitize_file_name(name),
            stored_year=int(year),
            stored_path=None,
            oversize=False,
            description=(payload.description or None),
            saved_date=saved_on,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return AttachmentUploadResult(
            id=row.id, entry_id=entry.id, file_name=row.file_name,
            stored_year=row.stored_year, status="inline", stored=False,
            oversize=False, missing=True, stored_path=None, size_bytes=None,
            max_bytes=max_attachment_bytes(),
            detail="Inline image recorded; the file was not saved.",
        )

    raw = (payload.content_base64 or "").strip()
    if raw[:5].lower() == "data:" and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        contents = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64")

    display, stored_rel, resolved_year, status = store_attachment_bytes(
        entry.id, name, contents, year=year, saved_on=saved_on,
    )

    row = ActivityAttachment(
        activity_log_id=entry.id,
        file_name=display,
        stored_year=resolved_year,
        stored_path=stored_rel,
        oversize=(status == OVERSIZE),
        description=(payload.description or None),
        saved_date=saved_on,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    detail = None
    if status == OVERSIZE:
        detail = (
            f"{display} is {len(contents)} bytes, over the "
            f"{max_attachment_bytes()}-byte limit. Recorded without the file."
        )
    elif status == NO_FILE:
        detail = f"{display} could not be written. Recorded without the file."

    return AttachmentUploadResult(
        id=row.id,
        entry_id=entry.id,
        file_name=row.file_name,
        stored_year=row.stored_year,
        status=status,
        stored=(status == STORED),
        oversize=(status == OVERSIZE),
        missing=not stored_path_exists(row.stored_path),
        stored_path=row.stored_path,
        size_bytes=len(contents),
        max_bytes=max_attachment_bytes(),
        detail=detail,
    )


@router.get("/attachments/{attachment_id}/file")
def get_attachment_file(attachment_id: int, db: Session = Depends(get_db)):
    """Open a stored attachment. 404 when there is no file behind the row.

    A row with no file is not an error in the data — an inline image and an
    oversize file are both recorded on purpose — so the detail says which it is
    rather than implying something is broken.
    """
    row = (
        db.query(ActivityAttachment)
        .filter(ActivityAttachment.id == attachment_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if row.oversize:
        raise HTTPException(
            status_code=404,
            detail=f"{row.file_name} was too large to store, so there is no file to open.",
        )

    path = resolve_stored_path(row.stored_path)
    # Rows written before stored_path existed still resolve the old way, so
    # anything already filed under <folder>/<year>/<file_name> keeps opening.
    if not path or not os.path.isfile(path):
        path = resolve_attachment_path(row.file_name, row.stored_year)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail=f"{row.file_name} is recorded, but the file is not in the documents folder.",
        )

    return FileResponse(path, filename=row.file_name)


class MissingAttachment(BaseModel):
    id: int
    entry_id: int
    file_name: str
    stored_year: int
    stored_path: Optional[str] = None
    oversize: bool = False
    # Why there is no file, in the terms Jack would use: a file that was
    # written and has since gone, versus one that was never written at all.
    reason: str


class MissingAttachmentReport(BaseModel):
    checked: int
    missing: int
    attachments: List[MissingAttachment] = []


@router.get("/attachments/missing-files", response_model=MissingAttachmentReport)
def report_missing_attachment_files(
    db: Session = Depends(get_db),
    include_never_stored: bool = Query(
        False,
        description=(
            "Also report rows that never had a file (inline images, oversize "
            "files, rows predating stored_path). Off by default: those are not "
            "breakage, and burying the real losses among them helps nobody."
        ),
    ),
):
    """Report every attachment row whose stored file is no longer on disk.

    The question this answers is "what have I lost?", so by default it reports
    only rows that HAD a file — a stored_path that no longer resolves. That is
    a document that went missing, which is worth acting on; an inline image
    with no file is working as intended.
    """
    rows = db.query(ActivityAttachment).order_by(ActivityAttachment.id.asc()).all()
    missing: List[MissingAttachment] = []

    for row in rows:
        if row.stored_path:
            if stored_path_exists(row.stored_path):
                continue
            reason = "stored file is no longer on disk"
        else:
            if not include_never_stored:
                continue
            if row.oversize:
                reason = "too large to store; recorded without a file"
            elif attachment_file_exists(row.file_name, row.stored_year):
                # Filed the old way, before stored_path. Not missing.
                continue
            else:
                reason = "no file was ever stored for this row"

        missing.append(MissingAttachment(
            id=row.id,
            entry_id=row.activity_log_id,
            file_name=row.file_name,
            stored_year=row.stored_year,
            stored_path=row.stored_path,
            oversize=bool(row.oversize),
            reason=reason,
        ))

    return MissingAttachmentReport(
        checked=len(rows), missing=len(missing), attachments=missing,
    )
