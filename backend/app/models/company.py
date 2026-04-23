# backend/app/models/company.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, Date, DateTime
from sqlalchemy.orm import relationship

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, unique=True, index=True)  # e.g. CO-001

    name = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Size & Growth
    current_headcount = Column(Integer, nullable=True)
    headcount_12mo_ago = Column(Integer, nullable=True)
    headcount_growth_pct = Column(Float, nullable=True)   # Computed
    open_positions = Column(Integer, default=0)
    hiring_velocity = Column(Float, nullable=True)        # open_positions / headcount * 100

    # Location & Space
    current_address = Column(String, nullable=True)
    current_submarket = Column(String, nullable=True)
    current_sf = Column(Integer, nullable=True)
    sf_per_head = Column(Float, nullable=True)            # current_sf / headcount

    # Lease
    lease_expiry_date = Column(Date, nullable=True)
    lease_expiry_months = Column(Integer, nullable=True)  # Months until expiry
    lease_expiry_source = Column(String, nullable=True)   # costar | manual | sec_filing | landlord_confirmed | public_record
    lease_expiry_last_verified = Column(Date, nullable=True)
    estimated_sf_needed = Column(Integer, nullable=True)  # Projected space need

    # Behavioral Signals
    expansion_signal = Column(Boolean, default=False)
    contraction_signal = Column(Boolean, default=False)
    relocation_signal = Column(Boolean, default=False)

    # Signal sub-scores (0-100)
    sig_headcount_growth = Column(Float, default=0.0)
    sig_hiring_velocity = Column(Float, default=0.0)
    sig_lease_expiry = Column(Float, default=0.0)
    sig_space_utilization = Column(Float, default=0.0)
    sig_geo_clustering = Column(Float, default=0.0)

    # Composite score and metadata
    opportunity_score = Column(Float, default=0.0)
    priority = Column(String, default="IGNORE")
    signals_scored_count = Column(Integer, default=0)
    insufficient_data = Column(Boolean, default=False)

    # Contact
    primary_contact_name = Column(String, nullable=True)
    primary_contact_title = Column(String, nullable=True)
    primary_contact_phone = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    website = Column(String, nullable=True)

    # CoStar Tenant enrichment fields
    tenant_representative = Column(String, nullable=True)
    current_rent_psf = Column(Float, nullable=True)
    future_move_flag = Column(Boolean, nullable=True)
    future_move_type = Column(String, nullable=True)
    linked_property_id = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    opportunities = relationship("Opportunity", back_populates="company")
    activity_logs = relationship("ActivityLog", back_populates="company")
