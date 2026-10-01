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
    # Who cleared this fact: "human" (Jack confirmed/corrected it in Review) or
    # "auto" (a low-risk field auto-approved by the intelligence layer). Null
    # while unverified. Kept distinct so machine approvals never masquerade as
    # human judgement in the feedback loop.
    verified_by = Column(String, nullable=True)

    # ── Who a mined fact is ABOUT ────────────────────────────────────────────
    # A note is not always about the company it is filed under. Jack emailing
    # Avison Young's broker "a tenant seeking 500-600 sqft" states a CLIENT's
    # requirement in an entry stamped to Avison Young. Filing it there made the
    # brokerage look like a tenant; hiding it lost the requirement. So:
    #
    #   null / "entry"  the company the source entry is filed under (the default,
    #                   and every fact mined before this existed)
    #   "named"         a tenant the note names — about_name — resolved to a
    #                   company when Intel runs
    #   "unassigned"    a client the note does not name: waits in Review's
    #                   holding list until Jack attaches it (assigned_company_id)
    #   "market"        what a broker or landlord said about space, rents or the
    #                   market. Kept on the broker; drives no card, never copy.
    #   "dismissed"     Jack said it is not a requirement
    about = Column(String, nullable=True)
    about_name = Column(String, nullable=True)
    # Jack's answer to "whose is this?" — wins over everything above. A company
    # (a tenant: it joins that company's Intel card), or a person. A person who
    # works at a tenant company is filed on that company's card; a counterparty,
    # or anyone with no company (an investor, a buyer), keeps it on their own
    # page — Intel stays tenant cards only.
    assigned_company_id = Column(Integer, nullable=True)
    assigned_contact_id = Column(Integer, nullable=True)

    # Set when this fact, mined from a newer note, CONTRADICTS one already on
    # file for the same tenant (a different SF, budget, term or lease date).
    # It then waits in Review beside the fact it contradicts, and the old one
    # stays in use until Jack picks: the loser is superseded by the winner,
    # never deleted. A restatement of the same value is not a contradiction.
    conflicts_with_id = Column(Integer, nullable=True)
    superseded_by_id = Column(Integer, ForeignKey("observations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    superseded_by = relationship("Observation", remote_side="Observation.id", back_populates="supersedes")
    supersedes = relationship("Observation", foreign_keys=[superseded_by_id], back_populates="superseded_by")
