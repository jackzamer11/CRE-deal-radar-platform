from datetime import datetime, date
from sqlalchemy import Boolean, Column, Integer, String, Float, Text, Date, DateTime, ForeignKey, text
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

    # The person this entry belongs to. Nullable: an entry can attach to a
    # company with no person behind it — a voicemail to a main line, a note on
    # an account. Indexed because the contact timeline filters on it.
    contact_id = Column(
        Integer, ForeignKey("contacts.id"), nullable=True, index=True,
    )

    # The company the conversation was ABOUT, at the time it happened.
    # IMMUTABLE after create. Contact.company_id is current employment and may
    # change; this stamp is what keeps a departed contact's history on the old
    # company's page while their personal thread stays complete.
    company_stamp_id = Column(
        Integer, ForeignKey("companies.id"), nullable=True, index=True,
    )

    # outbound = Jack reached out. inbound = they came to him.
    direction = Column(
        String, nullable=True, default="outbound", server_default=text("'outbound'"),
    )
    # email | call | meeting | text | linkedin | other
    channel = Column(
        String, nullable=True, default="other", server_default=text("'other'"),
    )

    # Provider message id (Outlook internetMessageId) for the email automation's
    # dedup. Indexed and unique — it lives here rather than in `notes` precisely
    # because `notes` is user-editable and editing one used to destroy the
    # marker, relogging the email on the next run.
    #
    # One email produces one row per participant (see `participation` below), so
    # only the FIRST of them carries the bare provider id; the rest carry it
    # suffixed with their contact id ("<id>#p12"). The unique index then still
    # holds, and a re-POST of the same email is caught by the bare id before any
    # row is written.
    source_message_id = Column(String, nullable=True, unique=True, index=True)

    # The address this entry came from, as the mailbox reported it. Recorded so
    # a correction can teach the resolver: when Jack moves an entry to a
    # different contact, this is the address that gets mapped to that person
    # (see models/email_ingest.ContactAddressOverride). Null on anything not
    # ingested from email.
    sender_email = Column(String, nullable=True, index=True)

    # True when this person was only COPIED on the email rather than written to.
    #
    # Someone cc'd on ten emails has no relationship with Jack. A participation
    # entry appears on their timeline as history and is visually distinct there,
    # but it does not count toward last touch, days of silence, entry count or
    # stage, and it never sets responded — their header must read "copied, never
    # directly contacted", not "active". When they later reply, THAT entry is
    # direct and everything updates from there, with the ten copies still
    # visible behind it.
    #
    # Every read that means "real correspondence" filters this out with
    # .isnot(True), which is null-safe for rows that pre-date the column.
    participation = Column(
        Boolean, nullable=False, default=False, server_default=text("0"),
    )

    # What happened
    action_type = Column(String, nullable=False)  # CALL / EMAIL / MEETING / SIGNAL_UPDATE / RESEARCH / NOTE
    action_taken = Column(Text, nullable=False)
    outcome = Column(Text, nullable=True)

    # Pipeline stage — current state only; can move in any direction at any time.
    # One of: Sent | Replied | Interested | In Play | Not Interested | Dormant.
    # Defaults to Sent for every new entry (and every migrated legacy entry).
    stage = Column(String, nullable=False, default="Sent", server_default=text("'Sent'"))

    # ── Stage-change events ──────────────────────────────────────────────────
    # A stage change is a divider, not a touch: it has no direction and no
    # channel, and it is not outreach. Rows carrying action_type=STAGE_CHANGE
    # record the transition here rather than only in `action_taken` prose, so
    # collapsing consecutive changes into a net move never parses display text.
    # Null on every real entry.
    stage_from = Column(String, nullable=True)
    stage_to   = Column(String, nullable=True)

    # Optional revisit / follow-up reminder. Required by the UI when the stage is
    # moved to Dormant or Not Interested; optional for Interested / In Play.
    next_touch_date = Column(Date, nullable=True)

    # Outreach-specific tracking (nullable — only set for outreach_sent events)
    outreach_type  = Column(String, nullable=True)   # tenant_match | for_sale_vacancy | acquisition
    target_type    = Column(String, nullable=True)   # broker | owner | sales_broker | tenant
    contact_method = Column(String, nullable=True)   # email | call
    subject        = Column(String, nullable=True)

    # Broker notes on this activity
    notes = Column(Text, nullable=True)

    # Follow-up
    follow_up_date = Column(Date, nullable=True)
    follow_up_action = Column(Text, nullable=True)

    # ── Discovery — captured during or right after a call ────────────────────
    # What the tenant said about their own situation. Nullable and inert: nothing
    # consumes these this build. They are deliberately NOT wired into scoring or
    # outreach generation — a tenant's claim is not verified data, and the
    # confirmation flow on the Company record is the only path from a claim to a
    # scoring field.
    disc_current_rent_psf   = Column(Float,   nullable=True)
    disc_current_sf         = Column(Integer, nullable=True)
    disc_lease_expiry       = Column(Date,    nullable=True)
    disc_decision_timeline  = Column(Text,    nullable=True)
    disc_buildout_needs     = Column(Text,    nullable=True)
    disc_decision_maker     = Column(Text,    nullable=True)

    # Meta
    created_by = Column(String, default="system")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    opportunity = relationship("Opportunity", back_populates="activity_logs")
    property = relationship("Property", back_populates="activity_logs")
    # Two paths to a company: `company` is the legacy free link (company_id),
    # `stamped_company` is the immutable record of what the conversation was
    # about. foreign_keys is required — the table now has two FKs to companies.
    company = relationship(
        "Company", back_populates="activity_logs", foreign_keys=[company_id],
    )
    stamped_company = relationship(
        "Company", back_populates="stamped_activity_logs",
        foreign_keys=[company_stamp_id],
    )
    contact = relationship("Contact", back_populates="activity_logs")
