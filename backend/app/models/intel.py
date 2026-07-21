"""Private Intelligence Layer — Phase D signal engine + weekly opportunities.

Namespaced as `intel_*` to sit alongside the existing (unrelated) Opportunity
model and `opportunities` table, which are a separate, active feature. Nothing
here touches that table.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Text, DateTime

from app.database import Base


class IntelSignal(Base):
    __tablename__ = "intel_signals"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    signal_type = Column(String, nullable=False)
    value = Column(String, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    evidence_observation_id = Column(Integer, nullable=True)


class IntelOpportunity(Base):
    __tablename__ = "intel_opportunities"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    rationale = Column(Text, nullable=True)
    signals_json = Column(Text, nullable=True)
    surfaced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, nullable=False, default="open")

    # Idempotency key: "{entity_type}:{entity_id}:{signal_type}". Re-running the
    # generator never creates a second open row for the same key.
    dedup_key = Column(String, nullable=True, index=True)
