"""
Import Lease Comps — ingest a CoStar Lease Activity PDF and populate lease
expiration dates onto existing tenant companies, matched by company NAME.

Two endpoints:

  POST /api/lease-comps/preview   (multipart PDF)
      Parse the PDF locally (pdfplumber — no API call), match each tenant to a
      company by name, and IMMEDIATELY auto-apply EXACT matches. Returns four
      lists: auto_applied, needs_review, skipped_no_match, skipped_already_set.

  POST /api/lease-comps/confirm   (JSON)
      Write the user-confirmed fuzzy ("needs review") matches.

  GET|POST /api/lease-comps/debug-text   (multipart PDF)  [TEMPORARY]
      Return pdfplumber's raw extracted text page-by-page (no matching). Used to
      diagnose why the parser produced 0 results. Remove once patterns are fixed.

Hard rules (see lease_comps_service):
  - Never overwrite a company that already has a lease expiry.
  - Soonest expiration wins when a tenant has multiple leases.
  - Malformed / unparseable PDF -> clean 400, never a 500.
  - Re-importing the same PDF is idempotent (already-set rows are skipped).
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
# Reuse the canonical signal recompute so scores stay consistent with the rest
# of the company pipeline (it reads lease_expiry_months, which we set).
from app.api.routes.companies import _run_signals
from app.services.lease_comps_service import (
    LeaseParseError,
    apply_expiry_to_company,
    compute_lease_matches,
    parse_lease_pdf,
)

router = APIRouter(prefix="/lease-comps", tags=["lease-comps"])


@router.post("/preview")
async def preview_lease_comps(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Parse the uploaded PDF, match tenants to companies, auto-apply exact hits."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    # Light sniff — CoStar exports are real PDFs. Reject non-PDF uploads early
    # with a clear message rather than letting pdfplumber raise.
    if not raw.lstrip()[:5].startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PDF (CoStar Lease Activity export).",
        )

    try:
        parsed = parse_lease_pdf(raw)
    except LeaseParseError as e:
        raise HTTPException(status_code=400, detail=f"Could not parse PDF: {e}")

    companies = db.query(Company).all()
    result = compute_lease_matches(parsed, companies)

    # Auto-apply EXACT matches now. (Re-check existence + never-overwrite at the
    # row to stay correct even if state changed since compute.)
    applied = []
    for item in result["auto_applied"]:
        c = db.query(Company).filter(Company.company_id == item["company_id"]).first()
        if c is None or c.lease_expiry_date is not None or c.lease_expiry_months is not None:
            continue
        apply_expiry_to_company(c, date.fromisoformat(item["proposed_expiry"]))
        _run_signals(c)
        applied.append(item)
    db.commit()

    result["auto_applied"] = applied
    result["parsed_count"] = len(parsed)
    return result


@router.api_route("/debug-text", methods=["GET", "POST"])
async def debug_lease_comps_text(file: UploadFile = File(...)):
    """TEMPORARY diagnostic — return pdfplumber's raw extracted text, page by page.

    No matching, no parsing of fields — just what pdfplumber sees, so we can
    compare the real CoStar layout against the parser's label/header patterns.
    Remove once the parser is dialed in.

    Registered for GET (as requested) and POST. A multipart file upload needs a
    request body, and many clients drop bodies on GET — if your tool refuses to
    send the file over GET, use POST: curl -F file=@export.pdf .../debug-text
    """
    import io

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if not raw.lstrip()[:5].startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PDF (CoStar Lease Activity export).",
        )

    try:
        import pdfplumber
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"pdfplumber unavailable: {e}")

    pages = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append({
                    "page": i,
                    "char_count": len(text),
                    "line_count": len(text.splitlines()),
                    "lines": text.splitlines(),
                    "text": text,
                })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {e}")

    return {
        "filename": file.filename,
        "page_count": len(pages),
        "total_chars": sum(p["char_count"] for p in pages),
        "pages": pages,
    }


class LeaseCompMatch(BaseModel):
    company_id: str
    expiration_date: str   # ISO "YYYY-MM-DD"


class LeaseCompsConfirm(BaseModel):
    matches: List[LeaseCompMatch] = []


@router.post("/confirm")
def confirm_lease_comps(
    payload: LeaseCompsConfirm,
    db: Session = Depends(get_db),
):
    """Write the user-confirmed review matches (still never overwriting)."""
    applied = []
    skipped = []
    for m in payload.matches:
        c = db.query(Company).filter(Company.company_id == m.company_id).first()
        if c is None:
            skipped.append({"company_id": m.company_id, "reason": "company not found"})
            continue
        if c.lease_expiry_date is not None or c.lease_expiry_months is not None:
            skipped.append({
                "company_id": m.company_id,
                "company_name": c.name,
                "reason": "already set — not overwritten",
            })
            continue
        try:
            expiry = date.fromisoformat(m.expiration_date)
        except ValueError:
            skipped.append({"company_id": m.company_id, "reason": "invalid date"})
            continue
        apply_expiry_to_company(c, expiry)
        _run_signals(c)
        applied.append({
            "company_id": c.company_id,
            "company_name": c.name,
            "expiration_date": expiry.isoformat(),
        })
    db.commit()
    return {"applied": applied, "skipped": skipped}
