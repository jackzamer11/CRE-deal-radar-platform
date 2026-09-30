"""Which tenant a mined fact is about.

A note is filed under one company, but it is not always ABOUT that company.
Jack emailing a landlord's broker "a tenant seeking 500-600 sqft" is stating a
client's requirement in an entry stamped to the brokerage. This module holds
the two decisions that follow from that, so the miner, the Intel generator and
Review's holding list cannot drift apart:

  * at mining time — `subject_for()`: what the fact is about, from the
    extractor's read of the note plus a guard the model cannot talk its way
    past (a requirement is never filed under a counterparty's own firm);
  * at Intel time — `NameResolver`: which company a named tenant is, matched
    only when the match is unambiguous. A near-miss waits for Jack.
"""

import re
from typing import Dict, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact

# Observation.about values. See models/observation.py.
ABOUT_ENTRY = "entry"
ABOUT_NAMED = "named"
ABOUT_UNASSIGNED = "unassigned"
ABOUT_MARKET = "market"
ABOUT_DISMISSED = "dismissed"

# What the extractor may say a requirement is about.
KIND_ENTRY_COMPANY = "entry_company"
KIND_NAMED_TENANT = "named_tenant"
KIND_UNNAMED_CLIENT = "unnamed_client"
REQUIREMENT_KINDS = (KIND_ENTRY_COMPANY, KIND_NAMED_TENANT, KIND_UNNAMED_CLIENT)


def entry_company(log: ActivityLog) -> Optional[Company]:
    """The company an entry is filed under: its stamp, then the legacy link,
    then its contact's current company."""
    if log.stamped_company is not None:
        return log.stamped_company
    if log.company is not None:
        return log.company
    if log.contact is not None:
        return log.contact.company
    return None


def is_counterparty_side(log: ActivityLog) -> bool:
    """Whether the company this entry is filed under sits across the table.

    A firm Jack marked decides it outright (SolaREIT marked tenant stays a
    tenant even though Laura there is a counterparty). Otherwise true when the
    person on the entry is a confirmed counterparty and the entry is filed
    under their own firm, or under nothing. A roundup entry Ann (a counterparty) wrote about Scott
    Management is stamped to Scott Management — a different company from Ann's —
    so its requirements still belong to the entry's company.
    """
    company = entry_company(log)
    if company is not None and company.company_type:
        # Jack marked the firm itself — that decides it, whoever the person is.
        return company.company_type == "counterparty"
    contact = log.contact
    if contact is None or contact.contact_type != "counterparty":
        return False
    return company is None or company.id == contact.company_id


def counterparty_side_log_ids(db: Session, log_ids) -> Set[int]:
    """is_counterparty_side() for many entries at once — a fixed number of
    queries, for the Intel run and Review's holding list.

    It exists for facts mined before `about` did: those carry no subject, and
    the ones from a counterparty's own firm are treated as waiting for a tenant
    straight away, with no API call to re-read them.
    """
    ids = sorted(set(log_ids))
    if not ids:
        return set()
    logs = []
    for i in range(0, len(ids), 500):
        logs += (
            db.query(ActivityLog.id, ActivityLog.company_stamp_id,
                     ActivityLog.company_id, ActivityLog.contact_id)
            .filter(ActivityLog.id.in_(ids[i:i + 500]))
            .all()
        )
    contact_ids = sorted({row[3] for row in logs if row[3]})
    contacts: Dict[int, Tuple[Optional[str], Optional[int]]] = {}
    for i in range(0, len(contact_ids), 500):
        for cid, ctype, company_id in (
            db.query(Contact.id, Contact.contact_type, Contact.company_id)
            .filter(Contact.id.in_(contact_ids[i:i + 500]))
            .all()
        ):
            contacts[cid] = (ctype, company_id)
    # Firms Jack marked either way — his firm-level decision wins over the person.
    marked_firms = dict(
        db.query(Company.id, Company.company_type)
        .filter(Company.company_type.isnot(None))
        .all()
    )
    out: Set[int] = set()
    for lid, stamp, legacy, contact_id in logs:
        ctype, contact_company = contacts.get(contact_id, (None, None)) if contact_id else (None, None)
        company_id = stamp or legacy or contact_company
        if company_id in marked_firms:
            if marked_firms[company_id] == "counterparty":
                out.add(lid)
        elif ctype == "counterparty" and (company_id is None or company_id == contact_company):
            out.add(lid)
    return out


def subject_for(log: ActivityLog, kind: Optional[str],
                tenant_name: Optional[str]) -> Tuple[str, Optional[str]]:
    """(about, about_name) for a requirement mined from `log`.

    The extractor's reading, with one guard it cannot override: a requirement
    is never filed under a counterparty's own firm. Avison Young is not the
    tenant, whatever the model thought — the fact waits for Jack instead.
    """
    name = (tenant_name or "").strip() or None
    if kind == KIND_NAMED_TENANT and name:
        return ABOUT_NAMED, name
    if kind == KIND_UNNAMED_CLIENT:
        return ABOUT_UNASSIGNED, None
    if is_counterparty_side(log):
        return ABOUT_UNASSIGNED, None
    return ABOUT_ENTRY, None


_SUFFIXES = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|pllc|pc|p\.c|llp|ltd|co|corp|corporation|company|the)\b\.?",
    re.IGNORECASE,
)


def name_key(name: Optional[str]) -> str:
    """Comparison form: casefolded, legal suffixes and punctuation gone.
    "Vienna Clinic, PLLC" and "vienna clinic" compare alike."""
    text = _SUFFIXES.sub(" ", (name or "").casefold())
    return re.sub(r"[^a-z0-9]+", "", text)


class NameResolver:
    """A named tenant -> company id, only when exactly one company fits.

    Tries company names, then contact names (a note that says "for Jim" means
    Jim's employer). Two matches is no match: guessing wrong would file one
    tenant's requirement on another's card, which is worse than asking.
    Loaded once per run — two queries, whatever the fact count.
    """

    def __init__(self, db: Session):
        self._companies: Dict[str, Optional[int]] = {}
        for cid, name in db.query(Company.id, Company.name).all():
            self._add(self._companies, name_key(name), cid)
        self._people: Dict[str, Optional[int]] = {}
        for name, company_id in (
            db.query(Contact.name, Contact.company_id)
            .filter(Contact.company_id.isnot(None))
            .all()
        ):
            self._add(self._people, name_key(name), company_id)

    @staticmethod
    def _add(index: Dict[str, Optional[int]], key: str, value: int) -> None:
        if not key:
            return
        if key in index and index[key] != value:
            index[key] = None   # ambiguous from here on
        else:
            index[key] = value

    def resolve(self, name: Optional[str]) -> Optional[int]:
        key = name_key(name)
        if not key:
            return None
        if key in self._companies:
            return self._companies[key]
        return self._people.get(key)
