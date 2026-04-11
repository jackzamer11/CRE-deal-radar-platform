from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, Text, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)

    # When
    log_date = Column(Date, default=date.today, nullable=False)

    # Links (all optional — can log against any entity)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    # What happened
    action_type = Column(String, nullable=False)  # CALL / EMAIL / MEETING / SIGNAL_UPDATE / RESEARCH / NOTE
    action_taken = Column(Text, nullable=False)
    outcome = Column(Text, nullable=True)

    # Follow-up
    follow_up_date = Column(Date, nullable=True)
    follow_up_action = Column(Text, nullable=True)

    # Meta
    created_by = Column(String, default="system")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    opportunity = relationship("Opportunity", back_populates="activity_logs")
    property = relationship("Property", back_populates="activity_logs")
    company = relationship("Company", back_populates="activity_logs")
