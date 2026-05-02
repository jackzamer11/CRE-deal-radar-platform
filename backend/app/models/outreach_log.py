from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class OutreachLog(Base):
    __tablename__ = "outreach_log"

    id                    = Column(Integer, primary_key=True, index=True)
    company_id            = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    generated_at          = Column(DateTime, default=datetime.utcnow)

    # Generated content
    email_subject         = Column(Text)
    email_body            = Column(Text)
    call_script_opening   = Column(Text)
    call_script_core      = Column(Text)
    call_script_pain_probe = Column(Text)
    call_script_close     = Column(Text)
    projected_sf          = Column(Integer, nullable=True)

    # Snapshot of score at generation time
    score_at_generation    = Column(Float)
    priority_at_generation = Column(String)

    # Tracking
    marked_contacted = Column(Boolean, default=False)
    email_sent       = Column(Boolean, default=False)
    call_made        = Column(Boolean, default=False)
    outcome_notes    = Column(Text, nullable=True)
    contacted_at     = Column(DateTime, nullable=True)

    company = relationship("Company", back_populates="outreach_logs")
