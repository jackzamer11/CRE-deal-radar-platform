# backend/app/models/contact.py
from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, String, Boolean, Text, Date, DateTime, ForeignKey, text,
)
from sqlalchemy.orm import relationship

from app.database import Base

# Contact stages. Stage now belongs to the PERSON, not to an individual entry:
# an entry records what happened, the contact records where the relationship
# stands.
#
# Closed is the seventh and it means Jack PLACED them — deal done, lease signed,
# nothing to do until their lease clock comes back around. It is not an end to
# the relationship: a Closed contact keeps their whole thread, stays searchable,
# and resurfaces on their new expiry (see CLOSED_STAGE / is_past_client below).
# Nothing auto-sets it — only Jack.
CONTACT_STAGES = [
    "Sent", "Replied", "Interested", "In Play", "Not Interested", "Dormant",
    "Closed",
]

# The one place the Closed string is written down. Closed drops a contact out of
# the default By Contact list and out of the active outreach queue (the same
# mechanic as untriaged), so every filter reads it from here rather than
# spelling it inline.
CLOSED_STAGE = "Closed"

# owner is defined here so the owner side never needs a second migration; only
# tenant and counterparty surface in the UI this build.
CONTACT_TYPES = ["tenant", "counterparty", "owner"]


class Contact(Base):
    """A person Jack talks to.

    Created whenever a name (or an email address) is known — never gated on
    whether they replied. A contact who never responds keeps responded=False and
    stays in the system permanently.
    """
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)

    name  = Column(String, nullable=False)
    # Unique where present: SQLite treats NULLs as distinct, so any number of
    # contacts may have no email while a known address can only belong to one.
    email = Column(String, nullable=True, unique=True, index=True)
    phone = Column(String, nullable=True)
    title = Column(String, nullable=True)

    # Current employment. May change; entries keep their own company stamp so a
    # departed contact's history stays on the old company's page.
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)

    # tenant | counterparty | owner. counterparty covers landlord brokers,
    # landlords, property managers and lenders.
    contact_type = Column(
        String, nullable=False, default="tenant", server_default=text("'tenant'"),
    )

    stage = Column(
        String, nullable=False, default="Sent", server_default=text("'Sent'"),
    )
    # When the stage last changed — drives "days in stage" in the thread header.
    stage_changed_at = Column(Date, nullable=True)
    # Set when the stage moves TO Closed, cleared when it moves off. Distinct
    # from stage_changed_at, which any stage move rewrites: this is the date
    # the deal was placed.
    closed_at = Column(Date, nullable=True)

    next_touch_date = Column(Date, nullable=True, index=True)

    # Set True the first time the stage moves to Closed, and NEVER cleared:
    # moving off Closed (a renewal falls through, the deal reopens) does not undo
    # the fact that Jack placed this tenant once. It is what lets the contact
    # resurface on their next expiry marked as a past client — "you placed this
    # tenant in this building" is the strongest opening line available.
    is_past_client = Column(
        Boolean, nullable=False, default=False, server_default=text("0"),
    )

    responded    = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    triaged      = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    auto_created = Column(Boolean, nullable=False, default=False, server_default=text("0"))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company       = relationship("Company", back_populates="contacts")
    activity_logs = relationship("ActivityLog", back_populates="contact")
    facts         = relationship(
        "ContactFact", back_populates="contact", cascade="all, delete-orphan",
    )


class ContactFact(Base):
    """A durable thing learned about a person.

    Stored as discrete rows so facts are stable, auditable and clickable through
    to their source entry; rendered as prose in the UI — Jack never sees the raw
    list. A contradicted fact is superseded, never deleted, so the history of
    what was believed when survives.

    Automated extraction is out of scope this build. `create_fact()` in
    services/contact_fact_service.py is the seam an extractor plugs into.
    """
    __tablename__ = "contact_facts"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(
        Integer, ForeignKey("contacts.id"), nullable=False, index=True,
    )
    fact_text = Column(Text, nullable=False)
    # Nullable: a fact Jack types by hand has no originating entry.
    source_entry_id = Column(
        Integer, ForeignKey("activity_logs.id"), nullable=True, index=True,
    )
    learned_date = Column(Date, nullable=False, default=date.today)
    # Self-FK: the newer fact that replaced this one. Set together with
    # is_active=False so "superseded" is never ambiguous.
    superseded_by_id = Column(
        Integer, ForeignKey("contact_facts.id"), nullable=True,
    )
    is_active  = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact", back_populates="facts")
