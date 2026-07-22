from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    field = Column(String, nullable=False)
    value = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    source_doc = Column(String, nullable=True)
    source_page = Column(Integer, nullable=True)
    source_snippet = Column(Text, nullable=True)
    human_verified = Column(Boolean, default=False, nullable=False)
    superseded_by_id = Column(Integer, ForeignKey("observations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    superseded_by = relationship("Observation", remote_side="Observation.id", back_populates="supersedes")
    supersedes = relationship("Observation", foreign_keys=[superseded_by_id], back_populates="superseded_by")
