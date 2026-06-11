from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, model_validator


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
    in_place_rent_source: Optional[str] = None
    in_place_rent_last_verified: Optional[date] = None
    occupancy_pct: Optional[float] = None
    vacancy_pct: Optional[float] = None
    vacancy_12mo_ago: Optional[float] = None
    leased_sf: Optional[float] = None
    vacant_sf: Optional[float] = None
    sf_expiring_12mo: float = 0.0
    sf_expiring_24mo: float = 0.0
    lease_rollover_pct: float = 0.0
    last_lease_signed_date: Optional[date] = None
    years_since_last_lease: float = 0.0
    listed_for_sale: bool = False
    listed_for_lease: bool = False
    listing_date: Optional[date] = None
    days_on_market: Optional[int] = None
    submarket_avg_dom: Optional[int] = None
    estimated_loan_maturity_year: Optional[int] = None
    estimated_ltv: Optional[float] = None
    notes: Optional[str] = None

    # Snooze
    snoozed_until:        Optional[date]  = None
    snooze_reason:        Optional[str]   = None
    returned_from_snooze: Optional[bool]  = None

    # Owner confirmed open to leasing while listed
    owner_confirmed_leasing:      bool          = False
    owner_confirmed_leasing_date: Optional[date] = None

    # CoStar enrichment fields
    star_rating: Optional[int] = None
    sf_avail: Optional[int] = None
    landlord_representative: Optional[str] = None
    landlord_rep_contact: Optional[str] = None
    sales_company: Optional[str] = None
    sales_contact: Optional[str] = None
    tenancy: Optional[str] = None
    stories: Optional[int] = None
    parking_ratio: Optional[float] = None


class PropertyCreate(PropertyBase):
    pass


class MatchedTenant(BaseModel):
    company_id: str
    name: str
    industry: str
    headcount: Optional[int]
    sf_needed: Optional[int]          # real occupied SF; None when unknown
    sf_display: str                   # "11,000 SF" when known, else "Unknown"
    submarket: Optional[str]
    match_score: float
    match_reasons: list[str]
    adjacent_submarket: bool = False
    is_medical: bool = False


class MatchedProperty(BaseModel):
    property_id: str
    address: str
    submarket: str
    sf_avail: Optional[int]
    match_score: float
    match_reasons: list[str]
    landlord_representative: Optional[str]
    landlord_rep_contact: Optional[str]
    sales_contact: Optional[str]
    listed_for_sale: bool
    is_medical: bool = False


class PropertyOut(PropertyBase):
    id: int
    prediction_score: float
    owner_behavior_score: float
    mispricing_score: float
    signal_score: float
    tenant_match_score: Optional[float] = None
    listing_rep_score: Optional[float] = None
    acquisition_score: Optional[float] = None
    dominant_score_type: Optional[str] = None
    priority: str
    deal_type: Optional[str] = None
    signal_breakdown: Optional[SignalBreakdown] = None
    signals_scored_count: int = 0
    insufficient_data: bool = False
    matched_tenants: list[MatchedTenant] = []
    created_at: datetime
    updated_at: datetime
    matched_tenants: list[MatchedTenant] = []
    is_medical: bool = False

    @model_validator(mode='before')
    @classmethod
    def derive_listed_for_lease(cls, data):
        if hasattr(data, '__dict__'):
            sf = getattr(data, 'sf_avail', None)
            object.__setattr__(data, 'listed_for_lease', bool(sf and sf > 0))
        elif isinstance(data, dict):
            sf = data.get('sf_avail')
            data['listed_for_lease'] = bool(sf and sf > 0)
        return data

    @model_validator(mode="after")
    def _derive_listed_for_lease(self) -> "PropertyOut":
        self.listed_for_lease = bool(self.sf_avail and self.sf_avail > 0)
        return self

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
    occupancy_pct: Optional[float]
    years_owned: Optional[float]
    lease_rollover_pct: float
    in_place_rent_psf: Optional[float] = None
    market_rent_psf: Optional[float] = None
    prediction_score: float
    mispricing_score: float
    signal_score: float
    tenant_match_score: Optional[float] = None
    listing_rep_score: Optional[float] = None
    acquisition_score: Optional[float] = None
    dominant_score_type: Optional[str] = None
    priority: str
    listed_for_sale: bool
    listed_for_lease: bool = False
    notes: Optional[str]
    star_rating: Optional[int] = None
    sf_avail: Optional[int] = None
    landlord_representative: Optional[str] = None
    landlord_rep_contact: Optional[str] = None
    sales_contact: Optional[str] = None
    signals_scored_count: int = 0
    insufficient_data: bool = False
    snoozed_until:        Optional[date] = None
    snooze_reason:        Optional[str]  = None
    returned_from_snooze: Optional[bool] = None
    owner_confirmed_leasing:      bool          = False
    owner_confirmed_leasing_date: Optional[date] = None
    is_medical:                   bool          = False

    @model_validator(mode="after")
    def _derive_listed_for_lease(self) -> "PropertyListOut":
        self.listed_for_lease = bool(self.sf_avail and self.sf_avail > 0)
        return self

    class Config:
        from_attributes = True


# ── In-place rent enrichment ─────────────────────────────────────────────────

class InPlaceRentUpdate(BaseModel):
    in_place_rent_psf: float
    in_place_rent_source: str = "manual"  # manual | costar | compstak | public_record
