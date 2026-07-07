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
    # Nullable, no default: NULL = never entered (abstain in scoring);
    # 0 = explicitly confirmed zero open positions (a deliberate score).
    open_positions = Column(Integer, nullable=True)
    hiring_velocity = Column(Float, nullable=True)        # open_positions / headcount * 100

    # Location & Space
    current_address = Column(String, nullable=True)
    current_submarket = Column(String, nullable=True)
    # The company's ACTUAL occupied square footage, sourced from CoStar ("SF
    # Occupied") or entered manually. This is the single SF field for a company:
    # it is NEVER calculated from headcount. Null means "SF unknown".
    current_sf_occupied = Column(Integer, nullable=True)
    sf_per_head = Column(Float, nullable=True)            # current_sf_occupied / headcount
    current_building_class = Column(String, nullable=True)  # Class A / B / C — drives class-fit factor

    # Lease
    lease_expiry_date = Column(Date, nullable=True)
    lease_expiry_months = Column(Integer, nullable=True)  # Months until expiry
    lease_expiry_source = Column(String, nullable=True)   # costar | manual | sec_filing | landlord_confirmed | public_record
    lease_expiry_last_verified = Column(Date, nullable=True)

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
    expiry_priority_override = Column(Boolean, default=False)

    # Contact
    primary_contact_name = Column(String, nullable=True)
    primary_contact_title = Column(String, nullable=True)
    primary_contact_phone = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)
    website = Column(String, nullable=True)

    # CoStar Tenant enrichment fields
    tenant_representative = Column(String, nullable=True)
    current_rent_psf = Column(Float, nullable=True)
    # Rent economics for the tenant-side rent-gap ladder. Plain fields on the
    # company record — no property/building linking. Null = unknown.
    # effective_rent_psf: the tenant's actual effective rent ($/SF/yr), sourced
    # from the CoStar Lease Activity import ("Effective Rent (Annual)", stored
    # as-is) or entered manually.
    # starting_rent_psf: the rent the lease STARTED at ($/SF/yr), sourced from
    # the Lease Activity import ("Starting Rent (Annual)", stored as-is) or
    # entered manually — feeds the escalation-creep rungs of the ladder.
    # building_asking_rent_psf: asking rent currently quoted at the tenant's
    # building ($/SF/yr), entered manually.
    effective_rent_psf = Column(Float, nullable=True)
    starting_rent_psf = Column(Float, nullable=True)
    building_asking_rent_psf = Column(Float, nullable=True)
    # Year the current lease was SIGNED, sourced from the Lease Activity import
    # ("Signed" / "Lease Signed Date" column) or entered manually. Anchors the
    # hedge rungs of the rent ladder to the tenant's real lease vintage —
    # null = unknown (hedge copy stays vague, never cites a year).
    lease_signed_year = Column(Integer, nullable=True)
    future_move_flag = Column(Boolean, nullable=True)
    future_move_type = Column(String, nullable=True)
    linked_property_id = Column(Integer, nullable=True)
    # Medical/non-medical classification — drives a soft match-score penalty when
    # a non-medical tenant is matched to a medical property (or vice versa).
    is_medical = Column(Boolean, nullable=False, default=False, server_default="0")

    # Lease trajectory — manually set by broker; drives SF projection in outreach agent
    # Values: AUTO | CONTRACTING | FLAT | GROWING
    lease_trajectory = Column(String, default="AUTO", nullable=False, server_default="AUTO")

    # Snooze — temporarily hide from Daily Briefing / outreach queue (mirrors property side)
    snoozed_until        = Column(Date,    nullable=True)   # if today < this, company is hidden from queue
    snooze_reason        = Column(String,  nullable=True)   # free text (e.g. "Just signed renewal — revisit next cycle")
    returned_from_snooze = Column(Boolean, nullable=True)   # set True when snooze expires on queue load

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Set to utcnow() on every user-initiated PATCH; used to guard against
    # automated pipeline overwrites on manually-verified records.
    last_modified_by_user = Column(DateTime, nullable=True)

    # Relationships
    opportunities  = relationship("Opportunity", back_populates="company")
    activity_logs  = relationship("ActivityLog", back_populates="company")
    outreach_logs  = relationship("OutreachLog", back_populates="company")
