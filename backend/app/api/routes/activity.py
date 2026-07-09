import re
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.activity import ActivityLog

router = APIRouter(prefix="/activity", tags=["activity"])

# Pipeline stages — current state only; an entry can move in any direction.
VALID_STAGES = ["Sent", "Replied", "Interested", "In Play", "Not Interested", "Dormant"]
# Stages that require a revisit reminder when selected.
REVISIT_STAGES = {"Dormant", "Not Interested"}

# "Emailed <Name> re <Company>" / "Called <Name> re <Company>" (case-insensitive).
# Captures the contact name between the verb and an optional " re <Company>" tail.
_CONTACT_RE = re.compile(r"^\s*(?:emailed|called)\s+(.+?)(?:\s+re\s+.+)?\s*$", re.IGNORECASE)


def parse_contact_name(action_taken: Optional[str]) -> Optional[str]:
    """Extract the contact name from a standard outreach log line.

    Handles "Emailed <Name> re <Company>" and "Called <Name> re <Company>"
    (the format written by the Outreach Draft modal). Returns None for any line
    that does not match, or whose name is the generic "contact" placeholder, so
    callers can fall back to displaying the entry as-is.
    """
    if not action_taken:
        return None
    m = _CONTACT_RE.match(action_taken)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    if not name or name.lower() == "contact":
        return None
    return name


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

    # Pipeline tracking
    stage:           str = "Sent"
    next_touch_date: Optional[date] = None

    outreach_type:  Optional[str] = None
    target_type:    Optional[str] = None
    contact_method: Optional[str] = None
    subject:        Optional[str] = None
    notes:          Optional[str] = None

    # Denormalized
    property_address: Optional[str] = None
    company_name: Optional[str] = None
    opportunity_ref: Optional[str] = None
    contact_name: Optional[str] = None

    class Config:
        from_attributes = True


def _to_out(log: ActivityLog) -> "ActivityOut":
    """Build an ActivityOut with denormalized + parsed fields from an ORM row."""
    item = ActivityOut.model_validate(log)
    # Legacy rows created before the stage column existed read back as None.
    if not item.stage:
        item.stage = "Sent"
    if log.property:
        item.property_address = log.property.address
    if log.company:
        item.company_name = log.company.name
    if log.opportunity:
        item.opportunity_ref = log.opportunity.opportunity_id
    item.contact_name = parse_contact_name(log.action_taken)
    return item


class ActivityCreate(BaseModel):
    log_date: Optional[date] = None
    opportunity_id: Optional[int] = None
    property_id: Optional[int] = None
    company_id: Optional[int] = None
    action_type: str
    action_taken: str
    outcome: Optional[str] = None
    stage: Optional[str] = "Sent"
    next_touch_date: Optional[date] = None
    follow_up_date: Optional[date] = None
    follow_up_action: Optional[str] = None
    # Outreach-specific fields (optional)
    outreach_type:  Optional[str] = None
    target_type:    Optional[str] = None
    contact_method: Optional[str] = None
    subject:        Optional[str] = None


@router.get("/", response_model=List[ActivityOut])
def list_activity(
    since: Optional[date] = None,
    action_type: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = Query(1000, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(ActivityLog)
    if since:
        q = q.filter(ActivityLog.log_date >= since)
    if action_type:
        q = q.filter(ActivityLog.action_type == action_type)
    if stage:
        q = q.filter(ActivityLog.stage == stage)
    logs = q.order_by(ActivityLog.created_at.desc()).limit(limit).all()
    return [_to_out(log) for log in logs]


@router.get("/re-engage", response_model=List[ActivityOut])
def list_re_engage(db: Session = Depends(get_db)):
    """Activity contacts whose next_touch_date is today or past due.

    Powers the Daily Briefing "Re-engage Today" section. Entries with no
    next_touch_date never appear; an entry moved back to 'Sent' (which clears its
    next_touch_date) drops off automatically. Returns [] when nothing is due —
    never 500s on empty data.
    """
    today = date.today()
    logs = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.next_touch_date.isnot(None),
            ActivityLog.next_touch_date <= today,
            (ActivityLog.stage.is_(None)) | (ActivityLog.stage != "Sent"),
        )
        .order_by(ActivityLog.next_touch_date.asc())
        .all()
    )
    return [_to_out(log) for log in logs]


class ActivityNoteUpdate(BaseModel):
    notes: str


@router.patch("/{entry_id}/notes", response_model=ActivityOut)
def update_activity_notes(
    entry_id: int,
    payload: ActivityNoteUpdate,
    db: Session = Depends(get_db),
):
    """Update (or set) the broker note on an activity log entry."""
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")
    log.notes = payload.notes
    db.commit()
    db.refresh(log)
    return _to_out(log)


class ActivityStageUpdate(BaseModel):
    stage: str
    next_touch_date: Optional[date] = None


@router.patch("/{entry_id}/stage", response_model=ActivityOut)
def update_activity_stage(
    entry_id: int,
    payload: ActivityStageUpdate,
    db: Session = Depends(get_db),
):
    """Move an activity entry to a new stage (any direction, current-state only).

    - Moving to 'Sent' clears next_touch_date so the entry leaves Re-engage Today.
    - Otherwise next_touch_date is set from the payload (may be null). Dormant /
      Not Interested expect a revisit date from the inline picker, but a missing
      date is tolerated rather than erroring.
    """
    if payload.stage not in VALID_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage '{payload.stage}'. Must be one of: {', '.join(VALID_STAGES)}",
        )
    log = db.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Activity log entry not found")

    log.stage = payload.stage
    if payload.stage == "Sent":
        # Re-engaged — drop the reminder so it disappears from Re-engage Today.
        log.next_touch_date = None
    else:
        log.next_touch_date = payload.next_touch_date
    db.commit()
    db.refresh(log)
    return _to_out(log)


@router.post("/", response_model=ActivityOut)
def create_activity(payload: ActivityCreate, db: Session = Depends(get_db)):
    stage = payload.stage if payload.stage in VALID_STAGES else "Sent"
    log = ActivityLog(
        log_date       = payload.log_date or date.today(),
        opportunity_id = payload.opportunity_id,
        property_id    = payload.property_id,
        company_id     = payload.company_id,
        action_type    = payload.action_type,
        action_taken   = payload.action_taken,
        outcome        = payload.outcome,
        stage          = stage,
        next_touch_date = None if stage == "Sent" else payload.next_touch_date,
        follow_up_date = payload.follow_up_date,
        follow_up_action = payload.follow_up_action,
        outreach_type  = payload.outreach_type,
        target_type    = payload.target_type,
        contact_method = payload.contact_method,
        subject        = payload.subject,
        created_by     = "user",
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return _to_out(log)
