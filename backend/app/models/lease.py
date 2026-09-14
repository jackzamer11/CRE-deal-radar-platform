# backend/app/models/lease.py
"""Every lease a company has signed, kept as a list — never a single field.

A second lease used to overwrite the first, and with it the prior term's base
rent, escalation schedule and option language. That history is the most
valuable thing Jack has going into a renewal conversation, so a new upload is a
new row and the previous one is demoted, never overwritten or deleted.

Exactly one lease per company is current (is_current). Which one is decided by
services/lease_records.py — the latest commencement date — and only the
current lease's confirmed values reach the company record.

file_name holds a BARE FILENAME, never a path: the folder is one setting
(settings.LEASES_FOLDER) joined at read time by services/lease_storage.py.
Nothing machine- or user-specific is written into a data column.

extraction_json is the FULL extraction as returned by the model — every field
with the clause text behind it, including values Jack unchecked and, for a row
he typed over, the original extracted value beside what he typed. It is the
audit trail: any value on this row traces back to its clause or to Jack.

Lease content is private. It never enters generated outreach copy.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, text,
)

from app.database import Base


class Lease(Base):
    __tablename__ = "leases"
    # Ids are never reused after a delete: a lease id sits in a file URL, and a
    # recycled id would open a different tenant's document.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)

    file_name = Column(String, nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    extraction_json = Column(Text, nullable=True)

    # The confirmed values. Null until Jack confirms, and null for any row he
    # left unchecked. Dates and SF are parsed; the clause-shaped fields are kept
    # as the document (or Jack) worded them.
    commencement_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    premises_address = Column(String, nullable=True)
    suite = Column(String, nullable=True)
    rentable_sf = Column(Integer, nullable=True)
    base_rent = Column(Text, nullable=True)
    escalation_terms = Column(Text, nullable=True)
    renewal_options = Column(Text, nullable=True)
    tenant_legal_entity = Column(String, nullable=True)

    is_current = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    confirmed_at = Column(DateTime, nullable=True)
