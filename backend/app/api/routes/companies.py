from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.schemas.company import CompanyOut, CompanyListOut
from app.services import signal_engine as se
from app.services.scoring_model import score_property

router = APIRouter(prefix="/companies", tags=["companies"])

CURRENT_YEAR = 2026


class CompanyManualCreate(BaseModel):
    name: str
    industry: str
    description: Optional[str] = None
    current_headcount: int
    headcount_12mo_ago: Optional[int] = None
    open_positions: int = 0
    current_address: Optional[str] = None
    current_submarket: Optional[str] = None
    current_sf: Optional[int] = None
    lease_expiry_months: Optional[int] = None
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None


def _run_signals(company: Company) -> None:
    result = se.compute_tenant_opportunity_score(
        company.headcount_growth_pct,
        company.open_positions or 0,
        company.current_headcount or 1,
        company.lease_expiry_months,
        company.current_sf,
        company.current_submarket,
        nearby_company_count=1,
    )
    breakdown = result["breakdown"]
    company.sig_headcount_growth  = breakdown["headcount_growth"]
    company.sig_hiring_velocity   = breakdown["hiring_velocity"]
    company.sig_lease_expiry      = breakdown["lease_expiry"]
    company.sig_space_utilization = breakdown["space_utilization"]
    company.sig_geo_clustering    = breakdown["geo_clustering"]

    composite = result["composite"]
    company.opportunity_score = composite

    if composite >= 75:
        company.priority = "IMMEDIATE"
    elif composite >= 62:
        company.priority = "HIGH"
    elif composite >= 42:
        company.priority = "WORKABLE"
    else:
        company.priority = "IGNORE"

    # Derived fields
    if company.current_headcount:
        if company.headcount_12mo_ago and company.headcount_12mo_ago > 0:
            company.headcount_growth_pct = round(
                (company.current_headcount - company.headcount_12mo_ago)
                / company.headcount_12mo_ago * 100, 1
            )
        if company.current_headcount > 0:
            company.hiring_velocity = round(
                (company.open_positions or 0) / company.current_headcount * 100, 1
            )
        if company.current_sf and company.current_headcount > 0:
            company.sf_per_head = round(company.current_sf / company.current_headcount, 1)

    # Set expansion signal
    company.expansion_signal = (
        (company.headcount_growth_pct or 0) >= 15
        and (company.lease_expiry_months or 999) <= 24
        and (company.sf_per_head or 999) <= 150
    )


@router.post("/", response_model=CompanyOut)
def create_company(payload: CompanyManualCreate, db: Session = Depends(get_db)):
    """Manually add a new company. Signals are computed immediately after creation."""

    # Auto-generate company_id (CO-XXX)
    existing_ids = [c.company_id for c in db.query(Company.company_id).all()]
    nums = []
    for cid in existing_ids:
        try:
            nums.append(int(cid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    next_num = (max(nums) + 1) if nums else 1
    company_id = f"CO-{next_num:03d}"

    # Derived fields
    growth_pct = None
    if payload.headcount_12mo_ago and payload.headcount_12mo_ago > 0:
        growth_pct = round(
            (payload.current_headcount - payload.headcount_12mo_ago)
            / payload.headcount_12mo_ago * 100, 1
        )

    hiring_velocity = None
    if payload.current_headcount > 0:
        hiring_velocity = round(payload.open_positions / payload.current_headcount * 100, 1)

    sf_per_head = None
    if payload.current_sf and payload.current_headcount > 0:
        sf_per_head = round(payload.current_sf / payload.current_headcount, 1)

    estimated_sf_needed = None
    if payload.current_headcount:
        growth_factor = 1 + ((growth_pct or 0) / 100.0) * 1.25
        estimated_sf_needed = int(payload.current_headcount * growth_factor * 175)

    company = Company(
        company_id            = company_id,
        name                  = payload.name,
        industry              = payload.industry,
        description           = payload.description,
        current_headcount     = payload.current_headcount,
        headcount_12mo_ago    = payload.headcount_12mo_ago,
        headcount_growth_pct  = growth_pct,
        open_positions        = payload.open_positions,
        hiring_velocity       = hiring_velocity,
        current_address       = payload.current_address,
        current_submarket     = payload.current_submarket,
        current_sf            = payload.current_sf,
        sf_per_head           = sf_per_head,
        lease_expiry_months   = payload.lease_expiry_months,
        estimated_sf_needed   = estimated_sf_needed,
        primary_contact_name  = payload.primary_contact_name,
        primary_contact_title = payload.primary_contact_title,
        primary_contact_phone = payload.primary_contact_phone,
        linkedin_url          = payload.linkedin_url,
        website               = payload.website,
    )
    db.add(company)
    db.flush()

    # Run signals immediately
    _run_signals(company)
    db.commit()
    db.refresh(company)
    return company


@router.get("/", response_model=List[CompanyListOut])
def list_companies(
    submarket: Optional[str] = None,
    priority: Optional[str] = None,
    expansion_only: bool = False,
    min_score: Optional[float] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Company)
    if submarket:
        q = q.filter(Company.current_submarket == submarket)
    if priority:
        q = q.filter(Company.priority == priority)
    if expansion_only:
        q = q.filter(Company.expansion_signal == True)
    if min_score is not None:
        q = q.filter(Company.opportunity_score >= min_score)
    companies = q.order_by(Company.opportunity_score.desc()).all()
    return companies


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: str, db: Session = Depends(get_db)):
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.post("/refresh-signals", response_model=dict)
def refresh_all_signals(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    for c in companies:
        _run_signals(c)
    db.commit()
    return {"refreshed": len(companies)}
