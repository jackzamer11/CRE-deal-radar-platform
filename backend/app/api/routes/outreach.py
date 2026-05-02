from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.outreach_log import OutreachLog
from app.schemas.outreach import OutreachLogUpdate, OutreachLogOut

router = APIRouter(prefix="/outreach-log", tags=["outreach"])


@router.patch("/{log_id}", response_model=OutreachLogOut)
def update_outreach_log(
    log_id: int,
    payload: OutreachLogUpdate,
    db: Session = Depends(get_db),
):
    """Update outcome notes, email_sent, call_made, or mark contacted."""
    log = db.query(OutreachLog).filter(OutreachLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")

    if payload.outcome_notes is not None:
        log.outcome_notes = payload.outcome_notes
    if payload.email_sent is not None:
        log.email_sent = payload.email_sent
    if payload.call_made is not None:
        log.call_made = payload.call_made
    if payload.marked_contacted is not None:
        log.marked_contacted = payload.marked_contacted
        if payload.marked_contacted and log.contacted_at is None:
            log.contacted_at = datetime.utcnow()

    db.commit()
    db.refresh(log)
    return log


@router.get("/{log_id}", response_model=OutreachLogOut)
def get_outreach_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(OutreachLog).filter(OutreachLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")
    return log
