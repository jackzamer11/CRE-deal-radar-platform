"""The rules the email ingestion path enforces, kept out of the route.

Four concerns live here:

  1. **Addresses Jack owns never become contacts.** Every ingested email has
     Jack on one side of it. He is not a contact in his own CRM.
  2. **A correction is remembered.** Moving an entry to a different contact maps
     the address that produced the wrong answer to the right person, and the
     resolver consults that map before anything else.
  3. **A stated number never writes.** It queues against the company, showing
     the stated value beside the value on file and the sentence it came from.
     The only exception is a company that did not exist a moment ago: there is
     nothing to conflict with, so the values are written directly.
  4. **An accepted value is marked conversation-sourced**, which is both how
     Jack can see where a number came from and how the CoStar import knows not
     to overwrite it.
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_ingest import (
    ACCEPTED, CONVERSATION_SOURCE, PENDING, PENDING_UPDATE_FIELDS,
    ContactAddressOverride, PendingCompanyUpdate,
)
from app.services.contact_service import (
    create_contact, normalize_email, resolve_companies_for_emails,
    resolve_contact_by_email, resolve_or_create_contact,
)

# Company column that records where each accepted value came from. Only these
# four fields can be stated in an email, so only these four need a marker.
FIELD_SOURCE_COLUMN = {
    "headcount":    "current_headcount_source",
    "growth_rate":  "headcount_growth_pct_source",
    "lease_expiry": "lease_expiry_source",
    "sf":           "current_sf_occupied_source",
}

# How each field renders in a confirmation prompt.
FIELD_LABEL = {
    "headcount":    "headcount",
    "growth_rate":  "headcount growth %",
    "lease_expiry": "lease expiry",
    "sf":           "square footage",
}


# ── Addresses Jack owns ───────────────────────────────────────────────────────

def _split_setting(raw: Optional[str]) -> set:
    return {
        part.strip().lower().lstrip("@")
        for part in str(raw or "").split(",")
        if part.strip()
    }


def own_email_domains() -> set:
    """Read at call time so the env var is live, never cached at import."""
    return _split_setting(getattr(settings, "OWN_EMAIL_DOMAINS", ""))


def own_email_addresses() -> set:
    return _split_setting(getattr(settings, "OWN_EMAIL_ADDRESSES", ""))


def is_own_address(email: Optional[str]) -> bool:
    """True when this address is Jack's own.

    Two tests, because they are not the same question. A domain Jack owns covers
    every address at it (jzamer@z-reg.com, anything@z-reg.com). A free-mail
    address has to match exactly — blocklisting gmail.com as a domain would
    swallow every real contact who uses it.
    """
    normalized = normalize_email(email)
    if not normalized:
        return False
    if normalized in own_email_addresses():
        return True
    if "@" not in normalized:
        return False
    domain = normalized.rsplit("@", 1)[1]
    if domain in own_email_domains():
        return True
    # Subdomain of an owned domain (mail.z-reg.com) is still Jack's.
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in own_email_domains():
            return True
    return False


# ── Corrections that teach the resolver ───────────────────────────────────────

def lookup_address_override(db: Session, email: Optional[str]) -> Optional[Contact]:
    """The contact Jack has taught us to file this address under, if any."""
    normalized = normalize_email(email)
    if not normalized:
        return None
    row = (
        db.query(ContactAddressOverride)
        .filter(ContactAddressOverride.email == normalized)
        .first()
    )
    if row is None:
        return None
    return db.query(Contact).filter(Contact.id == row.contact_id).first()


def record_address_override(
    db: Session,
    email: Optional[str],
    contact: Optional[Contact],
    source_entry_id: Optional[int] = None,
) -> Optional[ContactAddressOverride]:
    """Remember that `email` belongs to `contact`. Does not commit.

    Called when Jack reassigns an entry. An address Jack owns is never mapped —
    that would teach the resolver to file his own mail under a contact, which is
    the exact thing the guard exists to prevent.
    """
    normalized = normalize_email(email)
    if not normalized or contact is None or is_own_address(normalized):
        return None
    row = (
        db.query(ContactAddressOverride)
        .filter(ContactAddressOverride.email == normalized)
        .first()
    )
    if row is None:
        row = ContactAddressOverride(email=normalized, contact_id=contact.id)
        db.add(row)
    else:
        # A later correction wins — Jack is correcting the correction.
        row.contact_id = contact.id
        row.updated_at = datetime.utcnow()
    if source_entry_id is not None:
        row.source_entry_id = source_entry_id
    db.flush()
    return row


def resolve_contact_for_address(
    db: Session,
    email: Optional[str],
    display_name: Optional[str] = None,
    *,
    company: Optional[Company] = None,
    create: bool = True,
    create_fn=None,
) -> Tuple[Optional[Contact], bool]:
    """Resolve an address to a contact. Returns (contact, created).

    Order, and the reason for it:
      1. An address Jack owns resolves to nobody, ever. The entry is left
         unattached rather than filed under a contact called "Jack Zamer".
      2. A correction Jack has made (ContactAddressOverride) — before anything
         else, so the same correction is never needed twice.
      3. The address itself.
      4. Create, untriaged.

    `create_fn` is the creation step, injectable so the CALLER owns it. The
    route passes its own resolve_or_create_contact: creating a contact is the
    step that can fail mid-transaction, and it belongs where the transaction is
    managed and where it can be substituted, rather than buried behind a second
    module boundary.
    """
    normalized = normalize_email(email)
    if not normalized or is_own_address(normalized):
        return None, False

    taught = lookup_address_override(db, normalized)
    if taught is not None:
        if taught.company_id is None and company is not None:
            taught.company_id = company.id
        return taught, False

    if not create:
        return resolve_contact_by_email(db, normalized), False

    creator = create_fn or resolve_or_create_contact
    return creator(db, normalized, display_name, company=company)


def resolve_contacts_for_addresses(
    db: Session,
    addresses: List[Tuple[Optional[str], Optional[str]]],
    *,
    create_fn=None,
) -> Dict[str, Tuple[Contact, Optional[Company]]]:
    """resolve_contact_for_address() for several (email, name) pairs, in ONE pass.

    Returns {normalized email: (contact, company from their own domain)}.
    Addresses Jack owns, blanks and strings that are not addresses are left out
    of the result entirely — the caller treats a missing key as "no contact".

    Same rules, same order, a fixed number of queries however many addresses:
      1. an address Jack owns resolves to nobody
      2. a correction Jack has made (ContactAddressOverride)
      3. the address itself
      4. create, untriaged, with the company from their own domain

    `create_fn(db, email, name, company=...)` is the creation step, injectable
    so the caller that owns the transaction owns it.
    """
    creator = create_fn or create_contact
    wanted: Dict[str, Optional[str]] = {}
    for email, name in addresses:
        normalized = normalize_email(email)
        if not normalized or "@" not in normalized or is_own_address(normalized):
            continue
        if normalized not in wanted or not wanted[normalized]:
            wanted[normalized] = (name or "").strip() or None
    if not wanted:
        return {}

    companies = resolve_companies_for_emails(db, list(wanted))
    emails = list(wanted)

    taught_rows = (
        db.query(ContactAddressOverride)
        .filter(ContactAddressOverride.email.in_(emails))
        .all()
    )
    taught_ids = {row.contact_id for row in taught_rows}
    taught_contacts = {
        c.id: c for c in db.query(Contact).filter(Contact.id.in_(taught_ids)).all()
    } if taught_ids else {}
    taught = {
        row.email: taught_contacts[row.contact_id]
        for row in taught_rows if row.contact_id in taught_contacts
    }

    remaining = [e for e in emails if e not in taught]
    by_email = {
        c.email: c
        for c in db.query(Contact).filter(Contact.email.in_(remaining)).all()
    } if remaining else {}

    out: Dict[str, Tuple[Contact, Optional[Company]]] = {}
    for e in emails:
        company = companies.get(e, (None, False))[0]
        contact = taught.get(e) or by_email.get(e)
        if contact is None:
            contact = creator(db, e, wanted[e], company=company)
            by_email[e] = contact
        elif contact.company_id is None and company is not None:
            # Backfill a company link learned later, but never overwrite one.
            contact.company_id = company.id
        out[e] = (contact, company)
    return out


def name_key(name: Optional[str]) -> Optional[str]:
    """The comparison form of a person's name: casefolded, whitespace collapsed.

    "R. Kibby", "r. kibby" and "R.  Kibby" are one person. Nothing else is
    normalised away — punctuation and initials are part of how Jack wrote the
    name, and stripping them would start merging "R. Kibby" into "Rob Kibby".
    """
    collapsed = " ".join((name or "").split())
    return collapsed.casefold() or None


def resolve_contacts_for_names(
    db: Session,
    entries: List[Tuple[Optional[str], Optional[Company]]],
    *,
    create_fn=None,
) -> Dict[Tuple[str, Optional[int]], Contact]:
    """Resolve (name, company) pairs to contacts in ONE pass. Creates as needed.

    For people an email names without giving an address. Returns
    {(name_key, company_id): contact}; a blank name is left out entirely and the
    caller treats a missing key as "no contact".

    A name is a weak identifier — two people called Mike Johnson are two people
    — so the match is scoped to the company the deal is about. Within that
    company the match is case-insensitive, and it only ever considers contacts
    with NO email address. A name-keyed contact is never matched to, or merged
    into, a contact that holds an address: the address is that person's
    identity, and a bare name is not evidence that they are the same human.

    Fixed query count however many names: one read of the candidate contacts,
    then creation for whoever is left.
    """
    creator = create_fn or create_contact

    wanted: Dict[Tuple[str, Optional[int]], Tuple[str, Optional[Company]]] = {}
    for raw_name, company in entries:
        key = name_key(raw_name)
        if not key:
            continue
        wanted.setdefault(
            (key, company.id if company is not None else None),
            (" ".join((raw_name or "").split()), company),
        )
    if not wanted:
        return {}

    # One query. Company scoping is applied in Python so a null company_id — a
    # deal with no company resolved — buckets like any other value instead of
    # falling out of an IN clause.
    candidates = (
        db.query(Contact)
        .filter(Contact.email.is_(None))
        .filter(func.lower(Contact.name).in_(sorted({k for k, _ in wanted})))
        .all()
    )
    existing: Dict[Tuple[str, Optional[int]], Contact] = {}
    for contact in candidates:
        key = (name_key(contact.name), contact.company_id)
        if key[0] and key not in existing:
            existing[key] = contact

    out: Dict[Tuple[str, Optional[int]], Contact] = {}
    for key, (display_name, company) in wanted.items():
        contact = existing.get(key)
        if contact is None:
            contact = creator(db, None, display_name, company=company)
            existing[key] = contact
        out[key] = contact
    return out


# ── Stated company values ─────────────────────────────────────────────────────

class StatedValueError(ValueError):
    """Raised when a stated value cannot be read as the field's type."""


def coerce_value(field: str, raw):
    """Parse a stated value into the type its company column holds."""
    if field not in PENDING_UPDATE_FIELDS:
        raise StatedValueError(f"Unknown field '{field}'")
    if raw is None or str(raw).strip() == "":
        raise StatedValueError(f"No value given for {FIELD_LABEL.get(field, field)}")
    text = str(raw).strip()
    try:
        if field == "lease_expiry":
            return date.fromisoformat(text[:10])
        if field == "growth_rate":
            return float(text.rstrip("% ").strip())
        # headcount and sf: tolerate "1,200" and "1200.0".
        return int(float(text.replace(",", "")))
    except (TypeError, ValueError):
        raise StatedValueError(
            f"Could not read '{raw}' as a value for {FIELD_LABEL.get(field, field)}"
        )


def render_value(value) -> Optional[str]:
    """Render a company value for side-by-side display without losing precision."""
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def current_value_of(company: Optional[Company], field: str) -> Optional[str]:
    if company is None:
        return None
    return render_value(getattr(company, PENDING_UPDATE_FIELDS[field], None))


def write_company_value(company: Company, field: str, value) -> None:
    """Write a stated value onto the company and mark it conversation-sourced.

    The marker is the point: Jack must be able to see that a number came from
    something someone said in an email rather than from CoStar, a lease or his
    own typing — and the CoStar import reads the same marker to know not to
    overwrite it.

    Lease expiry writes the derived months alongside the date, because that is
    the column scoring reads; leaving them out of step would score the company
    on a stale number.
    """
    column = PENDING_UPDATE_FIELDS[field]
    setattr(company, column, value)
    setattr(company, FIELD_SOURCE_COLUMN[field], CONVERSATION_SOURCE)
    if field == "lease_expiry" and isinstance(value, date):
        today = date.today()
        company.lease_expiry_months = max(
            0, (value.year - today.year) * 12 + (value.month - today.month)
        )
        company.lease_expiry_last_verified = today
    company.last_modified_by_user = datetime.utcnow()


def queue_company_update(
    db: Session,
    company: Company,
    field: str,
    value,
    source_sentence: Optional[str] = None,
    source_entry_id: Optional[int] = None,
) -> Optional[PendingCompanyUpdate]:
    """Queue a stated value for Jack's confirmation. Does not commit.

    Returns None when the stated value already matches what is on file — there
    is nothing to confirm, and a queue that asks about agreements is a queue
    Jack stops reading.
    """
    coerced = coerce_value(field, value)
    existing = getattr(company, PENDING_UPDATE_FIELDS[field], None)
    if existing is not None and render_value(existing) == render_value(coerced):
        return None

    row = PendingCompanyUpdate(
        company_id=company.id,
        field=field,
        proposed_value=render_value(coerced),
        current_value=render_value(existing),
        source_sentence=(source_sentence or None),
        source_entry_id=source_entry_id,
        status=PENDING,
    )
    db.add(row)
    db.flush()
    return row


def pending_updates(
    db: Session, company_id: Optional[int] = None,
) -> List[PendingCompanyUpdate]:
    """Unresolved stated values, newest first. Empty list, never a 500."""
    q = db.query(PendingCompanyUpdate).filter(
        PendingCompanyUpdate.status == PENDING
    )
    if company_id is not None:
        q = q.filter(PendingCompanyUpdate.company_id == company_id)
    return q.order_by(PendingCompanyUpdate.id.desc()).all()


def accept_pending_update(db: Session, row: PendingCompanyUpdate, company: Company):
    """Write the stated value onto the company, marked conversation-sourced."""
    write_company_value(company, row.field, coerce_value(row.field, row.proposed_value))
    row.status = ACCEPTED
    row.resolved_at = datetime.utcnow()
    return row
