from datetime import date
from typing import List, Optional
from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_properties: int
    total_companies: int
    total_opportunities: int
    immediate_count: int
    high_count: int
    pre_market_count: int
    tenant_driven_count: int
    active_mispriced_count: int
    avg_prediction_score: float
    avg_signal_score: float


class CallTarget(BaseModel):
    rank: int
    opportunity_id: str
    deal_type: str
    priority: str
    score: float
    confidence_level: str
    property_id: Optional[str] = None
    company_id: Optional[str] = None
    property_address: Optional[str]
    property_submarket: Optional[str]
    property_id: Optional[str] = None
    company_name: Optional[str]
    company_id: Optional[str] = None
    owner_name: Optional[str]
    thesis: str
    next_action: str
    call_script: Optional[str]
    estimated_commission: Optional[float]


class TenantMatchTarget(BaseModel):
    rank: int
    property_id: str
    address: str
    submarket: str
    asset_class: str
    total_sf: int
    owner_name: str
    vacancy_pct: Optional[float]
    sf_avail: Optional[int]
    tenant_match_score: float
    in_place_rent_psf: Optional[float]
    market_rent_psf: Optional[float]


class TenantMatchAction(BaseModel):
    """One property+tenant pair for Section A of the daily briefing."""
    property_id:   str
    address:       str
    submarket:     str
    sf_avail:      Optional[int]
    landlord_representative: Optional[str]
    listed_for_sale: bool
    outreach_type: str  # tenant_match | for_sale_vacancy
    target_type:   str  # broker | owner
    # Tenant (company) data
    tenant_company_id:  str
    tenant_name:        str
    tenant_industry:    str
    tenant_headcount:   Optional[int]
    tenant_sf_needed:   int
    match_score:        float
    lease_expiry_months: Optional[int]
    # UI state
    contact_status: str  # NOT_CONTACTED | CONTACTED | FOLLOW_UP


class AcquisitionTarget(BaseModel):
    """One property for Section B of the daily briefing."""
    property_id:  str
    address:      str
    submarket:    str
    year_built:   Optional[int]
    total_sf:     int
    vacancy_pct:  Optional[float]
    signal_score: float
    dominant_signal: Optional[str]  # human-readable e.g. "Debt Pressure"
    asking_price:    Optional[float]
    estimated_value: Optional[float]
    target_type:  str   # sales_broker | owner
    owner_name:   str
    sales_contact: Optional[str]
    contact_status: str  # NOT_CONTACTED | CONTACTED | FOLLOW_UP


class ExpiredLease(BaseModel):
    """A tenant company whose lease has expired (lease_expiry_date <= today)."""
    company_id: str
    name:       str
    industry:   str
    sf_needed:  Optional[int]
    submarket:  Optional[str]
    headcount:  Optional[int]


class DailyBriefing(BaseModel):
    briefing_date: date
    stats: DashboardStats
    immediate_deals: List[CallTarget]
    high_priority_deals: List[CallTarget]
    pre_market_predictions: List[CallTarget]
    tenant_opportunities: List[CallTarget]
    tenant_match_properties: List[TenantMatchTarget]
    tenant_match_actions: List[TenantMatchAction] = []
    acquisition_targets:  List[AcquisitionTarget] = []
    expired_leases: List[ExpiredLease] = []
    signal_refresh_timestamp: Optional[str] = None
