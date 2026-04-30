# backend/app/schemas/company.py
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class CompanyBase(BaseModel):
    company_id: str
    name: str
    industry: str
    description: Optional[str] = None
    current_headcount: Optional[int] = None
    headcount_12mo_ago: Optional[int] = None
    open_positions: int = 0
    current_address: Optional[str] = None
    current_submarket: Optional[str] = None
    current_sf: Optional[int] = None
    lease_expiry_date: Optional[date] = None
    lease_expiry_months: Optional[int] = None
    lease_expiry_source: Optional[str] = None
    lease_expiry_last_verified: Optional[date] = None
    expansion_signal: bool = False
    contraction_signal: bool = False
    relocation_signal: bool = False
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None
    tenant_representative: Optional[str] = None
    current_rent_psf: Optional[float] = None
    future_move_flag: Optional[bool] = None
    future_move_type: Optional[str] = None
    linked_property_id: Optional[int] = None
    lease_trajectory: str = "AUTO"


class CompanyCreate(CompanyBase):
    pass


class CompanyOut(CompanyBase):
    id: int
    headcount_growth_pct: Optional[float]
    hiring_velocity: Optional[float]
    sf_per_head: Optional[float]
    estimated_sf_needed: Optional[int]
    sig_headcount_growth: float
    sig_hiring_velocity: float
    sig_lease_expiry: float
    sig_space_utilization: float
    sig_geo_clustering: float
    opportunity_score: float
    priority: str
    signals_scored_count: int
    insufficient_data: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CompanyListOut(BaseModel):
    id: int
    company_id: str
    name: str
    industry: str

    # Contact
    primary_contact_name: Optional[str] = None   # contact_name
    primary_contact_phone: Optional[str] = None  # contact_phone
    primary_contact_title: Optional[str] = None  # contact_title

    # Space & financials
    current_headcount: Optional[int] = None
    current_sf: Optional[int] = None             # current_sf_leased
    current_rent_psf: Optional[float] = None
    current_submarket: Optional[str] = None

    # Broker rep
    tenant_representative: Optional[str] = None

    # Lease timing
    lease_expiry_months: Optional[int] = None    # months_until_lease_expiry
    lease_expiry_date: Optional[date] = None     # next_break_date
    lease_expiry_source: Optional[str] = None

    # Move intent
    future_move_flag: Optional[bool] = None
    future_move_type: Optional[str] = None

    # Lease trajectory (broker-set override for SF projection)
    lease_trajectory: str = "AUTO"

    # Signals & scoring
    headcount_growth_pct: Optional[float] = None  # growth_rate
    expansion_signal: bool = False
    contraction_signal: bool = False
    opportunity_score: float = 0.0               # composite_score
    priority: str = "IGNORE"
    signals_scored_count: int = 0
    insufficient_data: bool = False

    class Config:
        from_attributes = True
