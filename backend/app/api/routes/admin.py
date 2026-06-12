"""
Admin endpoints — Jack-only local use, no auth guard.

POST /api/admin/backfill-tenant-classes
    Run the tenant class deriver in backfill mode (all 395 tenants).
    dry_run=true  → preview without persisting (safe to call anytime).
    dry_run=false → persist results.
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
