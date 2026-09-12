import re
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.services.contact_service import (
    apply_inbound_stage_rules, email_domain_of, mark_engaged, normalize_email,
    resolve_company_for_email, resolve_or_create_contact,
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
    limit: int = Query(1000, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(ActivityLog).options(*_eager())
    if since:
        q = q.filter(ActivityLog.log_date >= since)
    if action_type:
        q = q.filter(ActivityLog.action_type == action_type)
    if stage:
        q = q.filter(ActivityLog.stage == stage)
    logs = q.order_by(ActivityLog.created_at.desc()).limit(limit).all()
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
            .filter(ActivityLog.contact_id == contact.id)
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


class ActivityFromEmail(BaseModel):
    """One email, as the scheduled mailbox check sees it."""
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


@router.post("/from-email", response_model=ActivityOut)
def create_activity_from_email(
    payload: ActivityFromEmail, db: Session = Depends(get_db),
):
    """Log an email, resolving or creating its contact and company.

    Everything happens in one transaction: resolve or create the contact by
    email address, resolve or create the company by email domain, stamp
    company_stamp_id, create the entry. A failure anywhere rolls the whole thing
    back rather than leaving a contact with no entry.

    The automation's own spam and noise filter is the only gate. If an email is
    clean enough to log, its sender is clean enough to become a contact — there
    is deliberately no second relevance filter here.
    """
    direction = (payload.direction or "outbound").lower()
    if direction not in ("outbound", "inbound"):
        raise HTTPException(
            status_code=400,
            detail="direction must be 'outbound' or 'inbound'",
        )

    # Idempotency: a second POST with the same message id is a no-op, not a
    # duplicate row and not a 500.
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

    # On an inbound mail the counterpart is the sender; on an outbound one it is
    # the recipient. Either way the address is the identity, never the name.
    counterpart_email = (
        payload.from_email if direction == "inbound" else (payload.to_email or payload.from_email)
    )
    counterpart_name = payload.from_name if direction == "inbound" else None

    try:
        # Free-mail senders get no company — never a company called "Gmail".
        company, _company_created = resolve_company_for_email(
            db, counterpart_email, counterpart_name,
        )
        contact, _contact_created = resolve_or_create_contact(
            db, counterpart_email, counterpart_name, company=company,
        )

        if contact is not None:
            # inbound → responded=True, and Sent → Replied only. A contact
            # already at Interested or In Play is never regressed.
            apply_inbound_stage_rules(contact, direction)

        summary = (payload.action_taken or "").strip()
        if not summary:
            who = (contact.name if contact else None) or counterpart_email or "contact"
            verb = "Received email from" if direction == "inbound" else "Emailed"
            summary = f"{verb} {who}" + (f" re {payload.subject}" if payload.subject else "")

        log = ActivityLog(
            log_date       = payload.sent_at or date.today(),
            company_id     = company.id if company else None,
            contact_id     = contact.id if contact else None,
            company_stamp_id = company.id if company else None,
            action_type    = "EMAIL",
            action_taken   = summary,
            outcome        = payload.outcome,
            subject        = payload.subject,
            follow_up_action = payload.follow_up_action,
            stage          = "Sent",
            direction      = direction,
            channel        = "email",
            source_message_id = payload.source_message_id,
            contact_method = "email",
            created_by     = "email-automation",
        )
        db.add(log)
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

    db.refresh(log)
    return _to_out(log)


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
    """Any freeform field on an entry. Omitted fields are left unchanged."""
    action_type: Optional[str] = None
    action_taken: Optional[str] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None
    follow_up_action: Optional[str] = None
    subject: Optional[str] = None


# Editing any of these changes what the note says, so the intelligence layer
# has to re-read it. Stage/date edits don't affect the extracted facts.
_TEXT_FIELDS = ("action_type", "action_taken", "outcome", "notes",
                "follow_up_action", "subject")


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

    changed = False
    for field in _TEXT_FIELDS:
        value = getattr(payload, field)
        if value is not None and value != getattr(log, field):
            setattr(log, field, value)
            changed = True
    if not changed:
        return _to_out(log)

    db.commit()
    db.refresh(log)

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

@router.patch("/{entry_id}/assign", response_model=ActivityOut)
def assign_activity(
    entry_id: int, payload: ActivityAssign, db: Session = Depends(get_db),
):
    """Attach an existing entry to a contact.

    Serves both retroactive assignment (a March voicemail attached to Dana once
    you learn her name) and manual correction of a wrong attachment.

    company_stamp_id is set here only if the entry does not already have one:
    the stamp records what the conversation was about at the time, so assigning
    an old entry to a contact who has since changed jobs must not rewrite it.
    """
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    contact = None
    if payload.contact_id is not None:
        contact = db.query(Contact).filter(Contact.id == payload.contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")

    log.contact_id = payload.contact_id
    if log.company_stamp_id is None:
        log.company_stamp_id = (
            payload.company_stamp_id
            or log.company_id
            or (contact.company_id if contact else None)
        )
    if contact is not None:
        mark_engaged(db, contact)
    db.commit()
    db.refresh(log)
    return _to_out(log)
