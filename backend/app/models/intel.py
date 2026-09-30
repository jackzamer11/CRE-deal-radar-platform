"""Private Intelligence Layer — Phase D signal engine + weekly opportunities.

Namespaced as `intel_*` to sit alongside the existing (unrelated) Opportunity
model and `opportunities` table, which are a separate, active feature. Nothing
here touches that table.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Text, Date, DateTime, Boolean

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

    # ── Time ────────────────────────────────────────────────────────────────
    # A key alone used to make every decision permanent: rejecting Desta's 2027
    # lease card meant her 2032 renewal could never surface. These three
    # columns scope a decision to the thing that was decided.

    # The lease cycle a lease card is about — the expiry month, "2027-08".
    # A decision blocks only its own cycle (see intel_signal_service
    # _same_cycle); the next lease is a new card. Null on cards that are not
    # about one lease (stated requirements, incomplete records).
    cycle = Column(String, nullable=True)
    # The newest note behind the card. A requirement card Jack accepted or
    # rejected comes back only when something NEWER than this is said.
    evidence_date = Column(Date, nullable=True)
    # A deferred card comes back on this day. Null for every other status.
    resurface_at = Column(Date, nullable=True)


# Reason categories a human picks when rejecting/deferring an opportunity.
REASON_CATEGORIES = [
    "durable_policy",   # a standing rule ("we never do X") — candidate for criteria
    "conditional",      # depends on something that could change
    "relational",       # relationship/political reason
    "timing",           # right idea, wrong time
    "already_known",    # already on our radar
    "not_a_tenant",     # a broker, landlord, vendor — never a card again
    "other",
]

# The one rejection that is permanent, and that reaches past the card: the firm
# is marked counterparty (services/contact_type_service.py) so no card and no
# tenant outreach is ever made for it again.
NOT_A_TENANT = "not_a_tenant"

DISPOSITIONS = ["accepted", "rejected", "deferred"]


class IntelFeedback(Base):
    __tablename__ = "intel_feedback"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, nullable=False, index=True)
    disposition = Column(String, nullable=False)          # accepted / rejected / deferred
    reason_category = Column(String, nullable=True)       # required for reject/defer
    reason_text = Column(Text, nullable=True)
    # For a deferral: the day the card comes back.
    resurface_at = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IntelActivityExtraction(Base):
    """Record of which activity logs have been mined for structured facts.

    Exists purely so re-running the miner is idempotent. ActivityLog rows are
    READ-ONLY to this pipeline — nothing here ever writes to activity_logs.
    """

    __tablename__ = "intel_activity_extractions"

    id = Column(Integer, primary_key=True, index=True)
    activity_log_id = Column(Integer, nullable=False, index=True)
    status = Column(String, nullable=False, default="done")  # done / failed / empty
    fields_found = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    extracted_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IntelCriterion(Base):
    __tablename__ = "intel_criteria"

    id = Column(Integer, primary_key=True, index=True)
    statement = Column(Text, nullable=False)
    criterion_type = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
