from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(String, unique=True, index=True)  # e.g. OPP-001

    # Classification
    deal_type = Column(String, nullable=False)           # PRE_MARKET / ACTIVE_MISPRICED / TENANT_DRIVEN
    opportunity_category = Column(String, nullable=False) # ACQUISITION / LANDLORD_REP / TENANT_REP

    # Links
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    # Scoring
    score = Column(Float, nullable=False)
    confidence_level = Column(String, nullable=False)    # HIGH / MEDIUM / LOW
    priority = Column(String, nullable=False)            # IMMEDIATE / HIGH / WORKABLE / IGNORE

    # Signal breakdown stored at opportunity creation
    prediction_score = Column(Float, nullable=True)
    owner_behavior_score = Column(Float, nullable=True)
    mispricing_score = Column(Float, nullable=True)
    tenant_opportunity_score = Column(Float, nullable=True)

    # Deal thesis
    thesis = Column(Text, nullable=False)
    next_action = Column(Text, nullable=False)
    call_script = Column(Text, nullable=True)

    # Stage tracking
    stage = Column(String, default="IDENTIFIED")  # IDENTIFIED / CONTACTED / ACTIVE / UNDER_LOI / CLOSED / DEAD

    # Financials
    estimated_deal_value = Column(Float, nullable=True)   # Property value or lease TLV
    estimated_commission = Column(Float, nullable=True)

    # Flags
    is_active = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)          # Flagged for daily briefing

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    property = relationship("Property", back_populates="opportunities")
    company = relationship("Company", back_populates="opportunities")
    activity_logs = relationship("ActivityLog", back_populates="opportunity")
