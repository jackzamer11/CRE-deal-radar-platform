"""Contact threads — a Contact is a first-class record that owns its pipeline
stage and next-touch date; activity entries attach to it.

Route-ordering note: every static path here (/search, /resolve, /facts/...,
/conflicts/...) is declared BEFORE the /{contact_id} routes. contact_id is an
int, so a static path declared after them would be parsed as an id and 422
instead of matching. Same reason /activity/re-engage sits above /activity/{id}.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import (
    Contact, ContactFact, CLOSED_STAGE, CONTACT_STAGES, CONTACT_TYPES,
)
from app.models.email_ingest import ActivityAttachment
from app.services.attachment_storage import attachment_file_exists
from app.api.routes.pending_updates import pending_updates_for_company
from app.schemas.company import months_until_lease_expiry
from app.schemas.pending_update import PendingUpdateOut
from app.services.contact_service import (
    STAGE_CHANGE_ACTION, active_facts, apply_closed_stage_bookkeeping,
    create_fact, mark_engaged, normalize_email, record_stage_change,
    resolve_contact_by_email,
)
from app.services.lease_records import current_lease
from app.services.lease_storage import lease_file_exists
from app.services.signal_engine import (
    is_in_peak_expiry_window, peak_window_date_bounds,
)

router = APIRouter(prefix="/contacts", tags=["contacts"])

# The three Company fields a contact's claim can contradict. Each maps the
# conversation-sourced column to the verified column it would overwrite — and
# it only ever overwrites on an explicit accept.
CONFLICT_FIELDS = {
    "lease_expiry": {
        "reported":  "contact_reported_lease_expiry",
        "verified":  "lease_expiry_date",
        "label":     "lease expiry",
    },
    "rent_psf": {
        "reported":  "contact_reported_rent_psf",
        "verified":  "current_rent_psf",
        "label":     "current rent $/SF",
    },
    "sf": {
        "reported":  "contact_reported_sf",
        "verified":  "current_sf_occupied",
        "label":     "square footage",
    },
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class ContactOut(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    company_id: Optional[int] = None
    contact_type: str = "tenant"
    stage: str = "Sent"
    stage_changed_at: Optional[date] = None
    # Set when the stage moves to Closed, cleared when it moves off.
    closed_at: Optional[date] = None
    next_touch_date: Optional[date] = None
    responded: bool = False
    # True once Jack has placed this tenant, permanently. Never cleared by
    # moving off Closed.
    is_past_client: bool = False
    triaged: bool = False
    auto_created: bool = False
    company_name: Optional[str] = None

    class Config:
        from_attributes = True


class ContactListRow(BaseModel):
    """One row per contact for the By Contact list.

    Every field here comes out of a single aggregated query — there is no
    per-contact follow-up query, so the endpoint does not degrade as contacts
    accumulate.
    """
    id: int
    name: str
    email: Optional[str] = None
    title: Optional[str] = None
    contact_type: str = "tenant"
    stage: str = "Sent"
    stage_changed_at: Optional[date] = None
    closed_at: Optional[date] = None
    days_in_stage: Optional[int] = None
    next_touch_date: Optional[date] = None
    overdue: bool = False
    responded: bool = False
    triaged: bool = False
    auto_created: bool = False
    # The past-client marker. is_past_client says Jack placed them once;
    # past_client_reentry says their company's lease has come back around into
    # the 6-9 month window, which is why a Closed contact is on this list at
    # all. "You placed this tenant in this building" is the strongest opening
    # line available on that call, so the row has to say so.
    is_past_client: bool = False
    past_client_reentry: bool = False
    lease_expiry_months: Optional[int] = None
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    # Real correspondence only. A copied recipient counts toward copied_count
    # instead, and copied_only says the two add up to "on the Cc line, never
    # written to" — which is what their row has to read as.
    entry_count: int = 0
    copied_count: int = 0
    copied_only: bool = False
    latest_entry_date: Optional[date] = None
    latest_entry_summary: Optional[str] = None
    latest_entry_channel: Optional[str] = None


class FactOut(BaseModel):
    id: int
    contact_id: int
    fact_text: str
    source_entry_id: Optional[int] = None
    learned_date: date
    superseded_by_id: Optional[int] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class TimelineAttachment(BaseModel):
    """One file that arrived on an ingested email.

    file_name plus the year is all the database holds; the absolute path is
    resolved from settings.DOCUMENTS_FOLDER at read time. `missing` says the
    file is not where it should be, so the UI can say so plainly instead of
    offering a link that does nothing.
    """
    id: int
    file_name: str
    stored_year: int
    description: Optional[str] = None
    saved_date: Optional[date] = None
    missing: bool = False

    class Config:
        from_attributes = True


class TimelineEntry(BaseModel):
    id: int
    log_date: date
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    company_stamp_id: Optional[int] = None
    company_stamp_name: Optional[str] = None
    action_type: str
    action_taken: str
    outcome: Optional[str] = None
    notes: Optional[str] = None
    follow_up_action: Optional[str] = None
    direction: Optional[str] = "outbound"
    channel: Optional[str] = "other"
    outreach_type: Optional[str] = None
    subject: Optional[str] = None
    # True when this person was only copied. Rendered distinctly — it is
    # history on their thread, not correspondence with them.
    participation: Optional[bool] = False
    # Files that arrived on the email. Filename and description only; the path
    # is resolved from settings at read time, never stored.
    attachments: List["TimelineAttachment"] = []
    # Set only on a STAGE_CHANGE row — the transition the divider renders.
    stage_from: Optional[str] = None
    stage_to: Optional[str] = None
    # Discovery capture — displayed, never consumed by scoring or generation.
    disc_current_rent_psf: Optional[float] = None
    disc_current_sf: Optional[int] = None
    disc_lease_expiry: Optional[date] = None
    disc_decision_timeline: Optional[str] = None
    disc_buildout_needs: Optional[str] = None
    disc_decision_maker: Optional[str] = None

    class Config:
        from_attributes = True


class TimelinePage(BaseModel):
    """Paginated, never capped. The July Activity Log bug was a hard backend
    ceiling below what the frontend asked for — there is no `le=` here."""
    total: int
    limit: int
    offset: int
    entries: List[TimelineEntry]


class ConflictOut(BaseModel):
    field: str
    label: str
    company_id: int
    company_name: str
    reported_value: Optional[str] = None
    verified_value: Optional[str] = None
    reported_at: Optional[date] = None
    source_entry_id: Optional[int] = None
    resolution: Optional[str] = None


class ThreadHeader(BaseModel):
    """The three header slots, in the order the thread renders them.

    1. Where we are  — stage, days in stage, last touch, days of silence, open loop
    2. Relationship  — prose composed from active facts, each clickable to source
    3. Deal context  — company facts plus the conflict marker
    """
    contact: ContactOut
    # Slot 1
    days_in_stage: Optional[int] = None
    last_touch_date: Optional[date] = None
    last_touch_channel: Optional[str] = None
    days_of_silence: Optional[int] = None
    open_loop: Optional[str] = None
    entry_count: int = 0
    # Emails this person was only copied on. They are on the timeline as
    # history and count toward nothing; copied_only means every entry on the
    # thread is one of these, which the header renders as "copied, never
    # directly contacted".
    copied_count: int = 0
    copied_only: bool = False
    # Slot 2 — two lines of prose, each with the entry it came from
    relationship_lines: List[dict] = []
    facts: List[FactOut] = []
    # Slot 3
    company_name: Optional[str] = None
    company_business_id: Optional[str] = None
    company_lease_expiry: Optional[date] = None
    company_lease_expiry_months: Optional[int] = None
    company_sf: Optional[int] = None
    company_submarket: Optional[str] = None
    company_address: Optional[str] = None
    # Which of the three fields above came off the signed lease rather than
    # CoStar. A lease outranks CoStar, so the Deal card says which is which.
    company_lease_expiry_source: Optional[str] = None
    company_address_source: Optional[str] = None
    company_sf_source: Optional[str] = None
    # The company's CURRENT lease. lease_file_name is a bare filename; the link
    # the card renders goes through the backend, which resolves it against the
    # configured folder and says so plainly when the file is not there.
    company_lease_file_name: Optional[str] = None
    company_lease_uploaded_at: Optional[datetime] = None
    company_lease_file_missing: bool = False
    has_lease_extraction: bool = False
    # Past-client re-entry, same meaning as on the list row.
    past_client_reentry: bool = False
    has_data_conflict: bool = False
    conflicts: List[ConflictOut] = []
    # Values an email stated about this company, awaiting Jack's call. Same
    # shape and the same panel as `conflicts` above — both values side by side
    # with where the claim came from — because they are the same decision.
    pending_updates: List[PendingUpdateOut] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_between(earlier: Optional[date], later: Optional[date] = None) -> Optional[int]:
    if not earlier:
        return None
    return ((later or date.today()) - earlier).days


def _contact_out(contact: Contact, company_name: Optional[str] = None) -> ContactOut:
    out = ContactOut.model_validate(contact)
    if company_name is not None:
        out.company_name = company_name
    elif contact.company is not None:
        out.company_name = contact.company.name
    return out


def _company_expiry_months(company: Optional[Company]) -> Optional[int]:
    """Months until this company's lease expires.

    Re-derived from lease_expiry_date whenever there is one — the stored
    lease_expiry_months column goes stale relative to the date (a direct edit,
    or a confirmed lease extraction writing a new expiry), and the queue must
    not surface a past client on a number that is a year out of date.
    """
    if company is None:
        return None
    if company.lease_expiry_date:
        return months_until_lease_expiry(company.lease_expiry_date)
    return company.lease_expiry_months


def _is_past_client_reentry(
    contact: Optional[Contact], lease_expiry_months: Optional[int],
) -> bool:
    """True when a past client's company has come back into the 6-9 month window.

    The window test is signal_engine.is_in_peak_expiry_window() — the same
    function behind the scoring tier, so the queue and the score can never
    disagree about what "the window" means.
    """
    if contact is None or not contact.is_past_client:
        return False
    return is_in_peak_expiry_window(lease_expiry_months)


def _get_contact(db: Session, contact_id: int) -> Contact:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _fmt(value) -> Optional[str]:
    """Render a conflict value for display without losing precision."""
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def pending_conflicts(db: Session, company: Optional[Company]) -> List[ConflictOut]:
    """Claims still awaiting Jack's call on this company.

    A claim is pending when a contact reported a value, it differs from the
    verified value, and Jack has neither accepted nor rejected it. Accepting
    copies the value across (so it stops differing); rejecting sets a resolution
    so it never re-prompts.
    """
    if company is None:
        return []
    out: List[ConflictOut] = []
    for key, spec in CONFLICT_FIELDS.items():
        reported = getattr(company, spec["reported"], None)
        if reported is None:
            continue
        resolution = getattr(company, f"{spec['reported']}_resolution", None)
        if resolution:
            continue
        verified = getattr(company, spec["verified"], None)
        if verified is not None and str(verified) == str(reported):
            continue
        out.append(ConflictOut(
            field=key,
            label=spec["label"],
            company_id=company.id,
            company_name=company.name,
            reported_value=_fmt(reported),
            verified_value=_fmt(verified),
            reported_at=getattr(company, f"{spec['reported']}_reported_at", None),
            source_entry_id=getattr(company, f"{spec['reported']}_source_entry_id", None),
            resolution=None,
        ))
    return out


# ══ Static paths — must precede /{contact_id} ═════════════════════════════════

@router.get("/search", response_model=List[ContactOut])
def search_contacts(
    q: str = Query("", description="Type-ahead over name and email"),
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Type-ahead on name and email. Untriaged contacts are included — they are
    fully searchable from the moment they exist, they just don't fill the
    default list."""
    term = (q or "").strip()
    if not term:
        return []
    like = f"%{term.lower()}%"
    rows = (
        db.query(Contact)
        .options(joinedload(Contact.company))
        .filter(or_(
            func.lower(Contact.name).like(like),
            func.lower(Contact.email).like(like),
        ))
        .order_by(Contact.name.asc())
        .limit(max(1, limit))
        .all()
    )
    return [_contact_out(c) for c in rows]


class ResolveRequest(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None


class ResolveResponse(BaseModel):
    found: bool
    contact: Optional[ContactOut] = None


@router.post("/resolve", response_model=ResolveResponse)
def resolve_contact(payload: ResolveRequest, db: Session = Depends(get_db)):
    """Given an email address and display name, return the matching contact or
    indicate none. Used by the email automation.

    Matching is on email only — exact, case-insensitive. The display name is
    accepted so callers can pass what they have, but it is never used to match:
    two people called "Mike Johnson" are two people.
    """
    contact = resolve_contact_by_email(db, payload.email)
    if not contact:
        return ResolveResponse(found=False, contact=None)
    return ResolveResponse(found=True, contact=_contact_out(contact))


class FactCreate(BaseModel):
    contact_id: int
    fact_text: str
    source_entry_id: Optional[int] = None
    learned_date: Optional[date] = None
    # When set, the named fact is marked superseded rather than deleted.
    supersedes_id: Optional[int] = None


@router.post("/facts", response_model=FactOut)
def add_fact(payload: FactCreate, db: Session = Depends(get_db)):
    """Record a durable thing learned about a person. Adding a fact is
    engagement, so it triages the contact and their company."""
    text = (payload.fact_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="fact_text cannot be empty")
    contact = _get_contact(db, payload.contact_id)
    fact = create_fact(
        db, contact, text,
        source_entry_id=payload.source_entry_id,
        learned_date=payload.learned_date,
        supersedes_id=payload.supersedes_id,
    )
    db.commit()
    db.refresh(fact)
    return FactOut.model_validate(fact)


class SupersedeRequest(BaseModel):
    fact_text: str
    source_entry_id: Optional[int] = None
    learned_date: Optional[date] = None


@router.post("/facts/{fact_id}/supersede", response_model=FactOut)
def supersede_fact(
    fact_id: int, payload: SupersedeRequest, db: Session = Depends(get_db),
):
    """Replace a fact with a newer one that contradicts it.

    The old fact is marked superseded, not deleted — it stops appearing in the
    active set but stays retrievable, so what was believed when survives.
    """
    text = (payload.fact_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="fact_text cannot be empty")
    old = db.query(ContactFact).filter(ContactFact.id == fact_id).first()
    if not old:
        raise HTTPException(status_code=404, detail="Fact not found")
    contact = _get_contact(db, old.contact_id)
    new_fact = create_fact(
        db, contact, text,
        source_entry_id=payload.source_entry_id,
        learned_date=payload.learned_date,
        supersedes_id=old.id,
    )
    db.commit()
    db.refresh(new_fact)
    return FactOut.model_validate(new_fact)


class FactEdit(BaseModel):
    """Correct a fact's wording in place."""
    fact_text: str
    learned_date: Optional[date] = None


@router.patch("/facts/{fact_id}", response_model=FactOut)
def edit_fact(fact_id: int, payload: FactEdit, db: Session = Depends(get_db)):
    """Edit a fact's text in place.

    Distinct from superseding: this is for a fact that was typed wrong, not one
    that stopped being true. Nothing is archived because there was never a
    second version — the correction replaces the mistake.
    """
    text = (payload.fact_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="fact_text cannot be empty")
    fact = db.query(ContactFact).filter(ContactFact.id == fact_id).first()
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")
    fact.fact_text = text
    if payload.learned_date is not None:
        fact.learned_date = payload.learned_date
    contact = db.query(Contact).filter(Contact.id == fact.contact_id).first()
    if contact is not None:
        mark_engaged(db, contact)   # correcting a fact is engagement
    db.commit()
    db.refresh(fact)
    return FactOut.model_validate(fact)


@router.delete("/facts/{fact_id}")
def delete_fact(fact_id: int, db: Session = Depends(get_db)):
    """One-click delete. Unlike superseding, this removes the row outright —
    it is for a fact that was wrong, not one that stopped being true."""
    fact = db.query(ContactFact).filter(ContactFact.id == fact_id).first()
    if not fact:
        raise HTTPException(status_code=404, detail="Fact not found")
    # Anything this fact superseded goes back to active — otherwise deleting a
    # mistaken correction would silently bury the fact it replaced.
    restored = (
        db.query(ContactFact)
        .filter(ContactFact.superseded_by_id == fact.id)
        .all()
    )
    for prior in restored:
        prior.superseded_by_id = None
        prior.is_active = True
    db.delete(fact)
    db.commit()
    return {"deleted": fact_id, "restored": [p.id for p in restored]}


@router.get("/facts", response_model=List[FactOut])
def list_facts(
    contact_id: int,
    include_superseded: bool = False,
    db: Session = Depends(get_db),
):
    """Facts for a contact. Superseded ones are excluded unless asked for —
    they remain retrievable, which is the point of superseding over deleting."""
    q = db.query(ContactFact).filter(ContactFact.contact_id == contact_id)
    if not include_superseded:
        q = q.filter(ContactFact.is_active.is_(True))
    rows = q.order_by(
        ContactFact.learned_date.desc(), ContactFact.id.desc()
    ).all()
    return [FactOut.model_validate(f) for f in rows]


# ── Conflicts ────────────────────────────────────────────────────────────────

class ConflictReport(BaseModel):
    """Record what a contact claimed. Writes ONLY to the conversation-sourced
    column — never to the verified field, which moves only on an accept."""
    field: str
    value: str
    source_entry_id: Optional[int] = None
    reported_at: Optional[date] = None


def _get_company(db: Session, company_pk: int) -> Company:
    company = db.query(Company).filter(Company.id == company_pk).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _coerce(field: str, raw: str):
    """Parse a reported value into the verified column's type."""
    try:
        if field == "lease_expiry":
            return date.fromisoformat(str(raw).strip()[:10])
        if field == "rent_psf":
            return float(raw)
        if field == "sf":
            return int(float(raw))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse '{raw}' as a value for {field}",
        )
    raise HTTPException(status_code=400, detail=f"Unknown conflict field '{field}'")


@router.get("/conflicts/{company_pk}", response_model=List[ConflictOut])
def get_conflicts(company_pk: int, db: Session = Depends(get_db)):
    """Claims still awaiting Jack's call. Empty list, never a 500, when there
    are none or the company has no reported values."""
    return pending_conflicts(db, _get_company(db, company_pk))


@router.post("/conflicts/{company_pk}/report", response_model=List[ConflictOut])
def report_conflict(
    company_pk: int, payload: ConflictReport, db: Session = Depends(get_db),
):
    """Record a contact-reported value against a company.

    Lease expiry, headcount, growth rate and square footage never write
    silently — this stores the claim alongside the verified value and surfaces
    it as a one-tap confirmation at the top of the thread. Automated proposal
    generation comes later; this is the endpoint it will call.
    """
    if payload.field not in CONFLICT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown field '{payload.field}'. One of: {', '.join(CONFLICT_FIELDS)}",
        )
    company = _get_company(db, company_pk)
    spec = CONFLICT_FIELDS[payload.field]
    setattr(company, spec["reported"], _coerce(payload.field, payload.value))
    setattr(company, f"{spec['reported']}_source_entry_id", payload.source_entry_id)
    setattr(company, f"{spec['reported']}_reported_at", payload.reported_at or date.today())
    # A fresh claim re-opens the question even if an earlier one was resolved.
    setattr(company, f"{spec['reported']}_resolution", None)
    db.commit()
    db.refresh(company)
    return pending_conflicts(db, company)


@router.post("/conflicts/{company_pk}/{field}/accept", response_model=List[ConflictOut])
def accept_conflict(company_pk: int, field: str, db: Session = Depends(get_db)):
    """Accept the contact's value: copy it onto the verified company field.

    This is the ONLY path from a conversational claim to a scoring field.
    """
    if field not in CONFLICT_FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown field '{field}'")
    company = _get_company(db, company_pk)
    spec = CONFLICT_FIELDS[field]
    reported = getattr(company, spec["reported"], None)
    if reported is None:
        raise HTTPException(
            status_code=400,
            detail=f"No reported {spec['label']} on this company to accept",
        )
    setattr(company, spec["verified"], reported)
    setattr(company, f"{spec['reported']}_resolution", "accepted")
    company.last_modified_by_user = datetime.utcnow()
    # Accepting settles the question, so this particular disagreement is over.
    if not pending_conflicts(db, company):
        company.has_data_conflict = False
    db.commit()
    db.refresh(company)
    return pending_conflicts(db, company)


@router.post("/conflicts/{company_pk}/{field}/reject", response_model=List[ConflictOut])
def reject_conflict(company_pk: int, field: str, db: Session = Depends(get_db)):
    """Reject the contact's value: leave the verified field alone.

    The claim stays on the contact's thread and the company gets a conflict
    marker. A tenant who believes their lease ends a year later than the record
    is itself a lead — a renewal option, a sublease, a phased expiry — so the
    disagreement is surfaced, not discarded.
    """
    if field not in CONFLICT_FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown field '{field}'")
    company = _get_company(db, company_pk)
    spec = CONFLICT_FIELDS[field]
    if getattr(company, spec["reported"], None) is None:
        raise HTTPException(
            status_code=400,
            detail=f"No reported {spec['label']} on this company to reject",
        )
    setattr(company, f"{spec['reported']}_resolution", "rejected")
    company.has_data_conflict = True
    db.commit()
    db.refresh(company)
    return pending_conflicts(db, company)


# ══ Collection routes ═════════════════════════════════════════════════════════

@router.get("/", response_model=List[ContactListRow])
def list_contacts(
    contact_type: Optional[str] = None,
    triaged: Optional[bool] = None,
    responded: Optional[bool] = None,
    stage: Optional[str] = None,
    include_closed: bool = False,
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """One row per contact, with entry counts and latest-entry summary.

    Computed in a single aggregated query — a LEFT JOIN with GROUP BY for the
    counts and latest date, plus one bulk fetch for the latest entry's text. No
    per-contact query loop, so this does not degrade as contacts accumulate.
    Adding the stage and past-client filters kept that shape: both are WHERE
    clauses on the same query.

    Default sort: overdue next-touch first (soonest due first), then most
    recent activity. Paginated with no hard ceiling.

    Closed contacts are excluded by default — a placed deal is not work in the
    queue — and come back three ways: `stage=Closed`, `include_closed=true`, or
    by being a past client whose company has re-entered the 6-9 month window.
    That last one is the point of the whole mechanic: they reappear on their new
    expiry with their stage still Closed and a past-client marker, never reset
    to Sent. Search (/contacts/search) reaches them regardless.
    """
    # Stage-change dividers are excluded throughout: entry count means real
    # touches, and a row reading "Stage: Sent -> Replied" is not the last thing
    # that happened with this person.
    #
    # Participation entries are excluded for the same reason: someone copied on
    # ten emails has no relationship with Jack, and a row that counted them
    # would put "last touch: yesterday" on a person he has never written to.
    # The count of them is selected separately so the row can still say
    # "copied, never directly contacted" rather than showing an empty thread.
    counts = (
        db.query(
            ActivityLog.contact_id.label("cid"),
            func.count(ActivityLog.id).label("entry_count"),
            func.max(ActivityLog.log_date).label("latest_date"),
        )
        .filter(
            ActivityLog.contact_id.isnot(None),
            ActivityLog.action_type != STAGE_CHANGE_ACTION,
            # .isnot(True) rather than .is_(False): null-safe for rows written
            # before the column existed.
            ActivityLog.participation.isnot(True),
        )
        .group_by(ActivityLog.contact_id)
        .subquery()
    )

    copied = (
        db.query(
            ActivityLog.contact_id.label("cid"),
            func.count(ActivityLog.id).label("copied_count"),
        )
        .filter(
            ActivityLog.contact_id.isnot(None),
            ActivityLog.participation.is_(True),
        )
        .group_by(ActivityLog.contact_id)
        .subquery()
    )

    q_rows = (
        db.query(
            Contact,
            Company.name.label("company_name"),
            # Selected, not lazy-loaded: the re-entry marker needs each row's
            # expiry, and touching contact.company per row would turn this back
            # into a query loop.
            Company.lease_expiry_date.label("company_lease_expiry_date"),
            Company.lease_expiry_months.label("company_lease_expiry_months"),
            func.coalesce(counts.c.entry_count, 0).label("entry_count"),
            counts.c.latest_date.label("latest_date"),
            func.coalesce(copied.c.copied_count, 0).label("copied_count"),
        )
        .outerjoin(Company, Company.id == Contact.company_id)
        .outerjoin(counts, counts.c.cid == Contact.id)
        .outerjoin(copied, copied.c.cid == Contact.id)
    )

    if contact_type:
        q_rows = q_rows.filter(Contact.contact_type == contact_type)
    if triaged is not None:
        q_rows = q_rows.filter(Contact.triaged.is_(triaged))
    if responded is not None:
        q_rows = q_rows.filter(Contact.responded.is_(responded))
    if stage:
        q_rows = q_rows.filter(Contact.stage == stage)
    elif not include_closed:
        # Same mechanic as untriaged: out of the default list, one filter away —
        # EXCEPT a past client whose company has come back into the 6-9 month
        # window, who belongs in the queue precisely because of that. One OR in
        # the existing WHERE clause, so this is still a single query.
        window_lo, window_hi = peak_window_date_bounds()
        q_rows = q_rows.filter(or_(
            Contact.stage != CLOSED_STAGE,
            and_(
                Contact.is_past_client.is_(True),
                Company.lease_expiry_date.isnot(None),
                Company.lease_expiry_date >= window_lo,
                Company.lease_expiry_date < window_hi,
            ),
        ))
    if q:
        like = f"%{q.strip().lower()}%"
        q_rows = q_rows.filter(or_(
            func.lower(Contact.name).like(like),
            func.lower(Contact.email).like(like),
            func.lower(Company.name).like(like),
        ))

    today = date.today()
    rows = (
        q_rows
        # Overdue first (due date not null and <= today), soonest first; then
        # the most recent activity. NULLs sort last in both keys.
        .order_by(
            (Contact.next_touch_date.is_(None)) | (Contact.next_touch_date > today),
            Contact.next_touch_date.asc(),
            counts.c.latest_date.desc().nullslast(),
            Contact.id.desc(),
        )
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )

    # One bulk fetch for the latest entry text of the contacts on this page —
    # a second constant-cost query, not one per contact.
    contact_ids = [r[0].id for r in rows]
    latest_text: dict = {}
    if contact_ids:
        for log in (
            db.query(ActivityLog)
            .filter(
                ActivityLog.contact_id.in_(contact_ids),
                ActivityLog.action_type != STAGE_CHANGE_ACTION,
                ActivityLog.participation.isnot(True),
            )
            .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
            .all()
        ):
            latest_text.setdefault(log.contact_id, log)

    out: List[ContactListRow] = []
    for (
        contact, company_name, expiry_date, expiry_months, entry_count, latest_date,
        copied_count,
    ) in rows:
        latest = latest_text.get(contact.id)
        ntd = contact.next_touch_date
        # Re-derived from the date when there is one — see _company_expiry_months.
        months = (
            months_until_lease_expiry(expiry_date) if expiry_date else expiry_months
        )
        out.append(ContactListRow(
            id=contact.id,
            name=contact.name,
            email=contact.email,
            title=contact.title,
            contact_type=contact.contact_type or "tenant",
            stage=contact.stage or "Sent",
            stage_changed_at=contact.stage_changed_at,
            closed_at=contact.closed_at,
            days_in_stage=_days_between(contact.stage_changed_at),
            next_touch_date=ntd,
            overdue=bool(ntd and ntd <= today),
            responded=bool(contact.responded),
            triaged=bool(contact.triaged),
            auto_created=bool(contact.auto_created),
            is_past_client=bool(contact.is_past_client),
            past_client_reentry=_is_past_client_reentry(contact, months),
            lease_expiry_months=months,
            company_id=contact.company_id,
            company_name=company_name,
            entry_count=int(entry_count or 0),
            copied_count=int(copied_count or 0),
            copied_only=bool(copied_count) and not int(entry_count or 0),
            latest_entry_date=latest_date,
            latest_entry_summary=(latest.action_taken if latest else None),
            latest_entry_channel=(latest.channel if latest else None),
        ))
    return out


class ContactCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    company_id: Optional[int] = None
    contact_type: str = "tenant"
    stage: str = "Sent"
    next_touch_date: Optional[date] = None
    # A contact Jack creates by hand is his own list, so it starts triaged.
    triaged: bool = True


@router.post("/", response_model=ContactOut)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    """Create a contact. Contacts are created when a name is known — never
    gated on whether the person replied."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if payload.contact_type not in CONTACT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid contact_type. One of: {', '.join(CONTACT_TYPES)}",
        )
    if payload.stage not in CONTACT_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage. One of: {', '.join(CONTACT_STAGES)}",
        )
    email = normalize_email(payload.email)
    if email and resolve_contact_by_email(db, email):
        raise HTTPException(
            status_code=409,
            detail=f"A contact with email {email} already exists",
        )
    if payload.company_id is not None:
        _get_company(db, payload.company_id)

    contact = Contact(
        name=name,
        email=email,
        phone=payload.phone,
        title=payload.title,
        company_id=payload.company_id,
        contact_type=payload.contact_type,
        stage=payload.stage,
        stage_changed_at=date.today(),
        next_touch_date=payload.next_touch_date,
        triaged=bool(payload.triaged),
        auto_created=False,
        responded=False,
    )
    # A contact created directly at Closed is still Jack setting Closed, so the
    # same bookkeeping applies — nothing auto-sets it, but nothing ignores it
    # either.
    apply_closed_stage_bookkeeping(contact, payload.stage)
    db.add(contact)
    db.flush()
    if contact.triaged:
        mark_engaged(db, contact)
    db.commit()
    db.refresh(contact)
    return _contact_out(contact)


# ══ Per-contact routes ════════════════════════════════════════════════════════

@router.get("/{contact_id}", response_model=ThreadHeader)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    """The thread header: where we are, relationship context, deal context —
    in that order, because that is the order Jack needs them in on a call.

    Null-safe throughout: a contact with no company, no entries and no facts
    returns a populated header rather than a 500.
    """
    contact = _get_contact(db, contact_id)
    company = (
        db.query(Company).filter(Company.id == contact.company_id).first()
        if contact.company_id else None
    )

    # Real touches only. A stage change has no direction and no channel, so
    # letting one in here produced "Awaiting their reply" off the back of a
    # pill click and counted six clicks as six entries.
    #
    # Participation entries are excluded here too. Someone copied on ten emails
    # has no relationship with Jack: counting those would give them a last
    # touch, a silence clock and an open loop off correspondence that was never
    # addressed to them. Their header has to read "copied, never directly
    # contacted", which is what copied_only below says. The copies are still on
    # their timeline as history — see /timeline, which includes them.
    entries = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.contact_id == contact_id,
            ActivityLog.action_type != STAGE_CHANGE_ACTION,
            ActivityLog.participation.isnot(True),
        )
        .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
        .all()
    )
    latest = entries[0] if entries else None
    copied_count = (
        db.query(func.count(ActivityLog.id))
        .filter(
            ActivityLog.contact_id == contact_id,
            ActivityLog.participation.is_(True),
        )
        .scalar()
    ) or 0

    # The open loop: what Jack owes them, or what they owe Jack — derived from
    # the NEWEST real entry and nothing else.
    #
    # This used to scan the whole thread for the most recent entry carrying a
    # follow_up_action, which meant a months-old follow-up outlived the
    # conversation: Richard Tedrow's header read "No response yet - consider
    # another follow-up if silence continues" (from a 23 July entry) while the
    # newest entry said the lease was signed and the deal closed. A stale open
    # loop is worse than an empty one, so if the newest entry carries no open
    # loop, nothing older is allowed to fill the slot.
    open_loop = None
    if latest is not None and (contact.stage or "Sent") != CLOSED_STAGE:
        if latest.follow_up_action:
            open_loop = latest.follow_up_action
        elif (latest.direction or "outbound") == "outbound":
            open_loop = "Awaiting their reply"
        else:
            open_loop = "They replied — owed a response"
    # A Closed contact has no open loop at all: the deal is placed and there is
    # nothing owed in either direction until their lease clock comes back
    # around. Falling through to "Awaiting their reply" on a signed deal is the
    # same wrong answer in a different costume.

    facts = active_facts(db, contact_id)
    # Two lines of prose, weighted toward recent (the query is already
    # newest-first) and stage-relevant. Each clicks through to its source entry.
    relationship_lines = [
        {"text": f.fact_text, "source_entry_id": f.source_entry_id, "fact_id": f.id}
        for f in facts[:2]
    ]

    conflicts = pending_conflicts(db, company)

    # The linked document is the company's CURRENT lease (models/lease.py).
    lease = current_lease(db, company.id) if company else None
    lease_file_name = lease.file_name if lease else None
    months = _company_expiry_months(company)

    return ThreadHeader(
        contact=_contact_out(contact, company.name if company else None),
        days_in_stage=_days_between(contact.stage_changed_at),
        last_touch_date=latest.log_date if latest else None,
        last_touch_channel=latest.channel if latest else None,
        days_of_silence=_days_between(latest.log_date) if latest else None,
        open_loop=open_loop,
        entry_count=len(entries),
        copied_count=int(copied_count),
        copied_only=bool(copied_count) and not entries,
        relationship_lines=relationship_lines,
        facts=[FactOut.model_validate(f) for f in facts],
        company_name=company.name if company else None,
        company_business_id=company.company_id if company else None,
        company_lease_expiry=company.lease_expiry_date if company else None,
        company_lease_expiry_months=months,
        company_sf=company.current_sf_occupied if company else None,
        company_submarket=company.current_submarket if company else None,
        company_address=company.current_address if company else None,
        company_lease_expiry_source=company.lease_expiry_source if company else None,
        company_address_source=(
            getattr(company, "current_address_source", None) if company else None
        ),
        company_sf_source=(
            getattr(company, "current_sf_occupied_source", None) if company else None
        ),
        company_lease_file_name=lease_file_name,
        company_lease_uploaded_at=lease.uploaded_at if lease else None,
        # Checked here so the card can say "the file is missing" instead of
        # handing Jack a link that does nothing.
        company_lease_file_missing=bool(
            lease_file_name and not lease_file_exists(lease_file_name)
        ),
        has_lease_extraction=bool(lease and lease.extraction_json),
        past_client_reentry=_is_past_client_reentry(contact, months),
        has_data_conflict=bool(company.has_data_conflict) if company else False,
        conflicts=conflicts,
        pending_updates=pending_updates_for_company(
            db, company.id if company else None,
        ),
    )


class ContactPatch(BaseModel):
    """Any field on a contact. Omitted fields are left unchanged.

    Changing the stage writes a timeline event recording the transition, so the
    history of where the relationship has been survives on the thread.
    """
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    company_id: Optional[int] = None
    contact_type: Optional[str] = None
    stage: Optional[str] = None
    next_touch_date: Optional[date] = None
    responded: Optional[bool] = None
    triaged: Optional[bool] = None
    # Distinguishes "clear the next-touch date" from "don't touch it", since
    # None means "omitted" in the field above.
    clear_next_touch: bool = False


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int, payload: ContactPatch, db: Session = Depends(get_db),
):
    """Edit a contact. Every edit is engagement, so it triages the record.

    Stage changes are always manual — nothing here auto-advances a stage or
    drifts a contact to Dormant.
    """
    contact = _get_contact(db, contact_id)

    if payload.contact_type is not None and payload.contact_type not in CONTACT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid contact_type. One of: {', '.join(CONTACT_TYPES)}",
        )
    if payload.stage is not None and payload.stage not in CONTACT_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage. One of: {', '.join(CONTACT_STAGES)}",
        )
    if payload.company_id is not None:
        _get_company(db, payload.company_id)

    if payload.email is not None:
        new_email = normalize_email(payload.email)
        if new_email and new_email != contact.email:
            clash = resolve_contact_by_email(db, new_email)
            if clash and clash.id != contact.id:
                raise HTTPException(
                    status_code=409,
                    detail=f"A contact with email {new_email} already exists",
                )
        contact.email = new_email

    old_stage = contact.stage or "Sent"
    stage_moved = payload.stage is not None and payload.stage != old_stage
    if stage_moved:
        contact.stage = payload.stage
        contact.stage_changed_at = date.today()
        # closed_at set/cleared, is_past_client set and never cleared. One
        # writer, in the service, so the asymmetry is not re-derived per route.
        apply_closed_stage_bookkeeping(contact, payload.stage)

    for field in ("name", "phone", "title", "company_id", "contact_type", "responded"):
        value = getattr(payload, field)
        if value is not None:
            setattr(contact, field, value)

    if payload.clear_next_touch:
        contact.next_touch_date = None
    elif payload.next_touch_date is not None:
        contact.next_touch_date = payload.next_touch_date

    if payload.triaged is not None:
        contact.triaged = payload.triaged

    contact.updated_at = datetime.utcnow()

    # A stage change is preserved as a divider so the thread shows how the
    # relationship moved, not just where it ended up. record_stage_change() is
    # the single writer: it collapses a burst of clicks into the net move and
    # deletes one that returns to where it started.
    if stage_moved:
        record_stage_change(db, contact, old_stage, payload.stage)

    # Any edit is engagement — unless Jack explicitly untriaged just now.
    if payload.triaged is not False:
        mark_engaged(db, contact)

    db.commit()
    db.refresh(contact)
    return _contact_out(contact)


class ContactDeleteResult(BaseModel):
    deleted_contact_id: int
    mode: str
    entries_deleted: int
    entries_unattached: int
    facts_deleted: int


@router.delete("/{contact_id}", response_model=ContactDeleteResult)
def delete_contact(
    contact_id: int,
    mode: str = Query(
        "unattach",
        description=(
            "unattach = keep the entries, detached, visible in All Activity. "
            "cascade = delete the entries too."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Delete a contact, with an explicit choice about their entries.

    Two outcomes, because they are not the same decision:

      - unattach (default): the entries survive with a null contact_id and stay
        in All Activity. Use it when the person record was wrong but the
        conversations happened.
      - cascade: the entries go too, along with any facts the intelligence layer
        derived from them. Use it for test data and duplicates.

    Facts are deleted either way — a fact is a thing learned about a person, so
    it cannot outlive the person record.
    """
    if mode not in ("unattach", "cascade"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'unattach' or 'cascade'",
        )
    contact = _get_contact(db, contact_id)

    facts_deleted = (
        db.query(ContactFact)
        .filter(ContactFact.contact_id == contact_id)
        .delete(synchronize_session=False)
    )

    entries_deleted = 0
    entries_unattached = 0
    entry_ids = [
        row[0] for row in
        db.query(ActivityLog.id).filter(ActivityLog.contact_id == contact_id).all()
    ]

    def _purge_intel(ids):
        """Drop derived intel for entries about to disappear. Bulk deletes with
        no commit, so a failure here can never half-apply the delete."""
        if not ids:
            return
        try:
            from app.services.activity_intel_service import purge_log_intel
            for entry_id in ids:
                purge_log_intel(db, entry_id)
        except Exception as exc:  # noqa: BLE001 — never block a delete on intel
            import logging
            logging.getLogger(__name__).warning(
                "intel purge skipped while deleting contact %s (%s)", contact_id, exc,
            )

    if mode == "cascade":
        if entry_ids:
            # Another contact's fact may cite one of these entries as its
            # source; null the pointer rather than orphaning a dangling id.
            db.query(ContactFact).filter(
                ContactFact.source_entry_id.in_(entry_ids)
            ).update({"source_entry_id": None}, synchronize_session=False)
            _purge_intel(entry_ids)
            entries_deleted = (
                db.query(ActivityLog)
                .filter(ActivityLog.id.in_(entry_ids))
                .delete(synchronize_session=False)
            )
    else:
        # Dividers go either way: "Stage: Sent -> Replied" detached from the
        # person it described is noise in All Activity, not history.
        divider_ids = [
            row[0] for row in
            db.query(ActivityLog.id).filter(
                ActivityLog.contact_id == contact_id,
                ActivityLog.action_type == STAGE_CHANGE_ACTION,
            ).all()
        ]
        if divider_ids:
            _purge_intel(divider_ids)
            entries_deleted = (
                db.query(ActivityLog)
                .filter(ActivityLog.id.in_(divider_ids))
                .delete(synchronize_session=False)
            )
        entries_unattached = (
            db.query(ActivityLog)
            .filter(ActivityLog.contact_id == contact_id)
            .update({"contact_id": None}, synchronize_session=False)
        )

    db.delete(contact)
    db.commit()
    return ContactDeleteResult(
        deleted_contact_id=contact_id,
        mode=mode,
        entries_deleted=entries_deleted,
        entries_unattached=entries_unattached,
        facts_deleted=facts_deleted,
    )


@router.get("/{contact_id}/timeline", response_model=TimelinePage)
def contact_timeline(
    contact_id: int,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """This contact's entries, newest first, paginated.

    No default high limit and no hard ceiling — callers page explicitly. An
    empty timeline returns an empty page, never a 500.
    """
    _get_contact(db, contact_id)
    base = db.query(ActivityLog).filter(ActivityLog.contact_id == contact_id)
    total = base.count()
    rows = (
        base.options(joinedload(ActivityLog.stamped_company))
        .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )
    # Attachments for the whole page in ONE query, never one per entry — a
    # thread with fifty attachments must cost the same as a thread with one.
    attachments_by_entry: dict = {}
    page_ids = [log.id for log in rows]
    if page_ids:
        for att in (
            db.query(ActivityAttachment)
            .filter(ActivityAttachment.activity_log_id.in_(page_ids))
            .order_by(ActivityAttachment.id.asc())
            .all()
        ):
            item = TimelineAttachment.model_validate(att)
            item.missing = not attachment_file_exists(att.file_name, att.stored_year)
            attachments_by_entry.setdefault(att.activity_log_id, []).append(item)

    entries = []
    for log in rows:
        item = TimelineEntry.model_validate(log)
        item.participation = bool(log.participation)
        if log.stamped_company is not None:
            item.company_stamp_name = log.stamped_company.name
        item.attachments = attachments_by_entry.get(log.id, [])
        entries.append(item)
    return TimelinePage(
        total=total, limit=limit, offset=offset, entries=entries,
    )
