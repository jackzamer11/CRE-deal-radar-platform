# backend/app/schemas/company.py
from datetime import date, datetime
from typing import Optional, TYPE_CHECKING
from pydantic import BaseModel, model_validator


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
    # Real occupied SF (CoStar "SF Occupied" / manual). Never calculated. Null = unknown.
    current_sf_occupied: Optional[int] = None
    current_building_class: Optional[str] = None
    lease_expiry_date: Optional[date] = None
    lease_expiry_months: Optional[int] = None
    lease_expiry_source: Optional[str] = None
    lease_expiry_last_verified: Optional[date] = None
    # Derived (not stored): tenant is within the final 1-3 months of its lease.
    late_stage: bool = False
    # Derived: thin-data company with near-term expiry (3-12 months) — qualifies for outreach override.
    expiry_priority_override: bool = False

    @model_validator(mode="after")
    def _decay_lease_months(self) -> "CompanyBase":
        if self.lease_expiry_date:
            today = date.today()
            ld = self.lease_expiry_date
            self.lease_expiry_months = max(0, (ld.year - today.year) * 12 + (ld.month - today.month))
        # Null-safe: None months → late_stage stays False.
        m = self.lease_expiry_months
        self.late_stage = m is not None and 0 < m <= 3
        return self
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


class MatchedProperty(BaseModel):
    property_id: str
    address: str
    submarket: str
    sf_avail: Optional[int]
    vacancy_pct: Optional[float]
    in_place_rent_psf: Optional[float]
    market_rent_psf: Optional[float]
    landlord_representative: Optional[str]
    landlord_rep_contact: Optional[str]
    sales_contact: Optional[str]
    listed_for_sale: bool
    match_score: float
    match_reasons: list[str]
    adjacent_submarket: bool = False
    is_medical: bool = False


class CompanyOut(CompanyBase):
    id: int
    headcount_growth_pct: Optional[float]
    hiring_velocity: Optional[float]
    sf_per_head: Optional[float]
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
    last_modified_by_user: Optional[datetime] = None
    matched_properties: list[MatchedProperty] = []
    is_medical: bool = False

    # Snooze state (null = active)
    snoozed_until:        Optional[date] = None
    snooze_reason:        Optional[str]  = None
    returned_from_snooze: Optional[bool] = None

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
    current_sf_occupied: Optional[int] = None    # real occupied SF; null = unknown
    current_building_class: Optional[str] = None
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

    # Derived (not stored): tenant is within the final 1-3 months of its lease.
    late_stage: bool = False
    # Derived: thin-data company with near-term expiry (3-12 months) — qualifies for outreach override.
    expiry_priority_override: bool = False

    # Snooze state (null = active) — agent reads snoozed_until to skip snoozed companies
    snoozed_until:        Optional[date] = None
    snooze_reason:        Optional[str]  = None
    returned_from_snooze: Optional[bool] = None
    is_medical:           bool           = False

    # Computed from tenant_representative — not stored in DB
    rep_class: str = "BLANK"

    @model_validator(mode="after")
    def _compute_rep_class(self) -> "CompanyListOut":
        from app.services.rep_classification import classify_rep
        self.rep_class = classify_rep(self.tenant_representative)
        if self.lease_expiry_date:
            today = date.today()
            ld = self.lease_expiry_date
            self.lease_expiry_months = max(0, (ld.year - today.year) * 12 + (ld.month - today.month))
        # Null-safe: None months → late_stage stays False.
        m = self.lease_expiry_months
        self.late_stage = m is not None and 0 < m <= 3
        return self

    class Config:
        from_attributes = True
