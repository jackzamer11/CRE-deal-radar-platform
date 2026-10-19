"""Merge a duplicate company into the one that survives.

Duplicates come from auto-creation: one email can name a tenant ("Global
Printing dba More Vang") while its tenant contact's address derives another
name from the domain ("Morevang"). The resolvers now match those
(contact_service._names_match), but a pair created before that — or any pair a
rule cannot see — still needs folding together without losing what either
record learned.

What moves from `source` to `target`:
  * every row that points at a company by id — activity entries (both the
    company and the stamp), contacts (current and former company), leases,
    pending company updates, opportunities, outreach drafts and log, tenant
    class feedback;
  * every company-scoped row keyed by entity — observations (the facts behind
    "What they need" and the market card, plus any assigned to the company),
    documents, intel signals and intel opportunities, whose dedup key carries
    the company id and is rewritten to the target's;
  * any company field the target has no value for. Target wins every field
    it already holds: a value read off a signed lease is never replaced by one
    a duplicate heard in an email.

Then source is deleted. One transaction: everything is a bulk UPDATE inside
the caller's session, and the caller commits, so a failure partway writes
nothing.
"""
from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.document import Document
from app.models.email_ingest import PendingCompanyUpdate
from app.models.intel import IntelOpportunity, IntelSignal
from app.models.lease import Lease
from app.models.observation import Observation
from app.models.opportunity import Opportunity
from app.models.outreach_draft import OutreachDraft
from app.models.outreach_log import OutreachLog
from app.models.tenant_class_feedback import TenantClassFeedback
from app.services.lease_records import choose_current


class CompanyMergeConflict(Exception):
    """Raised when the two companies given are not eligible to merge."""


# Never copied from source: identity and bookkeeping.
_NOT_COPIED = {"id", "company_id", "name", "created_at", "updated_at"}

# (model, column) pairs holding a companies.id.
_ID_REFERENCES = [
    (ActivityLog, "company_id"),
    (ActivityLog, "company_stamp_id"),
    (Contact, "company_id"),
    (Contact, "former_company_id"),
    (Lease, "company_id"),
    (PendingCompanyUpdate, "company_id"),
    (Opportunity, "company_id"),
    (OutreachDraft, "company_id"),
    (OutreachLog, "company_id"),
    (TenantClassFeedback, "company_id"),
    (Observation, "assigned_company_id"),
]

# Models keyed by (entity_type, entity_id).
_ENTITY_REFERENCES = [Observation, Document, IntelSignal, IntelOpportunity]


def merge_companies(db: Session, target: Company, source: Company) -> Dict[str, object]:
    """Fold `source` into `target` and delete `source`. Caller commits.

    Returns what moved, per table, and which company fields were filled from
    source — so the caller can say exactly what the merge did.
    """
    if target.id == source.id:
        raise CompanyMergeConflict("Cannot merge a company into itself")

    moved: Dict[str, int] = {}

    def _count(table: str, n: int) -> None:
        if n:
            moved[table] = moved.get(table, 0) + n

    for model, column in _ID_REFERENCES:
        n = db.query(model).filter(getattr(model, column) == source.id).update(
            {column: target.id}, synchronize_session=False,
        )
        _count(model.__tablename__, n)

    # Opportunities first: their dedup key embeds the company id
    # ("company:607:stated_requirement"), and the next intel cycle finds an
    # existing opportunity by that key.
    old_prefix, new_prefix = f"company:{source.id}:", f"company:{target.id}:"
    for opp in db.query(IntelOpportunity).filter(
        IntelOpportunity.entity_type == "company",
        IntelOpportunity.entity_id == source.id,
    ).all():
        if opp.dedup_key and opp.dedup_key.startswith(old_prefix):
            opp.dedup_key = new_prefix + opp.dedup_key[len(old_prefix):]
        if source.name and target.name:
            opp.title = (opp.title or "").replace(source.name, target.name)
            if opp.rationale:
                opp.rationale = opp.rationale.replace(source.name, target.name)

    for model in _ENTITY_REFERENCES:
        n = db.query(model).filter(
            model.entity_type == "company", model.entity_id == source.id,
        ).update({"entity_id": target.id}, synchronize_session=False)
        _count(model.__tablename__, n)

    filled: List[str] = []
    for column in Company.__table__.columns.keys():
        if column in _NOT_COPIED:
            continue
        if getattr(target, column) is None and getattr(source, column) is not None:
            setattr(target, column, getattr(source, column))
            filled.append(column)
    if source.triaged:
        target.triaged = True
    if not source.auto_created:
        # Jack entered one of them by hand: the survivor is not auto-only.
        target.auto_created = False
    target.updated_at = datetime.utcnow()
    db.flush()

    # Both companies' leases are now the target's; exactly one is current.
    choose_current(db.query(Lease).filter(Lease.company_id == target.id).all())

    # The bulk UPDATEs above bypassed the session, so any relationship
    # collection already loaded on either company is stale. Deleting source
    # with a stale collection would null the company id on the very rows just
    # moved — expire both so the delete reads what is really there (nothing).
    source_name, source_id, target_id = source.name, source.id, target.id
    db.flush()
    db.expire(source)
    db.expire(target)
    db.delete(source)
    db.flush()
    return {
        "target_id": target_id,
        "source_id": source_id,
        "source_name": source_name,
        "moved": moved,
        "filled_fields": filled,
    }
