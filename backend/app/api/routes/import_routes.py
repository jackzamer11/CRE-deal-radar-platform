"""
Import routes — file-based bulk imports beyond CoStar properties/tenants.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/import", tags=["import"])


@router.post("/costar-lease-activity")
async def import_costar_lease_activity(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a CoStar Lease Activity export (.xlsx or .csv).
    Updates in_place_rent_psf for matched properties that have no existing rent
    data, and effective_rent_psf / starting_rent_psf on name-matched tenant
    companies from the "Effective Rent (Annual)" / "Starting Rent (Annual)"
    columns (stored as-is; exact name match only; never overwrites).
    Returns {updated, skipped_no_match, skipped_existing, errors,
             tenants_matched, tenants_skipped, tenant_skips}.
    """
    fname = (file.filename or "").lower()
    if not (fname.endswith(".xlsx") or fname.endswith(".xls") or fname.endswith(".csv")):
        raise HTTPException(status_code=400, detail="File must be .xlsx, .xls, or .csv")

    contents = await file.read()
    from app.ingestion.adapters.costar_lease_activity import run_costar_lease_activity_import
    result = run_costar_lease_activity_import(contents, file.filename or "", db)

    if result.get("errors"):
        # Non-fatal — return 200 with errors list included
        pass

    return result
