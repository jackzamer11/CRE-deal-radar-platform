from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.opportunity import Opportunity
from app.models.property import Property
from app.models.company import Company
from app.models.activity import ActivityLog
from app.schemas.opportunity import OpportunityOut, OpportunityListOut, StageUpdate

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


def _enrich(opp: Opportunity) -> OpportunityOut:
    out = OpportunityOut.model_validate(opp)
    if opp.property:
        out.property_address    = opp.property.address
        out.property_submarket  = opp.property.submarket
    if opp.company:
        out.company_name = opp.company.name
    return out


@router.get("/", response_model=List[OpportunityListOut])
def list_opportunities(
    priority: Optional[str] = None,
    deal_type: Optional[str] = None,
    stage: Optional[str] = None,
    submarket: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(Opportunity)
    if active_only:
        q = q.filter(Opportunity.is_active == True)
    if priority:
        q = q.filter(Opportunity.priority == priority)
    if deal_type:
        q = q.filter(Opportunity.deal_type == deal_type)
    if stage:
        q = q.filter(Opportunity.stage == stage)
    opps = q.order_by(Opportunity.score.desc()).all()

    result = []
    for opp in opps:
        item = OpportunityListOut.model_validate(opp)
        if opp.property:
            item.property_address   = opp.property.address
            item.property_submarket = opp.property.submarket
        if opp.company:
            item.company_name = opp.company.name
        result.append(item)
    return result


@router.get("/{opportunity_id}", response_model=OpportunityOut)
def get_opportunity(opportunity_id: str, db: Session = Depends(get_db)):
    opp = db.query(Opportunity).filter(Opportunity.opportunity_id == opportunity_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return _enrich(opp)


@router.patch("/{opportunity_id}/stage", response_model=OpportunityOut)
def update_stage(opportunity_id: str, payload: StageUpdate, db: Session = Depends(get_db)):
    valid_stages = ["IDENTIFIED", "CONTACTED", "ACTIVE", "UNDER_LOI", "CLOSED", "DEAD"]
    if payload.stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Stage must be one of {valid_stages}")

    opp = db.query(Opportunity).filter(Opportunity.opportunity_id == opportunity_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    old_stage = opp.stage
    opp.stage = payload.stage
    if payload.stage == "DEAD":
        opp.is_active = False

    if payload.note:
        log = ActivityLog(
            opportunity_id=opp.id,
            property_id=opp.property_id,
            company_id=opp.company_id,
            action_type="NOTE",
            action_taken=f"Stage updated: {old_stage} → {payload.stage}",
            outcome=payload.note,
        )
        db.add(log)

    db.commit()
    db.refresh(opp)
    return _enrich(opp)
