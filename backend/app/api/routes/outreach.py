from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.models.outreach_log import OutreachLog
from app.schemas.outreach import OutreachLogUpdate, OutreachLogOut
from app.services.opportunity_stage_service import advance_opportunity_to_contacted

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
        # Save-and-advance is one logical operation: when this outreach is marked
        # contacted, advance the matching Opportunity IDENTIFIED -> CONTACTED in the
        # SAME transaction. Errors are surfaced (not swallowed) so we never report
        # success while leaving the stage stale.
        if payload.marked_contacted:
            # Resolve the paired tenant company to its integer FK so the
            # advance can do a precise property+company lookup.
            # pair_company_id (string like "CO-021") is passed from the
            # frontend for tenant-match property-side outreach; for company-side
            # outreach or when absent, fall back to log.company_id.
            resolved_company_id = log.company_id
            if payload.pair_company_id:
                co = db.query(Company).filter(
                    Company.company_id == payload.pair_company_id
                ).first()
                if co:
                    resolved_company_id = co.id
                else:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "advance_opportunity: pair_company_id=%r not found; "
                        "falling back to log.company_id=%r",
                        payload.pair_company_id, log.company_id,
                    )
            advance_opportunity_to_contacted(
                db,
                property_id=log.property_id,
                company_id=resolved_company_id,
            )

    db.commit()
    db.refresh(log)
    return log


@router.get("/{log_id}", response_model=OutreachLogOut)
def get_outreach_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(OutreachLog).filter(OutreachLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")
    return log
