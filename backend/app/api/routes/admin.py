"""
Admin endpoints — Jack-only local use, no auth guard.

POST /api/admin/backfill-tenant-classes
    Run the tenant class deriver in backfill mode (all 395 tenants).
    dry_run=true  → preview without persisting (safe to call anytime).
    dry_run=false → persist results.

POST /api/admin/approve-75-confidence
    Bulk-approve all tenants currently matched at 75 confidence.
    CoStar/DB exact address matches on multi-tenant buildings have a
    correct class — only the multi-tenant flag prevented auto-fill.
    Always persists.  Returns {approved, companies}.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


class BackfillRequest(BaseModel):
    dry_run: bool = False


@router.post("/backfill-tenant-classes")
def backfill_tenant_classes(
    body: BackfillRequest,
    db: Session = Depends(get_db),
):
    """
    Backfill building classes for all tenants using address-matching.

    Returns a preview summary with confidence distribution and lists of
    matched / unmatched / feedback-hit tenants. Pass dry_run=false to
    persist results.
    """
    from app.services.tenant_class_deriver import derive_tenant_building_classes
    return derive_tenant_building_classes(db, backfill=True, dry_run=body.dry_run)


@router.post("/approve-75-confidence")
def approve_75_confidence(db: Session = Depends(get_db)):
    """
    Bulk-approve all confidence-75 tenants by writing their matched_class
    to current_building_class.

    Confidence-75 entries are exact address matches where multiple companies
    share the building — the class is correct, only the occupancy ambiguity
    prevented auto-fill.

    Returns {"approved": int, "companies": [{"company_id", "name", "matched_class"}]}
    """
    from app.services.tenant_class_deriver import derive_tenant_building_classes
    from app.models.company import Company

    summary = derive_tenant_building_classes(db, backfill=True, dry_run=True)
    candidates = summary["logged_75_confidence"]

    approved = []
    for entry in candidates:
        company = (
            db.query(Company)
            .filter(Company.company_id == entry["company_id"])
            .first()
        )
        if company:
            company.current_building_class = entry["matched_class"]
            approved.append({
                "company_id": entry["company_id"],
                "name": entry["name"],
                "matched_class": entry["matched_class"],
            })

    if approved:
        db.commit()

    return {"approved": len(approved), "companies": approved}
