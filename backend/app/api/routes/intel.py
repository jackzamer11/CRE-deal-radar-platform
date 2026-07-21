import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.intel import IntelCriterion, IntelFeedback, IntelOpportunity
from app.services.intel_feedback_service import (
    FeedbackError,
    disposition_opportunity,
    save_criterion,
)
from app.services.intel_signal_service import generate_opportunities

router = APIRouter(prefix="/intel", tags=["intel"])


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
    )


@router.post("/opportunities/generate", response_model=List[IntelOpportunityOut])
def generate(db: Session = Depends(get_db)):
    """Run the signal rules and upsert opportunities (idempotent). Returns the
    opportunities touched this run, highest score first."""
    touched = generate_opportunities(db)
    touched.sort(key=lambda o: o.score, reverse=True)
    return [_to_out(o) for o in touched]


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


class DispositionOut(BaseModel):
    opportunity: IntelOpportunityOut
    # Non-null when a durable-policy reason has recurred enough to suggest a rule.
    suggested_rule: Optional[str] = None


class HistoryItemOut(IntelOpportunityOut):
    disposition: Optional[str] = None
    reason_category: Optional[str] = None
    reason_text: Optional[str] = None


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
        )
    except FeedbackError as exc:
        # "Opportunity not found" → 404; validation problems → 400.
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return DispositionOut(opportunity=_to_out(opp), suggested_rule=suggested)


@router.get("/history", response_model=List[HistoryItemOut])
def history(db: Session = Depends(get_db)):
    """Dispositioned opportunities with their latest feedback, newest first."""
    rows = (
        db.query(IntelOpportunity)
        .filter(IntelOpportunity.status != "open")
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
        ))
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
