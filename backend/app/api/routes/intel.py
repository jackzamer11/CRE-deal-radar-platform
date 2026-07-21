import json
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.intel import IntelOpportunity
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
