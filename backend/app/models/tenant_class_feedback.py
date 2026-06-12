from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from app.database import Base


class TenantClassFeedback(Base):
    __tablename__ = "tenant_class_feedback"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    current_address = Column(String, nullable=False)
    inferred_class = Column(String, nullable=True)       # what deriver would have guessed
    user_corrected_class = Column(String, nullable=True)  # what user actually entered
    created_at = Column(DateTime, default=datetime.utcnow)
