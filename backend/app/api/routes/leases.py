"""The lease document linked to a company: upload, read, confirm, open.

Four things happen here, in this order, and the order is the design:

  1. **Store and link, always.** The file is copied into the leases folder and
     the bare filename written to the company BEFORE extraction is attempted.
     Every downstream failure — no API key, an unreadable scan, a model error —
     still leaves Jack with a linked document. Never lose the document because
     the reading failed.
  2. **Read, and report a failure plainly.** Extraction returns each field with
     the clause text behind it; a value with no supporting text comes back
     not-found rather than inferred.
  3. **Confirm, don't silently write.** The extraction lands in a review panel
     with everything pre-accepted. Jack scans and unchecks what is wrong; he
     does not approve item by item. Only on confirm do lease expiry, premises
     address and rentable SF reach the company record, each marked
     lease-sourced.
  4. **Open the file.** The link goes through the backend, which resolves the
     stored filename against the configured folder — so a moved folder needs no
     data change, and a missing file says so instead of failing silently.

A lease outranks CoStar. That is exactly why step 3 exists: commencement vs.
expiration, rentable vs. usable SF, and whether an option term shifts the
effective expiry are the misreadings that would move a past client's re-entry
date by years without anyone noticing.

Lease content is private: nothing here feeds generated outreach copy.
"""
import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from dateutil import parser as _dateparser
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

# LEASE_DOCUMENT_SOURCE lives in companies.py, which is also where it is
# registered as a PROTECTED lease source — a CoStar import must never overwrite
# a value read off the signed document.
from app.api.routes.companies import LEASE_DOCUMENT_SOURCE
from app.database import get_db
from app.models.company import Company
from app.schemas.company import months_until_lease_expiry
from app.services.lease_extraction_service import (
    COMPANY_WRITEBACK_FIELDS, LEASE_FIELDS, LEASE_FIELD_LABELS,
    ExtractionUnavailable, MissingAPIKeyError, dumps_extraction,
    extract_lease_from_pdf,
)
from app.services.lease_storage import (
    lease_file_exists, resolve_lease_path, store_lease_file,
)

router = APIRouter(prefix="/leases", tags=["leases"])

# The marker written into the *_source columns for a field read off the lease.
LEASE_SOURCE = LEASE_DOCUMENT_SOURCE


# ── Schemas ───────────────────────────────────────────────────────────────────

class ExtractedField(BaseModel):
    """One extracted value beside the clause it came from."""
    field: str
    label: str
    value: Optional[str] = None
    source_text: Optional[str] = None
    page: Optional[int] = None
    # False when the document did not state it, or stated it without a clause
    # we could quote. Not-found fields render as "not found" and are never
    # pre-accepted.
    found: bool = False
    # True for the three fields that write through to the company record.
    writes_to_company: bool = False
    # Everything found defaults to accepted: Jack scans and unchecks.
    accepted: bool = False


class LeaseStatus(BaseModel):
    """The linked lease as the Deal card and company record see it."""
    company_id: int
    company_name: Optional[str] = None
    lease_file_name: Optional[str] = None
    lease_uploaded_at: Optional[datetime] = None
    # The file is linked but not where it should be — say so plainly.
    file_missing: bool = False
    has_extraction: bool = False
    fields: List[ExtractedField] = []
    # Present when extraction could not run or could not be read. The file is
    # stored and linked regardless.
    extraction_error: Optional[str] = None
    extraction_skipped: bool = False


class ConfirmRequest(BaseModel):
    """The fields Jack left checked.

    Defaults to None meaning "everything that was found" — the panel sends the
    list explicitly, but a caller that sends nothing gets the accept-by-default
    behaviour rather than a silent no-op.
    """
    accepted_fields: Optional[List[str]] = None


class ConfirmResult(BaseModel):
    company_id: int
    written: Dict[str, Optional[str]] = {}
    skipped: List[str] = []
    lease_expiry_date: Optional[date] = None
    lease_expiry_months: Optional[int] = None
    current_address: Optional[str] = None
    current_sf_occupied: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_company(db: Session, company_pk: int) -> Company:
    company = db.query(Company).filter(Company.id == company_pk).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _stored_extraction(company: Company) -> Dict[str, dict]:
    """The stored extraction, or {} when there is none or it is unreadable.

    A corrupt JSON blob must not 500 the Deal card — the linked file still
    matters more than the abstract.
    """
    if not company.lease_extraction_json:
        return {}
    try:
        parsed = json.loads(company.lease_extraction_json)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _to_fields(extraction: Dict[str, dict]) -> List[ExtractedField]:
    """Render an extraction as review-panel rows, in a fixed field order."""
    rows: List[ExtractedField] = []
    for field in LEASE_FIELDS:
        entry = extraction.get(field) or {}
        found = bool(entry.get("found")) and entry.get("value") is not None
        rows.append(ExtractedField(
            field=field,
            label=LEASE_FIELD_LABELS[field],
            value=entry.get("value"),
            source_text=entry.get("source_text"),
            page=entry.get("page"),
            found=found,
            writes_to_company=field in COMPANY_WRITEBACK_FIELDS,
            # Accept-by-default, but only for something actually found.
            accepted=found,
        ))
    return rows


def _status(
    company: Company,
    extraction: Optional[Dict[str, dict]] = None,
    extraction_error: Optional[str] = None,
    extraction_skipped: bool = False,
) -> LeaseStatus:
    data = extraction if extraction is not None else _stored_extraction(company)
    return LeaseStatus(
        company_id=company.id,
        company_name=company.name,
        lease_file_name=company.lease_file_name,
        lease_uploaded_at=company.lease_uploaded_at,
        file_missing=bool(
            company.lease_file_name and not lease_file_exists(company.lease_file_name)
        ),
        has_extraction=bool(data),
        fields=_to_fields(data) if data else [],
        extraction_error=extraction_error,
        extraction_skipped=extraction_skipped,
    )


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse a date the model quoted, or None.

    None rather than an exception: an unparseable date means the field simply
    does not get written, which is the safe outcome for the one value that
    drives every downstream re-entry.
    """
    if not raw:
        return None
    try:
        return _dateparser.parse(str(raw), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_sf(raw: Optional[str]) -> Optional[int]:
    """Parse rentable SF out of the model's wording ("12,500 rentable sq ft")."""
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return value if value > 0 else None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/companies/{company_pk}", response_model=LeaseStatus)
def get_lease(company_pk: int, db: Session = Depends(get_db)):
    """The linked lease and the last extraction — what the Deal card renders."""
    return _status(_get_company(db, company_pk))


@router.post("/companies/{company_pk}/upload", response_model=LeaseStatus)
async def upload_lease(
    company_pk: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Store a lease PDF against a company, then read it.

    The store-and-link commit happens before extraction is attempted, and every
    extraction failure is caught and reported rather than raised: a 500 here
    would leave Jack thinking the upload failed when the document is safely on
    disk and linked.
    """
    company = _get_company(db, company_pk)

    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    # Only the bare filename is stored — the folder is a setting, joined at
    # read time (services/lease_storage.py).
    try:
        stored_name = store_lease_file(file.filename, contents)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not write to the leases folder ({exc}). Check the "
                "LEASES_FOLDER setting."
            ),
        ) from exc

    company.lease_file_name = stored_name
    company.lease_uploaded_at = datetime.utcnow()
    # A new document supersedes the previous abstract; the old one would
    # otherwise sit in the panel next to the new file's name.
    company.lease_extraction_json = None
    db.commit()
    db.refresh(company)

    # ── Everything from here on is best-effort. The file is already linked. ──
    try:
        extraction = extract_lease_from_pdf(contents)
    except MissingAPIKeyError as exc:
        return _status(company, extraction_error=str(exc), extraction_skipped=True)
    except ExtractionUnavailable as exc:
        return _status(company, extraction_error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never lose the link to a read error
        return _status(
            company,
            extraction_error=(
                f"The lease was stored and linked, but could not be read ({exc})."
            ),
        )

    # Stored unconfirmed: this is the audit trail, not an accepted value. The
    # company record is untouched until Jack confirms.
    company.lease_extraction_json = dumps_extraction(extraction)
    db.commit()
    db.refresh(company)
    return _status(company, extraction=extraction)


@router.post("/companies/{company_pk}/confirm", response_model=ConfirmResult)
def confirm_lease_extraction(
    company_pk: int,
    payload: ConfirmRequest,
    db: Session = Depends(get_db),
):
    """Write the confirmed fields to the company record, marked lease-sourced.

    Only the three write-back fields can change a company record. An unchecked
    field is not written; a field with no supporting clause text was never
    extracted and so can never be written, whatever the request asks for.
    """
    company = _get_company(db, company_pk)
    extraction = _stored_extraction(company)
    if not extraction:
        raise HTTPException(
            status_code=400,
            detail="There is no lease extraction to confirm for this company.",
        )

    if payload.accepted_fields is None:
        # Accept-by-default: everything the document actually stated.
        accepted = {
            f for f, e in extraction.items()
            if isinstance(e, dict) and e.get("found") and e.get("value") is not None
        }
    else:
        accepted = set(payload.accepted_fields)

    written: Dict[str, Optional[str]] = {}
    skipped: List[str] = []

    for field in COMPANY_WRITEBACK_FIELDS:
        entry = extraction.get(field) or {}
        supported = bool(entry.get("found")) and entry.get("source_text")
        if field not in accepted or not supported:
            skipped.append(field)
            continue

        raw = entry.get("value")
        if field == "lease_expiration_date":
            parsed = _parse_date(raw)
            if parsed is None:
                skipped.append(field)
                continue
            company.lease_expiry_date = parsed
            # Kept in step with the date, the same derivation every other
            # consumer uses, so the queue reads the new expiry immediately.
            company.lease_expiry_months = months_until_lease_expiry(parsed)
            company.lease_expiry_source = LEASE_SOURCE
            company.lease_expiry_last_verified = date.today()
            written[field] = parsed.isoformat()
        elif field == "premises_address":
            company.current_address = str(raw)
            company.current_address_source = LEASE_SOURCE
            written[field] = str(raw)
        elif field == "rentable_square_footage":
            parsed_sf = _parse_sf(raw)
            if parsed_sf is None:
                skipped.append(field)
                continue
            company.current_sf_occupied = parsed_sf
            company.current_sf_occupied_source = LEASE_SOURCE
            written[field] = str(parsed_sf)

    # Re-stored with the accept decisions recorded, so the panel reopens showing
    # what Jack chose and any field traces back to its clause either way.
    for field, entry in extraction.items():
        if isinstance(entry, dict):
            entry["accepted"] = field in accepted
    company.lease_extraction_json = dumps_extraction(extraction)
    company.last_modified_by_user = datetime.utcnow()
    db.commit()
    db.refresh(company)

    return ConfirmResult(
        company_id=company.id,
        written=written,
        skipped=skipped,
        lease_expiry_date=company.lease_expiry_date,
        lease_expiry_months=company.lease_expiry_months,
        current_address=company.current_address,
        current_sf_occupied=company.current_sf_occupied,
    )


@router.post("/companies/{company_pk}/reextract", response_model=LeaseStatus)
def reextract_lease(company_pk: int, db: Session = Depends(get_db)):
    """Re-read the already-stored lease — for after an API key is added.

    The file is read back off disk through the resolver, so this works
    unchanged after the leases folder moves.
    """
    company = _get_company(db, company_pk)
    path = resolve_lease_path(company.lease_file_name)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail=(
                "The lease file is not in the leases folder. Check the "
                "LEASES_FOLDER setting, or upload the document again."
            ),
        )
    with open(path, "rb") as handle:
        contents = handle.read()

    try:
        extraction = extract_lease_from_pdf(contents)
    except MissingAPIKeyError as exc:
        return _status(company, extraction_error=str(exc), extraction_skipped=True)
    except ExtractionUnavailable as exc:
        return _status(company, extraction_error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return _status(company, extraction_error=f"The lease could not be read ({exc}).")

    company.lease_extraction_json = dumps_extraction(extraction)
    db.commit()
    db.refresh(company)
    return _status(company, extraction=extraction)


@router.get("/companies/{company_pk}/file")
def open_lease_file(company_pk: int, db: Session = Depends(get_db)):
    """Serve the linked lease PDF.

    Served through the backend rather than linked as a file:// URL, which a
    browser will not open from a page. A missing file is a plain 404 with a
    message that says where it was looked for — silence would leave Jack
    clicking a dead link with no idea why.
    """
    company = _get_company(db, company_pk)
    if not company.lease_file_name:
        raise HTTPException(
            status_code=404, detail="No lease document is linked to this company.",
        )
    path = resolve_lease_path(company.lease_file_name)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{company.lease_file_name}' is linked to this company but is "
                "not in the leases folder. It may have been moved or renamed — "
                "check the LEASES_FOLDER setting, or upload it again."
            ),
        )
    return FileResponse(
        path, media_type="application/pdf", filename=company.lease_file_name,
    )


@router.delete("/companies/{company_pk}", response_model=LeaseStatus)
def unlink_lease(company_pk: int, db: Session = Depends(get_db)):
    """Unlink the lease from the company. The file on disk is left alone.

    Deliberately does not delete the document: unlinking is a bookkeeping
    correction (wrong company, wrong lease), and a broker's signed lease is not
    something an app should be able to destroy on a misclick.
    """
    company = _get_company(db, company_pk)
    company.lease_file_name = None
    company.lease_uploaded_at = None
    company.lease_extraction_json = None
    db.commit()
    db.refresh(company)
    return _status(company)
