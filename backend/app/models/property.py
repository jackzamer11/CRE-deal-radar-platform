from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, Date, DateTime
from sqlalchemy.orm import relationship

from app.database import Base


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(String, unique=True, index=True)  # e.g. NVA-001

    # Location
    address = Column(String, nullable=False)
    submarket = Column(String, nullable=False)
    asset_class = Column(String, default="Class B")  # Class A / B / C

    # Physical
    total_sf = Column(Integer, nullable=False)
    year_built = Column(Integer, nullable=False)
    last_renovation_year = Column(Integer, nullable=True)
    num_floors = Column(Integer, nullable=True)

    # Ownership
    owner_name = Column(String, nullable=False)
    owner_type = Column(String, default="LLC")  # Individual / LLC / REIT / Private Equity
    owner_phone = Column(String, nullable=True)
    owner_email = Column(String, nullable=True)
    acquisition_date = Column(Date, nullable=True)
    acquisition_price = Column(Float, nullable=True)
    years_owned = Column(Float, nullable=True)  # Computed on seed / refresh

    # Financial
    asking_price = Column(Float, nullable=True)
    asking_price_psf = Column(Float, nullable=True)
    estimated_value = Column(Float, nullable=True)
    in_place_rent_psf = Column(Float, nullable=False)   # $/SF/yr NNN
    market_rent_psf = Column(Float, nullable=False)     # $/SF/yr NNN submarket avg
    noi = Column(Float, nullable=True)
    cap_rate = Column(Float, nullable=True)             # In-place cap rate
    market_cap_rate = Column(Float, nullable=False)     # Submarket avg cap rate
    in_place_rent_source = Column(String, nullable=True)        # manual | costar | compstak | public_record
    in_place_rent_last_verified = Column(Date, nullable=True)

    # CoStar enrichment
    star_rating = Column(Integer, nullable=True)                 # CoStar 1-5 quality rating
    sf_avail = Column(Integer, nullable=True)                    # Total available SF
    landlord_representative = Column(String, nullable=True)
    landlord_rep_contact = Column(String, nullable=True)
    sales_company = Column(String, nullable=True)
    sales_contact = Column(String, nullable=True)
    tenancy = Column(String, nullable=True)                      # 'single' | 'multi'
    stories = Column(Integer, nullable=True)
    parking_ratio = Column(Float, nullable=True)                 # spaces per 1,000 SF
    # Medical/non-medical classification — drives a soft match-score penalty when
    # a medical property is matched to a non-medical tenant (or vice versa).
    is_medical = Column(Boolean, nullable=False, default=False, server_default="0")

    # Occupancy & Leasing
    occupancy_pct = Column(Float, nullable=True)
    vacancy_pct = Column(Float, nullable=True)
    vacancy_12mo_ago = Column(Float, nullable=True)
    leased_sf = Column(Float, nullable=True)
    vacant_sf = Column(Float, nullable=True)
    sf_expiring_12mo = Column(Float, default=0.0)
    sf_expiring_24mo = Column(Float, default=0.0)
    lease_rollover_pct = Column(Float, default=0.0)     # sf_expiring_12mo / total_sf
    last_lease_signed_date = Column(Date, nullable=True)
    years_since_last_lease = Column(Float, default=0.0)

    # Listing status
    listed_for_sale = Column(Boolean, default=False)
    listing_date = Column(Date, nullable=True)
    days_on_market = Column(Integer, nullable=True)
    submarket_avg_dom = Column(Integer, nullable=True)

    # Debt proxy
    estimated_loan_maturity_year = Column(Integer, nullable=True)
    estimated_ltv = Column(Float, nullable=True)

    # --- Computed Signal Scores (0-100) ---
    prediction_score = Column(Float, default=0.0)
    owner_behavior_score = Column(Float, default=0.0)
    mispricing_score = Column(Float, default=0.0)
    signal_score = Column(Float, default=0.0)           # Weighted composite

    # Property-side outreach scores (each 0-100; max becomes the dominant_score_type)
    tenant_match_score = Column(Float, default=0.0)
    listing_rep_score  = Column(Float, default=0.0)
    acquisition_score  = Column(Float, default=0.0)
    dominant_score_type = Column(String, nullable=True)  # 'tenant_match' | 'listing_rep' | 'acquisition'

    # Signal sub-scores stored for transparency
    sig_lease_rollover = Column(Float, default=0.0)
    sig_vacancy_trend = Column(Float, default=0.0)
    sig_ownership_duration = Column(Float, default=0.0)
    sig_leasing_drought = Column(Float, default=0.0)
    sig_capex_gap = Column(Float, default=0.0)
    sig_hold_period = Column(Float, default=0.0)
    sig_occupancy_decline = Column(Float, default=0.0)
    sig_rent_stagnation = Column(Float, default=0.0)
    sig_reinvestment_inactivity = Column(Float, default=0.0)
    sig_debt_pressure = Column(Float, default=0.0)
    sig_rent_gap = Column(Float, default=0.0)
    sig_price_psf = Column(Float, default=0.0)
    sig_dom_premium = Column(Float, default=0.0)
    sig_cap_rate_spread = Column(Float, default=0.0)

    # Scoring metadata
    signals_scored_count = Column(Integer, default=0)
    insufficient_data = Column(Boolean, default=False)

    # Output
    priority = Column(String, default="IGNORE")   # IMMEDIATE / HIGH / WORKABLE / IGNORE
    deal_type = Column(String, nullable=True)      # PRE_MARKET / ACTIVE_MISPRICED
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_signal_run = Column(DateTime, nullable=True)
    # Set on every user-initiated PATCH; guards against pipeline overwrites.
    last_modified_by_user = Column(DateTime, nullable=True)

    # Snooze — temporarily hide from Daily Briefing / Section A
    snoozed_until        = Column(Date,    nullable=True)   # if today < this, property is hidden from queue
    snooze_reason        = Column(String,  nullable=True)   # free text (e.g. "Under contract — PSA signed")
    returned_from_snooze = Column(Boolean, nullable=True)   # set True when snooze expires on briefing load

    # Owner confirmed open to leasing while listed (hard trigger for tenant-match outreach)
    owner_confirmed_leasing      = Column(Boolean, default=False)   # switches outreach from property-side → tenant-match
    owner_confirmed_leasing_date = Column(Date,    nullable=True)   # auto-set server-side on first confirmation

    # Relationships
    opportunities = relationship("Opportunity", back_populates="property", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="property")
    outreach_logs = relationship("OutreachLog", back_populates="property")
