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
    is_listed = Column(Boolean, default=False)
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

    # Relationships
    opportunities = relationship("Opportunity", back_populates="property", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="property")
