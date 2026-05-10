from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, DateTime
from app.database import Base


class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    # String IDs — not FK so drafts survive property/company renames
    property_id = Column(String, nullable=False, index=True)
    company_id  = Column(String, nullable=True,  index=True)  # null for acquisition outreach
    outreach_type = Column(String, nullable=False)  # tenant_match | for_sale_vacancy | acquisition
    subject = Column(String, nullable=False)
    body    = Column(Text,   nullable=False)
    call_script_opening    = Column(Text, nullable=True)
    call_script_core       = Column(Text, nullable=True)
    call_script_pain_probe = Column(Text, nullable=True)
    call_script_close      = Column(Text, nullable=True)
    target_type      = Column(String, nullable=False)  # broker | owner | sales_broker
    recipient_name   = Column(String, nullable=True)
    recipient_email  = Column(String, nullable=True)
    internal_context = Column(Text,   nullable=True)
    score    = Column(Float,  nullable=True)
    priority = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    last_viewed_at = Column(DateTime, default=datetime.utcnow)
