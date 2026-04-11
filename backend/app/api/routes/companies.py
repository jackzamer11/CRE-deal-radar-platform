from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.schemas.company import CompanyOut, CompanyListOut
from app.services import signal_engine as se
from app.services.scoring_model import score_property

router = APIRouter(prefix="/companies", tags=["companies"])


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
