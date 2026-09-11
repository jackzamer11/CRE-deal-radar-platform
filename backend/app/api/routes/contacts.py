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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, ContactFact, CONTACT_STAGES, CONTACT_TYPES
from app.services.contact_service import (
    active_facts, create_fact, mark_engaged, normalize_email,
    resolve_contact_by_email,
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
    next_touch_date: Optional[date] = None
    responded: bool = False
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
    days_in_stage: Optional[int] = None
    next_touch_date: Optional[date] = None
    overdue: bool = False
    responded: bool = False
    triaged: bool = False
    auto_created: bool = False
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    entry_count: int = 0
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
    # Slot 2 — two lines of prose, each with the entry it came from
    relationship_lines: List[dict] = []
    facts: List[FactOut] = []
    # Slot 3
    company_name: Optional[str] = None
    company_business_id: Optional[str] = None
    company_lease_expiry: Optional[date] = None
    company_sf: Optional[int] = None
    company_submarket: Optional[str] = None
    has_data_conflict: bool = False
    conflicts: List[ConflictOut] = []


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
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """One row per contact, with entry counts and latest-entry summary.

    Computed in a single aggregated query — a LEFT JOIN with GROUP BY for the
    counts and latest date, plus one bulk fetch for the latest entry's text. No
    per-contact query loop, so this does not degrade as contacts accumulate.

    Default sort: overdue next-touch first (soonest due first), then most
    recent activity. Paginated with no hard ceiling.
    """
    counts = (
        db.query(
            ActivityLog.contact_id.label("cid"),
            func.count(ActivityLog.id).label("entry_count"),
            func.max(ActivityLog.log_date).label("latest_date"),
        )
        .filter(ActivityLog.contact_id.isnot(None))
        .group_by(ActivityLog.contact_id)
        .subquery()
    )

    q_rows = (
        db.query(
            Contact,
            Company.name.label("company_name"),
            func.coalesce(counts.c.entry_count, 0).label("entry_count"),
            counts.c.latest_date.label("latest_date"),
        )
        .outerjoin(Company, Company.id == Contact.company_id)
        .outerjoin(counts, counts.c.cid == Contact.id)
    )

    if contact_type:
        q_rows = q_rows.filter(Contact.contact_type == contact_type)
    if triaged is not None:
        q_rows = q_rows.filter(Contact.triaged.is_(triaged))
    if responded is not None:
        q_rows = q_rows.filter(Contact.responded.is_(responded))
    if stage:
        q_rows = q_rows.filter(Contact.stage == stage)
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
            .filter(ActivityLog.contact_id.in_(contact_ids))
            .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
            .all()
        ):
            latest_text.setdefault(log.contact_id, log)

    out: List[ContactListRow] = []
    for contact, company_name, entry_count, latest_date in rows:
        latest = latest_text.get(contact.id)
        ntd = contact.next_touch_date
        out.append(ContactListRow(
            id=contact.id,
            name=contact.name,
            email=contact.email,
            title=contact.title,
            contact_type=contact.contact_type or "tenant",
            stage=contact.stage or "Sent",
            stage_changed_at=contact.stage_changed_at,
            days_in_stage=_days_between(contact.stage_changed_at),
            next_touch_date=ntd,
            overdue=bool(ntd and ntd <= today),
            responded=bool(contact.responded),
            triaged=bool(contact.triaged),
            auto_created=bool(contact.auto_created),
            company_id=contact.company_id,
            company_name=company_name,
            entry_count=int(entry_count or 0),
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

    entries = (
        db.query(ActivityLog)
        .filter(ActivityLog.contact_id == contact_id)
        .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
        .all()
    )
    latest = entries[0] if entries else None

    # The open loop: what Jack owes them, or what they owe Jack. The newest
    # entry carrying a follow-up action wins; failing that, an unanswered
    # outbound is a loop on their side.
    open_loop = None
    for e in entries:
        if e.follow_up_action:
            open_loop = e.follow_up_action
            break
    if open_loop is None and latest is not None:
        if (latest.direction or "outbound") == "outbound":
            open_loop = "Awaiting their reply"
        else:
            open_loop = "They replied — owed a response"

    facts = active_facts(db, contact_id)
    # Two lines of prose, weighted toward recent (the query is already
    # newest-first) and stage-relevant. Each clicks through to its source entry.
    relationship_lines = [
        {"text": f.fact_text, "source_entry_id": f.source_entry_id, "fact_id": f.id}
        for f in facts[:2]
    ]

    conflicts = pending_conflicts(db, company)

    return ThreadHeader(
        contact=_contact_out(contact, company.name if company else None),
        days_in_stage=_days_between(contact.stage_changed_at),
        last_touch_date=latest.log_date if latest else None,
        last_touch_channel=latest.channel if latest else None,
        days_of_silence=_days_between(latest.log_date) if latest else None,
        open_loop=open_loop,
        entry_count=len(entries),
        relationship_lines=relationship_lines,
        facts=[FactOut.model_validate(f) for f in facts],
        company_name=company.name if company else None,
        company_business_id=company.company_id if company else None,
        company_lease_expiry=company.lease_expiry_date if company else None,
        company_sf=company.current_sf_occupied if company else None,
        company_submarket=company.current_submarket if company else None,
        has_data_conflict=bool(company.has_data_conflict) if company else False,
        conflicts=conflicts,
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

    stage_event = None
    if payload.stage is not None and payload.stage != (contact.stage or "Sent"):
        stage_event = f"Stage: {contact.stage or 'Sent'} → {payload.stage}"
        contact.stage = payload.stage
        contact.stage_changed_at = date.today()

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

    # A stage change is preserved as a timeline event so the thread shows how
    # the relationship moved, not just where it ended up.
    if stage_event:
        db.add(ActivityLog(
            log_date=date.today(),
            contact_id=contact.id,
            company_id=contact.company_id,
            company_stamp_id=contact.company_id,
            action_type="SIGNAL_UPDATE",
            action_taken=stage_event,
            direction="outbound",
            channel="other",
            created_by="user",
        ))

    # Any edit is engagement — unless Jack explicitly untriaged just now.
    if payload.triaged is not False:
        mark_engaged(db, contact)

    db.commit()
    db.refresh(contact)
    return _contact_out(contact)


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
    entries = []
    for log in rows:
        item = TimelineEntry.model_validate(log)
        if log.stamped_company is not None:
            item.company_stamp_name = log.stamped_company.name
        entries.append(item)
    return TimelinePage(
        total=total, limit=limit, offset=offset, entries=entries,
    )
