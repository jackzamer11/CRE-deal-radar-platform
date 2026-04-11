from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.activity import ActivityLog

router = APIRouter(prefix="/activity", tags=["activity"])


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

    # Denormalized
    property_address: Optional[str] = None
    company_name: Optional[str] = None
    opportunity_ref: Optional[str] = None

    class Config:
        from_attributes = True


class ActivityCreate(BaseModel):
    log_date: Optional[date] = None
    opportunity_id: Optional[int] = None
    property_id: Optional[int] = None
    company_id: Optional[int] = None
    action_type: str
    action_taken: str
    outcome: Optional[str] = None
    follow_up_date: Optional[date] = None
    follow_up_action: Optional[str] = None


@router.get("/", response_model=List[ActivityOut])
def list_activity(
    since: Optional[date] = None,
    action_type: Optional[str] = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(ActivityLog)
    if since:
        q = q.filter(ActivityLog.log_date >= since)
    if action_type:
        q = q.filter(ActivityLog.action_type == action_type)
    logs = q.order_by(ActivityLog.created_at.desc()).limit(limit).all()

    result = []
    for log in logs:
        item = ActivityOut.model_validate(log)
        if log.property:
            item.property_address = log.property.address
        if log.company:
            item.company_name = log.company.name
        if log.opportunity:
            item.opportunity_ref = log.opportunity.opportunity_id
        result.append(item)
    return result


@router.post("/", response_model=ActivityOut)
def create_activity(payload: ActivityCreate, db: Session = Depends(get_db)):
    log = ActivityLog(
        log_date       = payload.log_date or date.today(),
        opportunity_id = payload.opportunity_id,
        property_id    = payload.property_id,
        company_id     = payload.company_id,
        action_type    = payload.action_type,
        action_taken   = payload.action_taken,
        outcome        = payload.outcome,
        follow_up_date = payload.follow_up_date,
        follow_up_action = payload.follow_up_action,
        created_by     = "user",
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return ActivityOut.model_validate(log)
