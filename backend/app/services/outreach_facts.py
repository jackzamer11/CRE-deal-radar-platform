"""What a tenant has told Jack, gathered for that tenant's outreach.

Outreach used to know only the company record. It now also knows what the
tenant said — their SF range, timing, must-haves — and, for a tenant Jack has a
confirmed lease for, that lease's size and expiry. Every rule below exists so
none of it can reach the wrong person or break an outreach rule:

  * **Only this tenant's facts.** They are gathered the way Intel files them
    (services/intel_signal_service._load_context): a broker's market talk, a
    requirement still waiting for an owner, a dismissed fact, an unsettled
    contradiction and another company's notes never come back from here.
  * **No budget figure in the email.** Rent figures in an email must match the
    call sheet exactly, so the stated budget goes to the call sheet only and is
    never handed to the email model.
  * **Old statements are questions.** Past their freshness window a requirement
    is offered as something to re-confirm, never asserted.
  * **A lease only for the tenant who signed it.** The current, confirmed lease
    of THIS company — size and expiry month only, never the address or suite —
    and not when the person being written to has left the company.
"""

from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.lease import Lease

# Requirement fields the email may use, in the order they read best.
EMAIL_FIELDS = {
    "req_sf_min": "min SF",
    "req_sf_max": "max SF",
    "req_submarkets": "submarkets",
    "req_space_type": "space type",
    "req_must_haves": "must-haves",
    "req_access_needs": "access",
    "req_timing": "timing",
    "req_lease_term_years": "term",
    "req_buildout_willingness": "buildout",
    "req_ti_expectation": "TI",
}
# Stated, but for the call sheet only — a dollar figure in an email must match
# the call sheet's DATA block, and a tenant's own budget has no place there.
CALL_SHEET_ONLY = {"req_budget_max_psf": "budget"}


def _name_key(name: Optional[str]) -> str:
    return " ".join((name or "").casefold().split())


def _recipient_left(db: Session, company: Company) -> bool:
    """Whether the person this company's outreach is addressed to has left it."""
    recipient = _name_key(company.primary_contact_name)
    if not recipient:
        return False
    return any(
        _name_key(name) == recipient
        for (name,) in db.query(Contact.name).filter(Contact.former_company_id == company.id).all()
    )


def own_lease_facts(db: Session, company: Company) -> Optional[Dict[str, object]]:
    """The size and expiry of the lease THIS company signed, or None.

    Only the current, confirmed lease; only when the recipient still works
    there. The address and suite are never read here, so they cannot leak.
    """
    lease = (
        db.query(Lease)
        .filter(
            Lease.company_id == company.id,
            Lease.is_current.is_(True),
            Lease.confirmed_at.isnot(None),
        )
        .first()
    )
    if lease is None or lease.company_id != company.id:
        return None
    if _recipient_left(db, company):
        return None
    facts: Dict[str, object] = {}
    if lease.rentable_sf:
        facts["rentable_sf"] = int(lease.rentable_sf)
    if lease.expiration_date:
        facts["expires"] = f"{lease.expiration_date:%B %Y}"
    return facts or None


def other_lease_numbers(db: Session, company: Company) -> List[str]:
    """SF figures from every OTHER company's leases, as they could be written.

    The generated email is checked against these after the model writes it: a
    match means another tenant's lease leaked, and the draft is refused.
    """
    out: List[str] = []
    for (sf,) in (
        db.query(Lease.rentable_sf)
        .filter(Lease.company_id != company.id, Lease.rentable_sf.isnot(None))
        .all()
    ):
        if sf and sf >= 100:   # tiny numbers would match ordinary prose
            out += [f"{int(sf):,}", str(int(sf))]
    return sorted(set(out))


def stated_for(db: Session, company: Company, today: Optional[date] = None) -> Dict[str, List]:
    """What this tenant stated, split for the email and the call sheet.

    Returns {"current": [(label, value)], "reconfirm": [(label, value)],
    "call_sheet": [(label, value)]}. "current" and "reconfirm" hold only
    EMAIL_FIELDS; the budget is in "call_sheet" alone.
    """
    from app.services.intel_signal_service import (
        _active_observations, _group, _is_fresh, _load_context, _newest_value,
    )

    today = today or date.today()
    ctx, facts = _load_context(db, _active_observations(db))
    rows = _group(ctx, facts).get(("company", company.id), [])
    fresh = [o for o in rows if _is_fresh(ctx, o, today)]
    stale = [o for o in rows if not _is_fresh(ctx, o, today)]

    def pick(source, fields):
        out = []
        for field, label in fields.items():
            value = _newest_value(ctx, source, field)
            if value:
                out.append((label, value.strip()))
        return out

    current = pick(fresh, EMAIL_FIELDS)
    have = {label for label, _ in current}
    reconfirm = [(l, v) for l, v in pick(stale, EMAIL_FIELDS) if l not in have]
    call_sheet = current + pick(fresh, CALL_SHEET_ONLY)
    return {"current": current, "reconfirm": reconfirm, "call_sheet": call_sheet}


def outreach_context(db: Session, company: Company) -> Dict[str, object]:
    """Everything above, as the keys generate_outreach reads."""
    stated = stated_for(db, company)
    return {
        "stated_requirements": stated["current"],
        "reconfirm_requirements": stated["reconfirm"],
        "call_sheet_stated": stated["call_sheet"],
        "own_lease": own_lease_facts(db, company),
        "other_lease_numbers": other_lease_numbers(db, company),
    }
