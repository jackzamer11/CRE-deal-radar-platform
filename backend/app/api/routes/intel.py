import json
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import (
    IntelActivityExtraction, IntelCriterion, IntelFeedback, IntelOpportunity,
)
from app.models.observation import Observation
from app.services.activity_intel_service import (
    REQUIREMENT_FIELDS,
    mine_all_activity_logs,
    mineable_filters,
    requeue_fuzzy_dates,
)
from app.services.document_extraction_service import MissingAPIKeyError
from app.services.intel_feedback_service import (
    FeedbackError,
    disposition_opportunity,
    save_criterion,
)
from app.services.intel_results_service import outcome_for, results_summary
from app.services.intel_signal_service import generate_with_stats
from app.services.requirement_subject import (
    ABOUT_DISMISSED, ABOUT_MARKET, ABOUT_NAMED, ABOUT_UNASSIGNED, NameResolver,
    counterparty_side_log_ids,
)

router = APIRouter(prefix="/intel", tags=["intel"])

# How a held or market fact reads on screen.
REQUIREMENT_LABELS = {
    "req_sf_min": "Min SF", "req_sf_max": "Max SF", "req_submarkets": "Submarkets",
    "req_budget_max_psf": "Budget", "req_lease_term_years": "Term",
    "req_must_haves": "Must-haves", "req_access_needs": "Access",
    "req_buildout_willingness": "Buildout", "req_ti_expectation": "TI",
    "req_timing": "Timing", "req_space_type": "Space type",
    "expiration_date": "Lease expires", "contact_name": "Contact",
    "contact_email": "Email",
}
CONTACT_ONLY_FIELDS = {"contact_name", "contact_email"}
MARKET_LABELS = {
    "mkt_property": "Property", "mkt_available_sf": "Available",
    "mkt_asking_rent": "Asking rent", "mkt_concessions": "Concessions",
    "mkt_notes": "Note",
}


class IntelOpportunityOut(BaseModel):
    id: int
    title: str
    entity_type: str
    entity_id: int
    score: float
    rationale: Optional[str] = None
    signals: list = []
    surfaced_at: str
    status: str
    # The lease cycle a lease card is about ("2027-08"), and for a deferred
    # card, the day it comes back.
    cycle: Optional[str] = None
    resurface_at: Optional[date] = None

    class Config:
        from_attributes = True


def _to_out(opp: IntelOpportunity) -> IntelOpportunityOut:
    try:
        signals = json.loads(opp.signals_json) if opp.signals_json else []
    except (ValueError, TypeError):
        signals = []
    return IntelOpportunityOut(
        id=opp.id,
        title=opp.title,
        entity_type=opp.entity_type,
        entity_id=opp.entity_id,
        score=opp.score,
        rationale=opp.rationale,
        signals=signals,
        surfaced_at=opp.surfaced_at.isoformat() if opp.surfaced_at else "",
        status=opp.status,
        cycle=opp.cycle,
        resurface_at=opp.resurface_at,
    )


class GenerateStatsOut(BaseModel):
    """What the run actually looked at — so an empty result is explainable."""

    facts_scanned: int = 0
    expirations_found: int = 0
    expirations_unreadable: int = 0
    expirations_past: int = 0
    expirations_beyond_horizon: int = 0
    # Shown in the Waiting group: the tenant is being worked, said when to
    # come back, or said no recently. They move to Ready on their own.
    waiting: int = 0
    opportunities: int = 0
    by_signal_type: dict = {}


class GenerateOut(BaseModel):
    opportunities: List[IntelOpportunityOut]
    stats: GenerateStatsOut


@router.post("/opportunities/generate", response_model=GenerateOut)
def generate(db: Session = Depends(get_db)):
    """Run the signal rules and upsert opportunities (idempotent).

    Returns the opportunities touched this run, highest score first, plus a scan
    summary. The summary matters: a run that produces nothing used to render the
    same blank screen as a broken button.
    """
    touched, stats = generate_with_stats(db)
    touched.sort(key=lambda o: o.score, reverse=True)
    return GenerateOut(
        opportunities=[_to_out(o) for o in touched],
        stats=GenerateStatsOut(**stats),
    )


@router.get("/opportunities", response_model=List[IntelOpportunityOut])
def list_opportunities(status: str = "open", db: Session = Depends(get_db)):
    """List opportunities (open by default), highest score first."""
    query = db.query(IntelOpportunity)
    if status:
        query = query.filter(IntelOpportunity.status == status)
    rows = query.order_by(IntelOpportunity.score.desc(), IntelOpportunity.surfaced_at.desc()).all()
    return [_to_out(o) for o in rows]


# ── Feedback loop (Phase E) ──────────────────────────────────────────────────

class DispositionIn(BaseModel):
    disposition: str                       # accepted / rejected / deferred
    reason_category: Optional[str] = None  # required for reject/defer
    reason_text: Optional[str] = None
    # Deferrals only: the day the card comes back. Defaults to 30 days out.
    resurface_at: Optional[date] = None


class DispositionOut(BaseModel):
    opportunity: IntelOpportunityOut
    # Non-null when a durable-policy reason has recurred enough to suggest a rule.
    suggested_rule: Optional[str] = None


class OutcomeOut(BaseModel):
    """What followed an accepted card, read off the timeline."""
    decided_on: Optional[date] = None
    first_touch: Optional[date] = None
    first_touch_channel: Optional[str] = None
    touches: int = 0
    best_stage: Optional[str] = None
    closed: bool = False


class HistoryItemOut(IntelOpportunityOut):
    disposition: Optional[str] = None
    reason_category: Optional[str] = None
    reason_text: Optional[str] = None
    # Accepted cards only.
    outcome: Optional[OutcomeOut] = None


class ResultRowOut(BaseModel):
    family: str
    accepted: int = 0
    rejected: int = 0
    deferred: int = 0
    # Of the accepted: touched within two weeks, reached Interested or
    # further, closed.
    acted: int = 0
    interested: int = 0
    closed: int = 0


class CriterionIn(BaseModel):
    statement: str
    criterion_type: Optional[str] = None


class CriterionOut(BaseModel):
    id: int
    statement: str
    criterion_type: Optional[str] = None
    active: bool
    created_at: str

    class Config:
        from_attributes = True


@router.post("/opportunities/{opportunity_id}/disposition", response_model=DispositionOut)
def disposition(opportunity_id: int, payload: DispositionIn, db: Session = Depends(get_db)):
    """Accept / reject / defer an opportunity. Reject and defer require a reason."""
    try:
        opp, suggested = disposition_opportunity(
            db, opportunity_id, payload.disposition,
            payload.reason_category, payload.reason_text,
            resurface_at=payload.resurface_at,
        )
    except FeedbackError as exc:
        # "Opportunity not found" → 404; validation problems → 400.
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return DispositionOut(opportunity=_to_out(opp), suggested_rule=suggested)


@router.get("/history", response_model=List[HistoryItemOut])
def history(db: Session = Depends(get_db)):
    """Jack's decisions with their latest feedback, newest first — and, for an
    accepted card, what followed it.

    Cards the machine retired (superseded) are not decisions and are left out:
    every run retires the cards whose facts moved or changed, and listing those
    here buried the decisions.
    """
    rows = (
        db.query(IntelOpportunity)
        .filter(IntelOpportunity.status.in_(("accepted", "rejected", "deferred")))
        .order_by(IntelOpportunity.surfaced_at.desc())
        .all()
    )
    out: List[HistoryItemOut] = []
    for opp in rows:
        fb = (
            db.query(IntelFeedback)
            .filter(IntelFeedback.opportunity_id == opp.id)
            .order_by(IntelFeedback.created_at.desc())
            .first()
        )
        base = _to_out(opp)
        out.append(HistoryItemOut(
            **base.model_dump(),
            disposition=fb.disposition if fb else opp.status,
            reason_category=fb.reason_category if fb else None,
            reason_text=fb.reason_text if fb else None,
            outcome=OutcomeOut(**outcome_for(db, opp)) if opp.status == "accepted" else None,
        ))
    return out


@router.get("/results", response_model=List[ResultRowOut])
def results(db: Session = Depends(get_db)):
    """Per kind of card: decisions, and how often accepting one led anywhere."""
    return [ResultRowOut(**row) for row in results_summary(db)]


# ── Activity-log mining ──────────────────────────────────────────────────────

class ActivityMineIn(BaseModel):
    limit: Optional[int] = None   # process at most N logs this run
    force: bool = False           # re-process logs already mined


class ActivityMineOut(BaseModel):
    processed: int
    facts: int
    skipped: int
    failed: int


class ActivityStatusOut(BaseModel):
    total_logs: int
    mined: int
    remaining: int
    facts_extracted: int
    failed: int


@router.post("/activity/mine", response_model=ActivityMineOut)
def mine_activity(payload: ActivityMineIn, db: Session = Depends(get_db)):
    """Mine freeform activity-log text into structured observations.

    Read-only with respect to activity logs — it never edits or deletes them.
    Idempotent: already-mined logs are skipped unless force=true.
    """
    try:
        result = mine_all_activity_logs(db, limit=payload.limit, force=payload.force)
    except MissingAPIKeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — say what broke instead of a bare 500
        # Every entry read before this point is already saved. The traceback
        # goes to backend/logs/mining.log (gitignored) so a failure can be
        # diagnosed after the fact; the page gets the one-line reason.
        _log_mining_error(exc)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Mining stopped: {type(exc).__name__}: {str(exc)[:200]} — "
                   "everything read so far is saved; press Mine again to continue.",
        ) from exc
    return ActivityMineOut(**result)


def _log_mining_error(exc: Exception) -> None:
    import os
    import traceback
    from datetime import datetime as _dt

    folder = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "mining.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {_dt.now().isoformat(timespec='seconds')} ===\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except OSError:
        pass   # logging must never turn one failure into two


class RequeueDatesOut(BaseModel):
    requeued: int
    unreadable: int
    checked: int


@router.post("/activity/requeue-dates", response_model=RequeueDatesOut)
def requeue_dates(db: Session = Depends(get_db)):
    """Send auto-approved but imprecise lease dates back to the Review queue.

    One-time backfill for facts mined before auto-approval became value-aware.
    Idempotent, and never touches a fact a human already verified.
    """
    return RequeueDatesOut(**requeue_fuzzy_dates(db))


@router.get("/activity/status", response_model=ActivityStatusOut)
def activity_status(db: Session = Depends(get_db)):
    """How much of the activity log has been turned into structured facts.

    Counts only entries a run would read: copies of an email, stage dividers
    and archived entries are not waiting for anything, and counting them kept
    "Mine 95 logs" on screen forever.
    """
    mineable_ids = {
        lid for (lid,) in db.query(ActivityLog.id).filter(*mineable_filters()).all()
    }
    rows = [
        r for r in db.query(IntelActivityExtraction).all()
        if r.activity_log_id in mineable_ids
    ]
    # A failed read is still waiting, not read: counting it as read hid the
    # Mine button, leaving no way to retry it.
    mined = len({r.activity_log_id for r in rows if r.status != "failed"})
    total = len(mineable_ids)
    return ActivityStatusOut(
        total_logs=total,
        mined=mined,
        remaining=max(0, total - mined),
        facts_extracted=sum(r.fields_found for r in rows),
        # A failure the same entry later got past is not a failure: two reads
        # of one entry can overlap, one losing a lock while the other succeeds.
        failed=len({r.activity_log_id for r in rows if r.status == "failed"}
                   - {r.activity_log_id for r in rows if r.status != "failed"}),
    )


# ── Requirements waiting for a tenant ────────────────────────────────────────
# A note can state a client's requirement without saying which client — Jack
# asking a landlord's broker about "a tenant seeking 500-600 sqft". Those facts
# are kept, not filed under the brokerage, and wait here until Jack says whose
# they are. See services/requirement_subject.py.

class HeldFactOut(BaseModel):
    id: int
    field: str
    label: str
    value: Optional[str] = None
    snippet: Optional[str] = None


class HeldRequirementOut(BaseModel):
    """Everything one entry said about a client it did not name."""
    entry_id: int
    log_date: Optional[date] = None
    direction: Optional[str] = None
    summary: str
    contact_name: Optional[str] = None
    contact_company: Optional[str] = None
    # The name the note gave, when it gave one no single company matches.
    said_name: Optional[str] = None
    facts: List[HeldFactOut]


class AssignIn(BaseModel):
    """Exactly one: a company (a tenant) or a person (anyone — a counterparty,
    an investor, a tenant contact)."""
    company_id: Optional[int] = None
    contact_id: Optional[int] = None


def _entry_id(obs: Observation) -> Optional[int]:
    try:
        return int(str(obs.source_doc).split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _held_facts(db: Session, entry_id: Optional[int] = None) -> List[Observation]:
    """Unattached requirement facts waiting for a tenant.

    Marked unassigned or named by the miner — or mined before it said, and
    from an entry filed under a counterparty's own firm (the same rule Intel
    applies to those; see counterparty_side_log_ids).
    """
    query = (
        db.query(Observation)
        .filter(
            Observation.superseded_by_id.is_(None),
            Observation.assigned_company_id.is_(None),
            Observation.assigned_contact_id.is_(None),
            or_(Observation.about.in_((ABOUT_UNASSIGNED, ABOUT_NAMED)),
                Observation.about.is_(None)),
            Observation.source_doc.like("activity_log:%"),
            Observation.field.in_(list(REQUIREMENT_FIELDS)),
        )
    )
    if entry_id is not None:
        query = query.filter(Observation.source_doc == f"activity_log:{entry_id}")
    rows = query.all()
    legacy = counterparty_side_log_ids(
        db, [eid for eid in (_entry_id(o) for o in rows if o.about is None) if eid]
    )
    return [o for o in rows if o.about is not None or _entry_id(o) in legacy]


def _entry_facts(db: Session, entry_id: int) -> List[Observation]:
    return _held_facts(db, entry_id)


@router.get("/unassigned-requirements", response_model=List[HeldRequirementOut])
def unassigned_requirements(db: Session = Depends(get_db)):
    """Requirements waiting for Jack to say which tenant they belong to,
    grouped by the entry that stated them, newest first.

    A fact naming a tenant is held only while no single company matches the
    name — the same resolver Intel uses, so a fact is never both on a card and
    in this list.
    """
    resolver = NameResolver(db)
    by_entry: dict = {}
    for obs in _held_facts(db):
        if obs.about == ABOUT_NAMED and resolver.resolve(obs.about_name) is not None:
            continue
        entry_id = _entry_id(obs)
        if entry_id is None:
            continue
        by_entry.setdefault(entry_id, []).append(obs)
    if not by_entry:
        return []

    logs = {
        log.id: log for log in
        db.query(ActivityLog).filter(ActivityLog.id.in_(list(by_entry))).all()
    }
    out: List[HeldRequirementOut] = []
    for entry_id, facts in by_entry.items():
        log = logs.get(entry_id)
        if log is None or log.archived:
            continue
        # A name or an address is not a requirement. An entry that stated only
        # who the client is has nothing to attach; the names stay on the entry
        # as hints when there IS something to attach.
        if not any(o.field not in CONTACT_ONLY_FIELDS for o in facts):
            continue
        contact = log.contact
        said = next((o.about_name for o in facts if o.about_name), None)
        # One line per field: the same fact restated in a note is one fact.
        seen: set = set()
        rows: List[HeldFactOut] = []
        for obs in sorted(facts, key=lambda o: list(REQUIREMENT_FIELDS).index(o.field)):
            key = (obs.field, (obs.value or "").strip().casefold())
            if key in seen:
                continue
            seen.add(key)
            rows.append(HeldFactOut(
                id=obs.id, field=obs.field,
                label=REQUIREMENT_LABELS.get(obs.field, obs.field),
                value=obs.value, snippet=obs.source_snippet,
            ))
        out.append(HeldRequirementOut(
            entry_id=entry_id, log_date=log.log_date, direction=log.direction,
            summary=log.action_taken or "",
            contact_name=contact.name if contact is not None else None,
            contact_company=(contact.company.name
                             if contact is not None and contact.company is not None else None),
            said_name=said, facts=rows,
        ))
    out.sort(key=lambda h: (h.log_date or date.min, h.entry_id), reverse=True)
    return out


@router.post("/unassigned-requirements/{entry_id}/assign")
def assign_requirement(entry_id: int, payload: AssignIn, db: Session = Depends(get_db)):
    """This entry's held requirement belongs to this company, or this person.

    A company is a tenant: the requirement joins its Intel card. A person can
    be anyone. A tenant contact at a company joins that company's card; a
    counterparty, or someone with no company (an investor, a buyer), keeps it
    on their own page — see /intel/attached-requirements.

    Marked as Jack's judgement (verified_by="human"), so re-mining the entry
    keeps the answer instead of asking again.
    """
    if (payload.company_id is None) == (payload.contact_id is None):
        raise HTTPException(status_code=400, detail="Pick a company or a person — one of them.")
    if payload.company_id is not None:
        if db.query(Company.id).filter(Company.id == payload.company_id).first() is None:
            raise HTTPException(status_code=404, detail="Company not found")
    elif db.query(Contact.id).filter(Contact.id == payload.contact_id).first() is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    facts = _entry_facts(db, entry_id)
    if not facts:
        raise HTTPException(status_code=404, detail="Nothing is waiting on that entry")
    for obs in facts:
        obs.assigned_company_id = payload.company_id
        obs.assigned_contact_id = payload.contact_id
        obs.human_verified = True
        obs.verified_by = "human"
    db.commit()
    return {"entry_id": entry_id, "company_id": payload.company_id,
            "contact_id": payload.contact_id, "facts": len(facts)}


class StatedNeedOut(BaseModel):
    """One thing a tenant needs, as most recently said."""
    field: str
    label: str
    value: Optional[str] = None
    stated_on: Optional[date] = None
    entry_id: Optional[int] = None
    snippet: Optional[str] = None
    # False once it is old enough to re-confirm on the next call.
    fresh: bool = True


NEED_LABELS = {k: v for k, v in REQUIREMENT_LABELS.items()
               if k not in ("contact_name", "contact_email")}


@router.get("/needs", response_model=List[StatedNeedOut])
def stated_needs(contact_id: Optional[int] = None, company_key: Optional[str] = None,
                 db: Session = Depends(get_db)):
    """What a tenant has told Jack they need, for the Activity Log.

    For a contact: their company's needs, gathered from every note about that
    company, plus anything on their own notes that has no company and anything
    Jack attached to them. For a company (company_key is its business id, as
    the company timeline uses): every note about it. Filed exactly as Intel
    files facts, so a broker's market talk, a held requirement, a dismissed one
    or an unsettled contradiction never appears here. Each field once, as most
    recently said, linked to the note.
    """
    from app.services.intel_signal_service import (
        _active_observations, _group, _is_fresh, _load_context, _source_log_id, _stated_on,
    )

    entities = []
    attached_rows: List[Observation] = []
    if company_key:
        company = db.query(Company).filter(Company.company_id == company_key).first()
        if company is None:
            raise HTTPException(status_code=404, detail="Company not found")
        entities.append(("company", company.id))
    elif contact_id is not None:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        if contact.company_id:
            entities.append(("company", contact.company_id))
        entities += [
            ("activity_log", lid) for (lid,) in
            db.query(ActivityLog.id).filter(ActivityLog.contact_id == contact_id).all()
        ]
        attached_rows = (
            db.query(Observation)
            .filter(Observation.superseded_by_id.is_(None),
                    Observation.assigned_contact_id == contact_id)
            .all()
        )
    else:
        raise HTTPException(status_code=400, detail="Give a contact_id or a company_key")

    ctx, facts = _load_context(db, _active_observations(db))
    groups = _group(ctx, facts)
    rows = [o for e in entities for o in groups.get(e, [])] + attached_rows

    today = date.today()
    newest: dict = {}
    for obs in rows:
        if obs.field not in NEED_LABELS or not obs.value:
            continue
        said = _stated_on(ctx, obs)
        key = (said or date.min, obs.id)
        if obs.field not in newest or key > newest[obs.field][0]:
            newest[obs.field] = (key, obs, said)

    out: List[StatedNeedOut] = []
    for field in NEED_LABELS:
        if field not in newest:
            continue
        _, obs, said = newest[field]
        out.append(StatedNeedOut(
            field=field, label=NEED_LABELS[field], value=obs.value.strip(),
            stated_on=said, entry_id=_source_log_id(obs.source_doc),
            snippet=obs.source_snippet,
            fresh=True if field == "expiration_date" else _is_fresh(ctx, obs, today),
        ))
    return out


class AttachedRequirementOut(BaseModel):
    id: int
    entry_id: int
    log_date: Optional[date] = None
    field: str
    label: str
    value: Optional[str] = None
    snippet: Optional[str] = None


@router.get("/attached-requirements", response_model=List[AttachedRequirementOut])
def attached_requirements(contact_id: int, db: Session = Depends(get_db)):
    """Requirements Jack attached to this person, newest first.

    Where a counterparty's or an investor's stated needs live — they are not a
    tenant card, but they are not lost either.
    """
    rows = (
        db.query(Observation)
        .filter(
            Observation.superseded_by_id.is_(None),
            Observation.assigned_contact_id == contact_id,
        )
        .all()
    )
    entry_ids = [eid for eid in (_entry_id(o) for o in rows) if eid]
    dates = dict(
        db.query(ActivityLog.id, ActivityLog.log_date)
        .filter(ActivityLog.id.in_(entry_ids)).all()
    ) if entry_ids else {}
    out: List[AttachedRequirementOut] = []
    seen: set = set()
    for obs in rows:
        key = (obs.field, (obs.value or "").strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        eid = _entry_id(obs)
        out.append(AttachedRequirementOut(
            id=obs.id, entry_id=eid or 0, log_date=dates.get(eid),
            field=obs.field, label=REQUIREMENT_LABELS.get(obs.field, obs.field),
            value=obs.value, snippet=obs.source_snippet,
        ))
    out.sort(key=lambda r: (r.log_date or date.min, r.entry_id), reverse=True)
    return out


@router.post("/unassigned-requirements/{entry_id}/dismiss")
def dismiss_requirement(entry_id: int, db: Session = Depends(get_db)):
    """Not a requirement after all. Kept, marked, and never asked about again."""
    facts = _entry_facts(db, entry_id)
    if not facts:
        raise HTTPException(status_code=404, detail="Nothing is waiting on that entry")
    for obs in facts:
        obs.about = ABOUT_DISMISSED
        obs.human_verified = True
        obs.verified_by = "human"
    db.commit()
    return {"entry_id": entry_id, "facts": len(facts)}


# ── What brokers and landlords said about the market ─────────────────────────

class MarketFactOut(BaseModel):
    id: int
    entry_id: int
    log_date: Optional[date] = None
    field: str
    label: str
    value: Optional[str] = None
    snippet: Optional[str] = None


@router.get("/market-facts", response_model=List[MarketFactOut])
def market_facts(contact_id: int, db: Session = Depends(get_db)):
    """Space and market information from one person's entries, newest first.

    Reference only: it drives no card and never reaches outreach copy.
    """
    logs = {
        log.id: log for log in
        db.query(ActivityLog).filter(ActivityLog.contact_id == contact_id).all()
    }
    if not logs:
        return []
    sources = [f"activity_log:{lid}" for lid in logs]
    out: List[MarketFactOut] = []
    seen: set = set()
    for obs in (
        db.query(Observation)
        .filter(
            Observation.superseded_by_id.is_(None),
            Observation.about == ABOUT_MARKET,
            Observation.source_doc.in_(sources),
        )
        .all()
    ):
        entry_id = int(str(obs.source_doc).split(":", 1)[1])
        key = (obs.field, (obs.value or "").strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(MarketFactOut(
            id=obs.id, entry_id=entry_id, log_date=logs[entry_id].log_date,
            field=obs.field, label=MARKET_LABELS.get(obs.field, obs.field),
            value=obs.value, snippet=obs.source_snippet,
        ))
    out.sort(key=lambda m: (m.log_date or date.min, m.entry_id), reverse=True)
    return out


@router.get("/criteria", response_model=List[CriterionOut])
def list_criteria(db: Session = Depends(get_db)):
    """Active standing rules, newest first."""
    rows = (
        db.query(IntelCriterion)
        .filter(IntelCriterion.active.is_(True))
        .order_by(IntelCriterion.created_at.desc())
        .all()
    )
    return [
        CriterionOut(
            id=c.id, statement=c.statement, criterion_type=c.criterion_type,
            active=c.active, created_at=c.created_at.isoformat() if c.created_at else "",
        )
        for c in rows
    ]


@router.post("/criteria", response_model=CriterionOut, status_code=201)
def create_criterion(payload: CriterionIn, db: Session = Depends(get_db)):
    """Save a standing rule (from the 'save as standing rule?' suggestion)."""
    c = save_criterion(db, payload.statement, payload.criterion_type)
    return CriterionOut(
        id=c.id, statement=c.statement, criterion_type=c.criterion_type,
        active=c.active, created_at=c.created_at.isoformat() if c.created_at else "",
    )
