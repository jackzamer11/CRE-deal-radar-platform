import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.models.outreach_log import OutreachLog
from app.schemas.outreach import OutreachLogUpdate, OutreachLogOut
from app.services.opportunity_stage_service import advance_opportunity_to_contacted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach-log", tags=["outreach"])


@router.patch("/{log_id}", response_model=OutreachLogOut)
def update_outreach_log(
    log_id: int,
    payload: OutreachLogUpdate,
    db: Session = Depends(get_db),
):
    """Update outcome notes, email_sent, call_made, or mark contacted."""
    # ── DIAG: received payload ────────────────────────────────────────────────
    logger.info(
        "[DIAG][PATCH /outreach-log/%s] received payload: marked_contacted=%r "
        "pair_company_id=%r outcome_notes=%r email_sent=%r call_made=%r",
        log_id, payload.marked_contacted, payload.pair_company_id,
        payload.outcome_notes, payload.email_sent, payload.call_made,
    )

    log = db.query(OutreachLog).filter(OutreachLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")

    # ── DIAG: log row found ───────────────────────────────────────────────────
    logger.info(
        "[DIAG][PATCH /outreach-log/%s] log row: log.property_id=%r log.company_id=%r "
        "log.outreach_type=%r",
        log_id, log.property_id, log.company_id, log.outreach_type,
    )

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
            # ── DIAG: entering advance block ──────────────────────────────────
            logger.info(
                "[DIAG][PATCH /outreach-log/%s] marked_contacted=True — entering advance block",
                log_id,
            )
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
                    logger.info(
                        "[DIAG][PATCH /outreach-log/%s] pair_company_id=%r resolved to integer FK %r",
                        log_id, payload.pair_company_id, resolved_company_id,
                    )
                else:
                    logger.warning(
                        "[DIAG][PATCH /outreach-log/%s] advance_opportunity: pair_company_id=%r "
                        "NOT FOUND in Company table; falling back to log.company_id=%r",
                        log_id, payload.pair_company_id, log.company_id,
                    )
            else:
                logger.info(
                    "[DIAG][PATCH /outreach-log/%s] no pair_company_id supplied; "
                    "using log.company_id=%r as-is",
                    log_id, log.company_id,
                )

            logger.info(
                "[DIAG][PATCH /outreach-log/%s] calling advance_opportunity_to_contacted("
                "property_id=%r, company_id=%r)",
                log_id, log.property_id, resolved_company_id,
            )
            advance_opportunity_to_contacted(
                db,
                property_id=log.property_id,
                company_id=resolved_company_id,
            )
            logger.info(
                "[DIAG][PATCH /outreach-log/%s] advance_opportunity_to_contacted returned OK",
                log_id,
            )
    else:
        logger.info(
            "[DIAG][PATCH /outreach-log/%s] marked_contacted not in payload — skipping advance",
            log_id,
        )

    db.commit()
    logger.info("[DIAG][PATCH /outreach-log/%s] db.commit() done", log_id)
    db.refresh(log)
    return log


@router.get("/{log_id}", response_model=OutreachLogOut)
def get_outreach_log(log_id: int, db: Session = Depends(get_db)):
    log = db.query(OutreachLog).filter(OutreachLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Outreach log entry not found")
    return log
