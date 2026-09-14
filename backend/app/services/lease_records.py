"""A company's leases as a list: which one is current, and what reaches the record.

The rules, in one place so the upload, confirm and delete routes cannot drift:

* **Current = latest commencement date.** A lease not yet confirmed has no
  commencement date, so it ranks by the day it was uploaded. Ties break on
  upload time, then id. The same ranking picks who is promoted when the
  current lease is deleted.
* **Upload demotes explicitly.** A new upload is current the moment it lands,
  whatever the ranking says — Jack just linked it, and it is the one he is
  about to review. Confirming it then re-ranks: a historical lease confirmed
  with a 2015 commencement falls back to a prior term on its own.
* **Only the current lease writes to the company.** Confirming a prior term
  fills in that lease's own record and leaves the company alone.
* **Nothing is overwritten.** A new lease is a new row; a demoted lease keeps
  every field, its file and its extraction.

Every value carries where it came from: "lease_document" when it was read off
the page, "manual" when Jack typed it in the review panel. Both outrank CoStar.

Lease content is private. Nothing here feeds generated outreach copy.
"""
import json
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from dateutil import parser as _dateparser
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.lease import Lease
from app.schemas.company import months_until_lease_expiry

LEASE_DOCUMENT_SOURCE = "lease_document"
MANUAL_SOURCE = "manual"
# The two sources a confirmed lease can write. Both are protected from CoStar.
CONFIRMED_LEASE_SOURCES = frozenset({LEASE_DOCUMENT_SOURCE, MANUAL_SOURCE})

# Extraction field -> Lease column.
FIELD_TO_COLUMN = {
    "lease_commencement_date":  "commencement_date",
    "lease_expiration_date":    "expiration_date",
    "premises_address":         "premises_address",
    "suite_or_unit":            "suite",
    "rentable_square_footage":  "rentable_sf",
    "base_rent":                "base_rent",
    "escalation_terms":         "escalation_terms",
    "renewal_options":          "renewal_options",
    "tenant_legal_entity_name": "tenant_legal_entity",
}
_DATE_FIELDS = {"lease_commencement_date", "lease_expiration_date"}
_SF_FIELDS = {"rentable_square_footage"}


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_date(raw) -> Optional[date]:
    """A date as quoted or typed, or None.

    None rather than an exception: an unparseable date means the field simply
    does not get written, the safe outcome for the value that drives every
    downstream re-entry.
    """
    if not raw:
        return None
    try:
        return _dateparser.parse(str(raw), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def parse_sf(raw) -> Optional[int]:
    """Rentable SF out of free wording ("12,500 rentable sq ft")."""
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_field_value(field: str, raw):
    """The column-ready value for one extraction field, or None when unusable."""
    if raw is None:
        return None
    if field in _DATE_FIELDS:
        return parse_date(raw)
    if field in _SF_FIELDS:
        return parse_sf(raw)
    text = str(raw).strip()
    return text or None


# ── Extraction JSON ───────────────────────────────────────────────────────────

def load_extraction(raw: Optional[str]) -> Dict[str, dict]:
    """The stored extraction, or {} when there is none or it is unreadable.

    A corrupt blob must not 500 the Deal card — the linked file still matters
    more than the abstract.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def field_source(extraction: Dict[str, dict], field: str) -> str:
    """Where a confirmed field's value came from. Defaults to the document."""
    entry = extraction.get(field)
    if isinstance(entry, dict) and entry.get("source") == MANUAL_SOURCE:
        return MANUAL_SOURCE
    return LEASE_DOCUMENT_SOURCE


# ── Which lease is current ────────────────────────────────────────────────────

def rank_key(lease: Lease):
    uploaded = lease.uploaded_at or datetime.min
    effective = lease.commencement_date or (
        lease.uploaded_at.date() if lease.uploaded_at else date.min
    )
    return (effective, uploaded, lease.id or 0)


def company_leases(db: Session, company_pk: int) -> List[Lease]:
    """Every lease for a company in ONE query, current first then newest.

    The Deal card renders the whole list from this — a company with many
    leases never costs a query per lease.
    """
    rows = db.query(Lease).filter(Lease.company_id == company_pk).all()
    return sorted(rows, key=lambda l: (bool(l.is_current), rank_key(l)), reverse=True)


def current_lease(db: Session, company_pk: int) -> Optional[Lease]:
    return (
        db.query(Lease)
        .filter(Lease.company_id == company_pk, Lease.is_current.is_(True))
        .order_by(Lease.id.desc())
        .first()
    )


def choose_current(leases: Iterable[Lease]) -> Optional[Lease]:
    """Flag exactly one lease current — the top-ranked — and return it."""
    rows = list(leases)
    if not rows:
        return None
    winner = max(rows, key=rank_key)
    for row in rows:
        row.is_current = row is winner
    return winner


# ── Writing through to the company ────────────────────────────────────────────

def write_company_field(
    company: Company, field: str, value, source: str,
) -> Optional[str]:
    """Write one of the three write-back fields to the company, with its source.

    Returns the value as written (a string), or None when the field is not a
    write-back field or the value is unusable.
    """
    if field == "lease_expiration_date":
        parsed = value if isinstance(value, date) else parse_date(value)
        if parsed is None:
            return None
        company.lease_expiry_date = parsed
        # Kept in step with the date — the same derivation every other consumer
        # uses — so the queue reads the new expiry immediately.
        company.lease_expiry_months = months_until_lease_expiry(parsed)
        company.lease_expiry_source = source
        # A protected source only guards the expiry from CoStar once verified.
        company.lease_expiry_last_verified = date.today()
        return parsed.isoformat()
    if field == "premises_address":
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        company.current_address = text
        company.current_address_source = source
        return text
    if field == "rentable_square_footage":
        parsed_sf = value if isinstance(value, int) else parse_sf(value)
        if parsed_sf is None:
            return None
        company.current_sf_occupied = parsed_sf
        company.current_sf_occupied_source = source
        return str(parsed_sf)
    return None


def sync_company_from_lease(company: Company, lease: Lease) -> Dict[str, str]:
    """Copy a confirmed lease's write-back values onto the company.

    Used when a lease is PROMOTED to current (the previous current one was
    deleted). A field the promoted lease does not hold is left as the company
    has it — removing a document never un-knows a confirmed value. An
    unconfirmed lease writes nothing.
    """
    written: Dict[str, str] = {}
    if lease is None or lease.confirmed_at is None:
        return written
    extraction = load_extraction(lease.extraction_json)
    for field, column in (
        ("lease_expiration_date", "expiration_date"),
        ("premises_address", "premises_address"),
        ("rentable_square_footage", "rentable_sf"),
    ):
        value = getattr(lease, column)
        if value is None:
            continue
        out = write_company_field(company, field, value, field_source(extraction, field))
        if out is not None:
            written[field] = out
    return written


def lease_columns_from_legacy_extraction(extraction: Dict[str, dict]) -> Dict[str, object]:
    """Column values for a lease migrated from the old single-lease columns.

    Only fields the old confirm marked accepted, and that carried clause text,
    are filled — the same bar the old write-back used.
    """
    columns: Dict[str, object] = {}
    for field, column in FIELD_TO_COLUMN.items():
        entry = extraction.get(field)
        if not isinstance(entry, dict):
            continue
        if not (entry.get("accepted") and entry.get("found") and entry.get("source_text")):
            continue
        value = parse_field_value(field, entry.get("value"))
        if value is not None:
            columns[column] = value
    return columns


def was_confirmed(extraction: Dict[str, dict]) -> bool:
    """The old confirm recorded an `accepted` flag on every entry it touched."""
    return any(isinstance(e, dict) and "accepted" in e for e in extraction.values())
