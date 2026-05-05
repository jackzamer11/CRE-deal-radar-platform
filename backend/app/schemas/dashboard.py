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
    property_address: Optional[str]
    property_submarket: Optional[str]
    company_name: Optional[str]
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


class DailyBriefing(BaseModel):
    briefing_date: date
    stats: DashboardStats
    immediate_deals: List[CallTarget]
    high_priority_deals: List[CallTarget]
    pre_market_predictions: List[CallTarget]
    tenant_opportunities: List[CallTarget]
    tenant_match_properties: List[TenantMatchTarget]
    signal_refresh_timestamp: Optional[str] = None
