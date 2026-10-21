"""Rename a company without forking it on the next email.

Every screen reads a company's name live through its id, so the rename itself
is one column. What needs care is everything that found the company BY NAME:

  * The email resolvers match a tenant an email names ("Development.org")
    against company names. Renamed to IDEA, that match is gone and the next
    email forks a new "Development.org". So the old name is kept in
    former_names, and the resolvers match it exactly.
  * The sender's domain finds a company through email_domain. A company that
    never claimed one (it was created from a name, not an address) was only
    findable by name — so a rename claims its contacts' work domain, when that
    is unambiguous. See _claim_contact_domain.
  * Generated text that baked the name in — an Intel card's title and
    rationale, a tenant opportunity's thesis, next action and call script — is
    rewritten. Prose a person or an email wrote (entries, facts, sent outreach)
    is a record of what was said and is left exactly as it was.

The caller commits.
"""
import json
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelOpportunity
from app.models.opportunity import Opportunity
from app.services.contact_service import email_domain_of, former_names_of, is_free_mail


class CompanyRenameError(ValueError):
    """The new name is not usable (blank)."""


class CompanyRenameConflict(Exception):
    """Another company already holds the new name."""


def _replace_whole(text: Optional[str], old: str, new: str) -> Optional[str]:
    """Replace `old` with `new` wherever it stands whole — never a fragment of
    a longer word, so renaming "Acme" leaves "Acmeville" alone."""
    if not text or not old:
        return text
    return re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text)


def _claim_contact_domain(db: Session, company: Company) -> None:
    """Give a domain-less company the work domain its own contacts write from.

    Only when it is unambiguous: every non-free-mail address among the
    company's contacts shares ONE domain. If another company holds that domain
    without a single contact on it — a guess the name resolver made, like
    "Research & Development Solutions" claiming development.org on the word
    "development" — the guess gives way to the company people actually email
    from. A holder that has a contact at the domain keeps it: that is evidence,
    and two real claims are Jack's call, not this function's.
    """
    if company.email_domain:
        return
    emails = [
        e for (e,) in db.query(Contact.email).filter(
            Contact.company_id == company.id, Contact.email.isnot(None),
        ).all()
    ]
    domains = {
        d for d in (email_domain_of(e) for e in emails)
        if d and not is_free_mail(d)
    }
    if len(domains) != 1:
        return
    domain = domains.pop()

    holders = db.query(Company).filter(
        Company.email_domain == domain, Company.id != company.id,
    ).all()
    for holder in holders:
        has_people = db.query(Contact.id).filter(
            Contact.company_id == holder.id,
            func.lower(Contact.email).like(f"%@{domain}"),
        ).first()
        if has_people is not None:
            return
    for holder in holders:
        holder.email_domain = None
    company.email_domain = domain


def rename_company(db: Session, company: Company, new_name: Optional[str]) -> Company:
    """Rename `company` to `new_name`. Caller commits.

    Raises CompanyRenameError on a blank name and CompanyRenameConflict when
    another company already has the name (case-insensitive). A rename never
    merges or overwrites: that is POST /companies/{id}/merge.
    """
    name = re.sub(r"\s+", " ", (new_name or "")).strip()
    if not name:
        raise CompanyRenameError("Company name cannot be blank")

    clash = db.query(Company).filter(
        func.lower(func.trim(Company.name)) == name.lower(),
        Company.id != company.id,
    ).first()
    if clash is not None:
        raise CompanyRenameConflict(
            f'{clash.company_id} is already named "{clash.name}". '
            "Rename or merge that company instead."
        )

    old = (company.name or "").strip()
    if old == name:
        return company

    # A pure case change ("Idea" -> "IDEA") is the same name: nothing to
    # remember. Renaming BACK to a former name takes it off the list.
    former = [n for n in former_names_of(company) if n.lower() != name.lower()]
    if old and old.lower() != name.lower() and old.lower() not in {n.lower() for n in former}:
        former.append(old)
    company.former_names = json.dumps(former) if former else None

    if old:
        for opp in db.query(IntelOpportunity).filter(
            IntelOpportunity.entity_type == "company",
            IntelOpportunity.entity_id == company.id,
        ).all():
            opp.title = _replace_whole(opp.title, old, name) or opp.title
            opp.rationale = _replace_whole(opp.rationale, old, name)
        for opp in db.query(Opportunity).filter(Opportunity.company_id == company.id).all():
            opp.thesis = _replace_whole(opp.thesis, old, name) or opp.thesis
            opp.next_action = _replace_whole(opp.next_action, old, name) or opp.next_action
            opp.call_script = _replace_whole(opp.call_script, old, name)

    _claim_contact_domain(db, company)

    company.name = name
    company.last_modified_by_user = datetime.utcnow()
    db.flush()
    return company
