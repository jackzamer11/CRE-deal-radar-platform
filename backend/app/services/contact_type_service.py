"""Who a contact is — tenant, counterparty, or not yet known.

Every rule about contact_type that is not a plain column write lives here:

  * what type an automatically created contact starts with,
  * what the UI pre-selects for an unconfirmed contact (a suggestion only),
  * recording the email automation's guess,
  * confirming a type, optionally for the whole firm.

The one invariant: **nothing here turns a guess into a type.** A contact's type
changes only when Jack confirms it — either on that person, or once for their
firm, which is also Jack's decision. A keyword, a domain or the automation's
read of an email signature only ever pre-selects a button.
"""

import re
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app import config
from app.models.company import Company
from app.models.contact import CONFIRMABLE_TYPES, UNCONFIRMED_TYPE, Contact
from app.services.rep_classification import MAJOR_BROKER_FIRMS


def _squash(value: Optional[str]) -> str:
    """Lowercase with everything but letters and digits removed, so "Stream
    Realty", "stream-realty" and "streamrealty.com" compare alike."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def firm_type(company: Optional[Company]) -> Optional[str]:
    """The type Jack set for a whole firm, or None. Only confirmable types
    count — an "owner" firm (dormant side) decides nothing here."""
    if company is None:
        return None
    value = company.company_type
    return value if value in CONFIRMABLE_TYPES else None


def initial_type_for(company: Optional[Company]) -> str:
    """The type an automatically created contact starts with.

    The firm's type when Jack has set one — marking Avison Young once is what
    makes the next person from Avison Young arrive already classified.
    Otherwise unconfirmed: an address says nothing about which side of the
    table someone sits on.
    """
    return firm_type(company) or UNCONFIRMED_TYPE


def _keyword_signal(*texts: Optional[str]) -> Optional[str]:
    """The first counterparty word found in any of the texts, or None."""
    for text in texts:
        squashed = _squash(text)
        if not squashed:
            continue
        if any(ex in squashed for ex in config.COUNTERPARTY_NAME_EXCLUSIONS):
            continue
        for firm in MAJOR_BROKER_FIRMS:
            if _squash(firm) in squashed:
                return firm
        for word in config.COUNTERPARTY_NAME_KEYWORDS:
            if word in squashed:
                return word
    return None


def _email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1]


def suggest_type_from(
    *,
    contact_type: Optional[str],
    suggested_type: Optional[str],
    email: Optional[str],
    company_name: Optional[str],
    company_type: Optional[str],
) -> Dict[str, Optional[str]]:
    """What to pre-select for an unconfirmed contact, and why.

    Takes plain values so the By Contact list can call it per row straight off
    its single aggregated query, with no per-contact lookup.

    Returns {"type": None, "reason": None} for any contact whose type is
    already settled. Otherwise, strongest signal first:

      1. the firm's type, when Jack has marked the firm,
      2. the email automation's read of the email (signature and all),
      3. a broker/landlord word in the company name or email domain,
      4. tenant — the platform is tenant-side, so it is the likelier answer
         when nothing points the other way.
    """
    if (contact_type or UNCONFIRMED_TYPE) != UNCONFIRMED_TYPE:
        return {"type": None, "reason": None}

    if company_type in CONFIRMABLE_TYPES:
        return {"type": company_type, "reason": f"{company_name} is marked {company_type}"}

    if suggested_type in CONFIRMABLE_TYPES:
        return {
            "type": suggested_type,
            "reason": "the email check read them as a " + suggested_type,
        }

    word = _keyword_signal(company_name, _email_domain(email))
    if word:
        return {"type": "counterparty", "reason": f'firm name or domain contains "{word}"'}

    return {"type": "tenant", "reason": "no broker or landlord signals"}


def suggest_type(contact: Contact) -> Dict[str, Optional[str]]:
    """suggest_type_from() for a loaded contact."""
    company = contact.company
    return suggest_type_from(
        contact_type=contact.contact_type,
        suggested_type=contact.suggested_type,
        email=contact.email,
        company_name=company.name if company is not None else None,
        company_type=company.company_type if company is not None else None,
    )


def record_type_guess(contact: Optional[Contact], guess: Optional[str]) -> bool:
    """Store the email automation's guess on an unconfirmed contact.

    A guess is advisory, so an unusable one is dropped rather than failing
    the email it arrived with. A settled type is never touched, and nor is a
    guess on a contact Jack has already classified.
    """
    if contact is None or not guess:
        return False
    normalized = guess.strip().lower()
    if normalized not in CONFIRMABLE_TYPES:
        return False
    if (contact.contact_type or UNCONFIRMED_TYPE) != UNCONFIRMED_TYPE:
        return False
    contact.suggested_type = normalized
    return True


def confirm_type(
    db: Session, contact: Contact, new_type: str, *, apply_to_firm: bool = False,
) -> Dict[str, object]:
    """Jack says who this person is. Optionally, who their whole firm is.

    With apply_to_firm, the firm's company_type is set, and every contact at
    that firm who is still UNCONFIRMED takes the same type. Anyone Jack has
    already classified keeps their type — a firm-wide click must not overwrite
    a decision made about one person. Future contacts created at the firm
    start with its type (see initial_type_for).

    Does not commit — the caller owns the transaction.
    """
    if new_type not in CONFIRMABLE_TYPES:
        raise ValueError(f"contact_type must be one of: {', '.join(CONFIRMABLE_TYPES)}")

    contact.contact_type = new_type
    contact.suggested_type = None

    firm_updated = 0
    company = contact.company
    if apply_to_firm:
        if company is None:
            raise ValueError("This contact has no company to apply the type to.")
        company.company_type = new_type
        others = (
            db.query(Contact)
            .filter(
                Contact.company_id == company.id,
                Contact.id != contact.id,
                Contact.contact_type == UNCONFIRMED_TYPE,
            )
            .all()
        )
        for other in others:
            other.contact_type = new_type
            other.suggested_type = None
        firm_updated = len(others)

    # What is left for the "apply to everyone at X?" offer. Only asked when
    # there is someone left to apply it to.
    firm_unconfirmed = 0
    if company is not None:
        db.flush()
        firm_unconfirmed = (
            db.query(Contact)
            .filter(
                Contact.company_id == company.id,
                Contact.contact_type == UNCONFIRMED_TYPE,
            )
            .count()
        )

    return {
        "firm_updated": firm_updated,
        "firm_unconfirmed": firm_unconfirmed,
        "firm_name": company.name if company is not None else None,
        "firm_type": firm_type(company),
    }
