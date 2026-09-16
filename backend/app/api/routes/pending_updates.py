"""Values an email stated about a company, awaiting Jack's confirmation.

The confirmation exists for one reason: headcount, growth, lease expiry and
square footage are scoring inputs, and a sentence in an email is not verified
data. So a stated value queues here showing what was said, what is on file, and
the sentence it came from — and moves onto the record only when Jack says so.

Accept writes the value and marks it conversation-sourced, which is both how
Jack can later see where a number came from and how the CoStar import knows not
to overwrite it. Reject leaves the company field alone, keeps the claim on the
thread, and sets Company.has_data_conflict: a tenant who believes something
different from the record is itself a lead — a renewal option, a sublease, a
phased expiry — so the disagreement is surfaced, not discarded.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.email_ingest import (
    ACCEPTED, PENDING, REJECTED, PendingCompanyUpdate,
)
from app.schemas.pending_update import PendingUpdateDigest, PendingUpdateOut
from app.services.email_ingest_service import (
    FIELD_LABEL, StatedValueError, accept_pending_update,
)

router = APIRouter(prefix="/pending-updates", tags=["pending-updates"])


def to_out(
    row: PendingCompanyUpdate,
    company_name: Optional[str] = None,
    entry_date=None,
) -> PendingUpdateOut:
    return PendingUpdateOut(
        id=row.id,
        company_id=row.company_id,
        company_name=company_name,
        field=row.field,
        label=FIELD_LABEL.get(row.field, row.field),
        proposed_value=row.proposed_value,
        current_value=row.current_value,
        source_sentence=row.source_sentence,
        source_entry_id=row.source_entry_id,
        source_entry_date=entry_date,
        status=row.status or PENDING,
        created_at=row.created_at,
    )


def pending_updates_for_company(
    db: Session, company_id: Optional[int],
) -> List[PendingUpdateOut]:
    """Pending claims against one company, ready to render.

    Two queries regardless of how many claims there are — the companies and the
    source entries are fetched in bulk, never one lookup per row. Empty list,
    never a 500, when there are none.
    """
    if company_id is None:
        return []
    rows = (
        db.query(PendingCompanyUpdate)
        .filter(
            PendingCompanyUpdate.company_id == company_id,
            PendingCompanyUpdate.status == PENDING,
        )
        .order_by(PendingCompanyUpdate.id.desc())
        .all()
    )
    return _hydrate(db, rows)


def _hydrate(db: Session, rows: List[PendingCompanyUpdate]) -> List[PendingUpdateOut]:
    if not rows:
        return []
    company_ids = {r.company_id for r in rows if r.company_id}
    entry_ids = {r.source_entry_id for r in rows if r.source_entry_id}
    names = {}
    if company_ids:
        names = {
            cid: name for cid, name in
            db.query(Company.id, Company.name).filter(Company.id.in_(company_ids)).all()
        }
    dates = {}
    if entry_ids:
        dates = {
            eid: log_date for eid, log_date in
            db.query(ActivityLog.id, ActivityLog.log_date)
            .filter(ActivityLog.id.in_(entry_ids)).all()
        }
    return [
        to_out(r, names.get(r.company_id), dates.get(r.source_entry_id))
        for r in rows
    ]


@router.get("/", response_model=PendingUpdateDigest)
def list_pending_updates(
    company_id: Optional[int] = None,
    limit: int = Query(500, ge=1),
    db: Session = Depends(get_db),
):
    """Everything awaiting Jack's confirmation, with a count the digest reads.

    `total` is the count the scheduled task reports at the end of a run — "four
    stated values are waiting on you" — so it is computed from the query rather
    than from the length of the returned page.
    """
    base = db.query(PendingCompanyUpdate).filter(
        PendingCompanyUpdate.status == PENDING
    )
    if company_id is not None:
        base = base.filter(PendingCompanyUpdate.company_id == company_id)

    total = base.count()
    rows = base.order_by(PendingCompanyUpdate.id.desc()).limit(limit).all()
    items = _hydrate(db, rows)

    by_company: dict = {}
    for item in items:
        bucket = by_company.setdefault(
            item.company_id,
            {"company_id": item.company_id, "company_name": item.company_name, "count": 0},
        )
        bucket["count"] += 1

    return PendingUpdateDigest(
        total=total, by_company=list(by_company.values()), updates=items,
    )


def _get_row(db: Session, update_id: int) -> PendingCompanyUpdate:
    row = db.query(PendingCompanyUpdate).filter(
        PendingCompanyUpdate.id == update_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Pending update not found")
    return row


@router.post("/{update_id}/accept", response_model=PendingUpdateOut)
def accept_update(update_id: int, db: Session = Depends(get_db)):
    """Write the stated value onto the company, marked conversation-sourced.

    This is the only path from something someone said in an email to a scoring
    field. The marker rides along with the value so the number's origin stays
    visible and the next CoStar import leaves it alone.
    """
    row = _get_row(db, update_id)
    if (row.status or PENDING) != PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"This update was already {row.status}.",
        )
    company = db.query(Company).filter(Company.id == row.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        accept_pending_update(db, row, company)
    except StatedValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    db.refresh(row)
    return to_out(row, company.name)


@router.post("/{update_id}/reject", response_model=PendingUpdateOut)
def reject_update(update_id: int, db: Session = Depends(get_db)):
    """Keep the value on file and mark the disagreement.

    The company field is untouched, the claim stays on the thread where it was
    said, and has_data_conflict goes up — a tenant who believes their lease ends
    a year later than the record is a lead, not a data-entry error.
    """
    row = _get_row(db, update_id)
    if (row.status or PENDING) != PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"This update was already {row.status}.",
        )
    company = db.query(Company).filter(Company.id == row.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    row.status = REJECTED
    row.resolved_at = datetime.utcnow()
    company.has_data_conflict = True
    db.commit()
    db.refresh(row)
    return to_out(row, company.name)
