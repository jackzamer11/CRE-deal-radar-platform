"""
Persistent outreach draft storage.

Each property+company pair has at most one draft per outreach_type stored here.
Drafts are loaded instantly (no GPT call) until explicitly reset.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.outreach_draft import OutreachDraft

router = APIRouter(prefix="/outreach-drafts", tags=["outreach-drafts"])


class DraftOut(BaseModel):
    id: int
    property_id: str
    company_id: Optional[str]
    outreach_type: str
    direction: Optional[str] = 'property_side'
    subject: str
    body: str
    call_script_opening:    Optional[str]
    call_script_core:       Optional[str]
    call_script_pain_probe: Optional[str]
    call_script_close:      Optional[str]
    target_type: str
    recipient_name:   Optional[str]
    recipient_email:  Optional[str]
    internal_context: Optional[str]
    score:    Optional[float]
    priority: Optional[str]
    created_at:    datetime
    last_viewed_at: datetime

    class Config:
        from_attributes = True


class IntelPayload(BaseModel):
    property_id: str
    company_id:  Optional[str] = None
    direction:   str = "property_side"


class DraftCreate(BaseModel):
    property_id: str
    company_id:  Optional[str] = None
    outreach_type: str
    direction: Optional[str] = 'property_side'
    subject: str
    body: str
    call_script_opening:    Optional[str] = None
    call_script_core:       Optional[str] = None
    call_script_pain_probe: Optional[str] = None
    call_script_close:      Optional[str] = None
    target_type: str
    recipient_name:   Optional[str] = None
    recipient_email:  Optional[str] = None
    internal_context: Optional[str] = None
    score:    Optional[float] = None
    priority: Optional[str]  = None


@router.get("/{property_id}", response_model=List[DraftOut])
def list_drafts_for_property(property_id: str, db: Session = Depends(get_db)):
    """Return all drafts for a property (any company, any type)."""
    drafts = (
        db.query(OutreachDraft)
        .filter(OutreachDraft.property_id == property_id)
        .order_by(OutreachDraft.last_viewed_at.desc())
        .all()
    )
    now = datetime.utcnow()
    for d in drafts:
        d.last_viewed_at = now
    db.commit()
    return drafts


@router.get("/{property_id}/{company_id}", response_model=Optional[DraftOut])
def get_draft_for_pair(
    property_id: str,
    company_id: str,
    outreach_type: Optional[str] = None,
    direction: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return the most recent draft for a property+company pair."""
    q = db.query(OutreachDraft).filter(
        OutreachDraft.property_id == property_id,
        OutreachDraft.company_id  == company_id,
    )
    if outreach_type:
        q = q.filter(OutreachDraft.outreach_type == outreach_type)
    if direction:
        q = q.filter(OutreachDraft.direction == direction)
    draft = q.order_by(OutreachDraft.last_viewed_at.desc()).first()
    if not draft:
        return None
    draft.last_viewed_at = datetime.utcnow()
    db.commit()
    return draft


@router.post("/", response_model=DraftOut)
def save_draft(payload: DraftCreate, db: Session = Depends(get_db)):
    """Upsert: if a draft for this property+company+type exists, replace it; else insert."""
    existing = (
        db.query(OutreachDraft)
        .filter(
            OutreachDraft.property_id  == payload.property_id,
            OutreachDraft.company_id   == payload.company_id,
            OutreachDraft.outreach_type == payload.outreach_type,
            OutreachDraft.direction == (payload.direction or 'property_side'),
        )
        .first()
    )
    now = datetime.utcnow()
    if existing:
        existing.subject               = payload.subject
        existing.body                  = payload.body
        existing.call_script_opening   = payload.call_script_opening
        existing.call_script_core      = payload.call_script_core
        existing.call_script_pain_probe = payload.call_script_pain_probe
        existing.call_script_close     = payload.call_script_close
        existing.target_type           = payload.target_type
        existing.recipient_name        = payload.recipient_name
        existing.recipient_email       = payload.recipient_email
        existing.internal_context      = payload.internal_context
        existing.score                 = payload.score
        existing.priority              = payload.priority
        existing.direction             = payload.direction or 'property_side'
        existing.created_at            = now
        existing.last_viewed_at        = now
        db.commit()
        db.refresh(existing)
        return existing

    draft = OutreachDraft(
        property_id            = payload.property_id,
        company_id             = payload.company_id,
        outreach_type          = payload.outreach_type,
        direction              = payload.direction or 'property_side',
        subject                = payload.subject,
        body                   = payload.body,
        call_script_opening    = payload.call_script_opening,
        call_script_core       = payload.call_script_core,
        call_script_pain_probe = payload.call_script_pain_probe,
        call_script_close      = payload.call_script_close,
        target_type            = payload.target_type,
        recipient_name         = payload.recipient_name,
        recipient_email        = payload.recipient_email,
        internal_context       = payload.internal_context,
        score                  = payload.score,
        priority               = payload.priority,
        created_at             = now,
        last_viewed_at         = now,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/search-intelligence", tags=["outreach-drafts"])
def search_intelligence(payload: IntelPayload, db: Session = Depends(get_db)):
    """Run web searches and return structured findings for intel review panel."""
    import os as _os
    from app.models.property import Property
    from app.models.company import Company

    property_id = payload.property_id
    company_id  = payload.company_id
    direction   = payload.direction

    print(f"[search-intelligence] ANTHROPIC_API_KEY set: {bool(_os.environ.get('ANTHROPIC_API_KEY'))}")
    print(f"[search-intelligence] property_id={property_id} company_id={company_id} direction={direction}")

    findings = []

    if direction == "property_side":
        prop = db.query(Property).filter(Property.property_id == property_id).first()
        if prop:
            from app.services.property_outreach_service import search_property_intelligence
            prop_dict = {
                "address":    prop.address,
                "submarket":  prop.submarket,
                "owner_name": prop.owner_name,
            }
            findings = search_property_intelligence(prop_dict)
    else:
        # tenant_side — search company intelligence
        if company_id:
            comp = db.query(Company).filter(Company.company_id == company_id).first()
            if comp:
                from app.services.outreach_service import search_company_intelligence
                findings = search_company_intelligence(comp.name)

    print(f"[search-intelligence] findings count: {len(findings)}")
    return {"findings": findings}


@router.delete("/{draft_id}", response_model=dict)
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    """Delete a draft (used by Reset Draft)."""
    draft = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    db.delete(draft)
    db.commit()
    return {"deleted": draft_id}
