from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class SignalBreakdown(BaseModel):
    lease_rollover: float = 0.0
    vacancy_trend: float = 0.0
    ownership_duration: float = 0.0
    leasing_drought: float = 0.0
    capex_gap: float = 0.0
    hold_period: float = 0.0
    occupancy_decline: float = 0.0
    rent_stagnation: float = 0.0
    reinvestment_inactivity: float = 0.0
    debt_pressure: float = 0.0
    rent_gap: float = 0.0
    price_psf: float = 0.0
    dom_premium: float = 0.0
    cap_rate_spread: float = 0.0


class PropertyBase(BaseModel):
    property_id: str
    address: str
    submarket: str
    asset_class: str = "Class B"
    total_sf: int
    year_built: int
    last_renovation_year: Optional[int] = None
    owner_name: str
    owner_type: str = "LLC"
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None
    acquisition_date: Optional[date] = None
    acquisition_price: Optional[float] = None
    years_owned: Optional[float] = None
    asking_price: Optional[float] = None
    asking_price_psf: Optional[float] = None
    in_place_rent_psf: float
    market_rent_psf: float
    noi: Optional[float] = None
    cap_rate: Optional[float] = None
    market_cap_rate: float
    occupancy_pct: float
    vacancy_pct: float
    vacancy_12mo_ago: Optional[float] = None
    leased_sf: Optional[float] = None
    vacant_sf: Optional[float] = None
    sf_expiring_12mo: float = 0.0
    sf_expiring_24mo: float = 0.0
    lease_rollover_pct: float = 0.0
    last_lease_signed_date: Optional[date] = None
    years_since_last_lease: float = 0.0
    is_listed: bool = False
    listing_date: Optional[date] = None
    days_on_market: Optional[int] = None
    submarket_avg_dom: Optional[int] = None
    estimated_loan_maturity_year: Optional[int] = None
    estimated_ltv: Optional[float] = None
    notes: Optional[str] = None


class PropertyCreate(PropertyBase):
    pass


class PropertyOut(PropertyBase):
    id: int
    prediction_score: float
    owner_behavior_score: float
    mispricing_score: float
    signal_score: float
    priority: str
    deal_type: Optional[str] = None
    signal_breakdown: Optional[SignalBreakdown] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PropertyListOut(BaseModel):
    id: int
    property_id: str
    address: str
    submarket: str
    asset_class: str
    total_sf: int
    owner_name: str
    occupancy_pct: float
    years_owned: Optional[float]
    lease_rollover_pct: float
    prediction_score: float
    mispricing_score: float
    signal_score: float
    priority: str
    is_listed: bool
    notes: Optional[str]

    class Config:
        from_attributes = True
