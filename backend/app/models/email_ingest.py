# backend/app/models/email_ingest.py
"""The three records the email ingestion path writes besides the entry itself.

Each exists because the alternative was silently wrong:

* **PendingCompanyUpdate** — a number stated in an email ("we're up to 40
  people now") never writes to the company record. It queues, showing the
  stated value beside the value on file and the sentence it came from, and
  moves only when Jack accepts it. The existing contact_reported_* columns on
  Company hold one claim per field; this table holds a queue, so a second email
  cannot overwrite the first claim before Jack has seen it.

* **ActivityAttachment** — file_name plus the YEAR it was filed under, never a
  path. The folder is one setting (settings.DOCUMENTS_FOLDER) joined at read
  time by services/attachment_storage.py, exactly as leases work.

* **ContactAddressOverride** — what Jack taught the resolver by correcting an
  entry. When he moves an entry to a different contact, the address that
  produced the wrong answer is mapped to the right person, and the next email
  from it resolves there before any domain matching runs.
"""
from datetime import date, datetime

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, String, Text, text,
)

from app.database import Base

# The four company fields an email may state a value for. Kept here rather than
# in the route so the model, the migration and the API all read the same list.
#   key          → the Company column an accept writes
PENDING_UPDATE_FIELDS = {
    "headcount":    "current_headcount",
    "growth_rate":  "headcount_growth_pct",
    "lease_expiry": "lease_expiry_date",
    "sf":           "current_sf_occupied",
}

# Resolution states. NULL/"pending" = still Jack's call.
PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"

# The source marker written onto a company field accepted from a conversation.
# Distinct from "costar", "lease_document" and "manual" precisely so Jack can
# see that a number came from something someone said in an email — and so the
# CoStar import knows not to overwrite it (see routes/companies.py).
CONVERSATION_SOURCE = "conversation"


class PendingCompanyUpdate(Base):
    """A value an email stated about a company, awaiting Jack's confirmation.

    Never written to the company record on create. Accepting copies the value
    across and marks it conversation-sourced; rejecting leaves the company
    field alone and sets Company.has_data_conflict — a tenant who believes
    something different from the record is itself a lead.
    """
    __tablename__ = "pending_company_updates"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(
        Integer, ForeignKey("companies.id"), nullable=False, index=True,
    )
    # One of PENDING_UPDATE_FIELDS.
    field = Column(String, nullable=False)
    # Both sides are stored as TEXT and coerced on accept: the queue has to
    # render "40" beside "35" for four different column types, and keeping the
    # raw string is also what lets the source sentence and the value agree.
    proposed_value = Column(Text, nullable=False)
    current_value = Column(Text, nullable=True)
    # The sentence the value came from. This is what makes the confirmation
    # answerable — "they said 40" is not reviewable, the sentence is.
    source_sentence = Column(Text, nullable=True)
    source_entry_id = Column(
        Integer, ForeignKey("activity_logs.id", use_alter=True),
        nullable=True, index=True,
    )
    status = Column(
        String, nullable=False, default=PENDING, server_default=text("'pending'"),
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class ActivityAttachment(Base):
    """A file that arrived on an ingested email.

    file_name is a BARE FILENAME and stored_year is the folder it was filed
    under; the two join to settings.DOCUMENTS_FOLDER at read time. Nothing
    machine- or user-specific is written into a data column.

    Attachments deliberately do NOT route into the lease flow. Leases get
    traded back and forth in draft and the ingestion task cannot tell draft
    eleven from the executed copy — only Jack uploads an executed lease, on
    purpose.
    """
    __tablename__ = "activity_attachments"
    # Ids are never reused: an attachment id sits in a file URL, and a recycled
    # id would open a different company's document.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    activity_log_id = Column(
        Integer, ForeignKey("activity_logs.id"), nullable=False, index=True,
    )
    file_name = Column(String, nullable=False)
    # The year subfolder. Integer, not a path fragment — resolve_attachment_path
    # builds "<folder>/<year>/<file_name>" and refuses anything else.
    stored_year = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    saved_date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactAddressOverride(Base):
    """An address Jack has taught the resolver to file under a given contact.

    Written when he moves an entry to a different contact. The resolver checks
    this table FIRST, before matching on the address itself or on the domain,
    so the same correction is never needed twice.
    """
    __tablename__ = "contact_address_overrides"

    id = Column(Integer, primary_key=True, index=True)
    # Normalised, lower-cased. Unique: one address maps to one person, and a
    # later correction updates the row rather than adding a rival mapping.
    email = Column(String, nullable=False, unique=True, index=True)
    contact_id = Column(
        Integer, ForeignKey("contacts.id"), nullable=False, index=True,
    )
    # Which correction taught this, for the audit trail.
    source_entry_id = Column(
        Integer, ForeignKey("activity_logs.id"), nullable=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
