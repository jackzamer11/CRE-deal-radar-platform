"""A company's leases: upload, read, confirm, open.

Four things happen here, in this order, and the order is the design:

  1. **Store and link, always.** The file is copied into the leases folder and
     a new Lease row written BEFORE extraction is attempted. Every downstream
     failure — no API key, an unreadable scan, a model error — still leaves Jack
     with a linked document. Never lose the document because the reading failed.
  2. **Read, and report a failure plainly.** Extraction returns each field with
     the clause text behind it; a value with no supporting text comes back
     not-found rather than inferred.
  3. **Confirm, don't silently write.** The extraction lands in a review panel
     with everything found pre-accepted. Jack scans, unchecks what is wrong, and
     types over anything the page got wrong or did not state. Only on confirm do
     values reach the lease record — and, for the current lease, lease expiry,
     premises address and rentable SF reach the company record, each marked
     "lease_document" (read off the page) or "manual" (typed by Jack).
  4. **Open the file.** The link goes through the backend, which resolves the
     stored filename against the configured folder — so a moved folder needs no
     data change, and a missing file says so instead of failing silently.

A company holds a LIST of leases (models/lease.py). A new upload is a new row
and demotes the previous one; nothing is overwritten. The ranking rules live in
services/lease_records.py.

A lease outranks CoStar. That is exactly why step 3 exists: commencement vs.
expiration, rentable vs. usable SF, and whether an option term shifts the
effective expiry are the misreadings that would move a past client's re-entry
date by years without anyone noticing.

Lease content is private: nothing here feeds generated outreach copy.
"""
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

# LEASE_DOCUMENT_SOURCE lives in companies.py too, which is where it is
# registered as a PROTECTED lease source — a CoStar import must never overwrite
# a value read off the signed document (or typed in over it).
from app.api.routes.companies import LEASE_DOCUMENT_SOURCE
from app.database import get_db
from app.models.company import Company
from app.models.lease import Lease
from app.services.lease_extraction_service import (
    COMPANY_WRITEBACK_FIELDS, LEASE_FIELDS, LEASE_FIELD_LABELS,
    ExtractionUnavailable, MissingAPIKeyError, dumps_extraction,
    extract_lease_from_pdf,
)
from app.services.lease_records import (
    FIELD_TO_COLUMN, MANUAL_SOURCE, choose_current, company_leases,
    current_lease, load_extraction, parse_field_value, write_company_field,
)
from app.services.lease_storage import (
    lease_file_exists, resolve_lease_path, store_lease_file,
)
from app.services.submarket_service import apply_derived_submarket

router = APIRouter(prefix="/leases", tags=["leases"])

# The marker written into the *_source columns for a field read off the lease.
LEASE_SOURCE = LEASE_DOCUMENT_SOURCE


# ── Schemas ───────────────────────────────────────────────────────────────────

class ExtractedField(BaseModel):
    """One extracted value beside the clause it came from."""
    field: str
    label: str
    # What the DOCUMENT said. Never replaced by a typed value — the original
    # reading stays here, beside its clause, whatever Jack types over it.
    value: Optional[str] = None
    source_text: Optional[str] = None
    page: Optional[int] = None
    # False when the document did not state it, or stated it without a clause
    # we could quote. A not-found row is never pre-accepted, but Jack can type
    # a value into it.
    found: bool = False
    # True for the three fields that write through to the company record.
    writes_to_company: bool = False
    # Everything found defaults to accepted: Jack scans and unchecks.
    accepted: bool = False
    # What Jack typed, when he typed over the page (or filled a not-found row).
    manual_value: Optional[str] = None
    # "lease_document" | "manual" once confirmed; None before.
    source: Optional[str] = None


class LeaseTerm(BaseModel):
    """One lease — current or prior — with its own file and extraction."""
    lease_id: int
    lease_file_name: Optional[str] = None
    lease_uploaded_at: Optional[datetime] = None
    is_current: bool = False
    confirmed_at: Optional[datetime] = None
    commencement_date: Optional[date] = None
    expiration_date: Optional[date] = None
    # The file is linked but not where it should be — say so plainly.
    file_missing: bool = False
    has_extraction: bool = False
    fields: List[ExtractedField] = []


class LeaseStatus(BaseModel):
    """The company's leases as the Deal card and company record see them.

    The top-level lease fields describe the CURRENT lease; prior_leases holds
    every other one, newest first, each with its own file and extraction.
    """
    company_id: int
    company_name: Optional[str] = None
    lease_id: Optional[int] = None
    lease_file_name: Optional[str] = None
    lease_uploaded_at: Optional[datetime] = None
    is_current: bool = False
    confirmed_at: Optional[datetime] = None
    commencement_date: Optional[date] = None
    expiration_date: Optional[date] = None
    file_missing: bool = False
    has_extraction: bool = False
    fields: List[ExtractedField] = []
    prior_leases: List[LeaseTerm] = []
    # Present when extraction could not run or could not be read. The file is
    # stored and linked regardless.
    extraction_error: Optional[str] = None
    extraction_skipped: bool = False


class ConfirmRequest(BaseModel):
    """The fields Jack left checked, and anything he typed.

    accepted_fields defaults to None meaning "everything that was found" — the
    panel sends the list explicitly, but a caller that sends nothing gets the
    accept-by-default behaviour rather than a silent no-op.

    manual_values maps a field to what Jack typed. A typed value is written
    only when its field is also accepted: an edited row he then unchecked still
    does not write.

    lease_id picks which lease to confirm; omitted means the current one.
    """
    accepted_fields: Optional[List[str]] = None
    manual_values: Optional[Dict[str, Optional[str]]] = None
    lease_id: Optional[int] = None


class ConfirmResult(BaseModel):
    company_id: int
    lease_id: Optional[int] = None
    # Whether the confirmed lease is the current one. Only the current lease
    # writes to the company record; a prior term fills in its own record only.
    is_current: bool = False
    # Written to the COMPANY record.
    written: Dict[str, Optional[str]] = {}
    # field -> "lease_document" | "manual" for everything saved to the lease.
    sources: Dict[str, str] = {}
    saved_to_lease: List[str] = []
    skipped: List[str] = []
    lease_expiry_date: Optional[date] = None
    lease_expiry_months: Optional[int] = None
    current_address: Optional[str] = None
    current_sf_occupied: Optional[int] = None
    current_submarket: Optional[str] = None
    # True when confirmation added the submarket to the list.
    submarket_created: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_company(db: Session, company_pk: int) -> Company:
    company = db.query(Company).filter(Company.id == company_pk).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _get_lease(db: Session, company: Company, lease_id: Optional[int]) -> Optional[Lease]:
    """The requested lease (404 if it is not this company's), else the current one."""
    if lease_id is None:
        return current_lease(db, company.id)
    lease = (
        db.query(Lease)
        .filter(Lease.id == lease_id, Lease.company_id == company.id)
        .first()
    )
    if lease is None:
        raise HTTPException(status_code=404, detail="That lease is not linked to this company.")
    return lease


def _to_fields(extraction: Dict[str, dict]) -> List[ExtractedField]:
    """Render an extraction as review-panel rows, in a fixed field order."""
    rows: List[ExtractedField] = []
    for field in LEASE_FIELDS:
        entry = extraction.get(field) or {}
        found = bool(entry.get("found")) and entry.get("value") is not None
        manual_value = entry.get("manual_value")
        rows.append(ExtractedField(
            field=field,
            label=LEASE_FIELD_LABELS[field],
            value=entry.get("value"),
            source_text=entry.get("source_text"),
            page=entry.get("page"),
            found=found,
            writes_to_company=field in COMPANY_WRITEBACK_FIELDS,
            # Accept-by-default for something actually found; once confirmed,
            # the panel reopens showing what Jack chose.
            accepted=bool(entry["accepted"]) if "accepted" in entry else found,
            manual_value=manual_value,
            source=entry.get("source"),
        ))
    return rows


def _term(lease: Lease, extraction: Optional[Dict[str, dict]] = None) -> LeaseTerm:
    data = extraction if extraction is not None else load_extraction(lease.extraction_json)
    return LeaseTerm(
        lease_id=lease.id,
        lease_file_name=lease.file_name,
        lease_uploaded_at=lease.uploaded_at,
        is_current=bool(lease.is_current),
        confirmed_at=lease.confirmed_at,
        commencement_date=lease.commencement_date,
        expiration_date=lease.expiration_date,
        file_missing=bool(lease.file_name and not lease_file_exists(lease.file_name)),
        has_extraction=bool(data),
        fields=_to_fields(data) if data else [],
    )


def _status(
    db: Session,
    company: Company,
    extraction: Optional[Dict[str, dict]] = None,
    extraction_error: Optional[str] = None,
    extraction_skipped: bool = False,
) -> LeaseStatus:
    """The whole list in one query: the current lease on top, the rest as priors."""
    leases = company_leases(db, company.id)
    current = next((l for l in leases if l.is_current), None)
    priors = [_term(l) for l in leases if l is not current]

    status = LeaseStatus(
        company_id=company.id,
        company_name=company.name,
        prior_leases=priors,
        extraction_error=extraction_error,
        extraction_skipped=extraction_skipped,
    )
    if current is not None:
        term = _term(current, extraction)
        for key, value in term.model_dump().items():
            if key == "fields":
                continue
            setattr(status, key, value)
        status.fields = term.fields
    return status


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/companies/{company_pk}", response_model=LeaseStatus)
def get_lease(company_pk: int, db: Session = Depends(get_db)):
    """Every lease and its extraction — what the Deal card renders."""
    return _status(db, _get_company(db, company_pk))


@router.post("/companies/{company_pk}/upload", response_model=LeaseStatus)
async def upload_lease(
    company_pk: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Store a lease PDF as a NEW lease for a company, then read it.

    The new lease becomes current and every other lease for the company is
    demoted — never overwritten, never deleted. Its file, extraction and
    confirmed values stay exactly as they were, readable as a prior term.

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

    for previous in db.query(Lease).filter(
        Lease.company_id == company.id, Lease.is_current.is_(True),
    ).all():
        previous.is_current = False
    lease = Lease(
        company_id=company.id,
        file_name=stored_name,
        uploaded_at=datetime.utcnow(),
        is_current=True,
    )
    db.add(lease)
    db.commit()
    db.refresh(lease)

    # ── Everything from here on is best-effort. The file is already linked. ──
    try:
        extraction = extract_lease_from_pdf(contents)
    except MissingAPIKeyError as exc:
        return _status(db, company, extraction_error=str(exc), extraction_skipped=True)
    except ExtractionUnavailable as exc:
        return _status(db, company, extraction_error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never lose the link to a read error
        return _status(
            db, company,
            extraction_error=(
                f"The lease was stored and linked, but could not be read ({exc})."
            ),
        )

    # Stored unconfirmed: this is the audit trail, not an accepted value. The
    # company record is untouched until Jack confirms.
    lease.extraction_json = dumps_extraction(extraction)
    db.commit()
    return _status(db, company, extraction=extraction)


@router.post("/companies/{company_pk}/confirm", response_model=ConfirmResult)
def confirm_lease_extraction(
    company_pk: int,
    payload: ConfirmRequest,
    db: Session = Depends(get_db),
):
    """Save the confirmed values to the lease — and, if it is current, the company.

    For every row:
      * unchecked -> nothing is written, whatever was typed into it;
      * checked with a typed value that differs from the page -> the typed value
        is written marked "manual", and the extracted value and its clause stay
        in extraction_json untouched;
      * checked, not typed over, and supported by clause text -> written marked
        "lease_document";
      * checked but neither typed nor supported -> skipped. A value with no
        clause behind it was never extracted and can never be written.

    Only the three write-back fields can change a company record, and only when
    the confirmed lease is the current one after re-ranking by commencement date.
    """
    company = _get_company(db, company_pk)
    lease = _get_lease(db, company, payload.lease_id)
    extraction = load_extraction(lease.extraction_json) if lease else {}
    if lease is None or not extraction:
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

    typed: Dict[str, str] = {}
    for field, raw in (payload.manual_values or {}).items():
        if field in FIELD_TO_COLUMN and raw is not None and str(raw).strip():
            typed[field] = str(raw).strip()

    # field -> (column-ready value, source)
    confirmed: Dict[str, tuple] = {}
    skipped: List[str] = []

    for field in LEASE_FIELDS:
        entry = extraction.get(field)
        if not isinstance(entry, dict):
            entry = {"value": None, "source_text": None, "page": None, "found": False}
            extraction[field] = entry
        supported = (
            bool(entry.get("found"))
            and entry.get("value") is not None
            and bool(entry.get("source_text"))
        )
        manual = typed.get(field)
        # Retyping exactly what the page says is not an edit.
        if manual is not None and supported and manual == str(entry.get("value")).strip():
            manual = None

        if field not in accepted:
            entry["accepted"] = False
            # An unchecked row writes nothing — including what was typed into it.
            entry.pop("manual_value", None)
            entry.pop("source", None)
            continue

        if manual is not None:
            value, source = manual, MANUAL_SOURCE
        elif supported:
            value, source = entry.get("value"), LEASE_DOCUMENT_SOURCE
        else:
            entry["accepted"] = False
            entry.pop("manual_value", None)
            entry.pop("source", None)
            skipped.append(field)
            continue

        parsed = parse_field_value(field, value)
        if parsed is None:
            entry["accepted"] = False
            entry.pop("manual_value", None)
            entry.pop("source", None)
            skipped.append(field)
            continue

        entry["accepted"] = True
        entry["source"] = source
        if source == MANUAL_SOURCE:
            # Beside, never over, the original reading: `value` and
            # `source_text` stay exactly as the page gave them.
            entry["manual_value"] = manual
        else:
            entry.pop("manual_value", None)
        confirmed[field] = (parsed, source)

    # The lease's own record. An unchecked row does not write, so a value from
    # an earlier confirmation is left as it was.
    for field, (parsed, _source) in confirmed.items():
        setattr(lease, FIELD_TO_COLUMN[field], parsed)
    lease.extraction_json = dumps_extraction(extraction)
    lease.confirmed_at = datetime.utcnow()
    db.flush()

    # Re-rank: a confirmed commencement date can move this lease ahead of, or
    # behind, the others.
    choose_current(db.query(Lease).filter(Lease.company_id == company.id).all())

    written: Dict[str, Optional[str]] = {}
    submarket_created = False
    if lease.is_current:
        for field in COMPANY_WRITEBACK_FIELDS:
            if field not in confirmed:
                continue
            parsed, source = confirmed[field]
            out = write_company_field(company, field, parsed, source)
            if out is not None:
                written[field] = out
        if "premises_address" in written:
            derived = apply_derived_submarket(db, company, company.current_address)
            submarket_created = bool(derived and derived["created"])
        company.last_modified_by_user = datetime.utcnow()

    skipped.extend(
        f for f in COMPANY_WRITEBACK_FIELDS
        if f not in confirmed and f not in skipped
    )

    db.commit()
    db.refresh(company)
    db.refresh(lease)

    return ConfirmResult(
        company_id=company.id,
        lease_id=lease.id,
        is_current=bool(lease.is_current),
        written=written,
        sources={f: s for f, (_v, s) in confirmed.items()},
        saved_to_lease=list(confirmed),
        skipped=[f for f in skipped if f in COMPANY_WRITEBACK_FIELDS],
        lease_expiry_date=company.lease_expiry_date,
        lease_expiry_months=company.lease_expiry_months,
        current_address=company.current_address,
        current_sf_occupied=company.current_sf_occupied,
        current_submarket=company.current_submarket,
        submarket_created=submarket_created,
    )


@router.post("/companies/{company_pk}/reextract", response_model=LeaseStatus)
def reextract_lease(
    company_pk: int,
    lease_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Re-read an already-stored lease — for after an API key is added.

    The current lease unless lease_id names another. The file is read back off
    disk through the resolver, so this works unchanged after the folder moves.
    """
    company = _get_company(db, company_pk)
    lease = _get_lease(db, company, lease_id)
    path = resolve_lease_path(lease.file_name) if lease else None
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
        return _status(db, company, extraction_error=str(exc), extraction_skipped=True)
    except ExtractionUnavailable as exc:
        return _status(db, company, extraction_error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return _status(db, company, extraction_error=f"The lease could not be read ({exc}).")

    lease.extraction_json = dumps_extraction(extraction)
    db.commit()
    return _status(db, company, extraction=extraction if lease.is_current else None)


def _serve(lease: Optional[Lease]):
    """Serve a lease PDF, or a plain 404 that says where it was looked for.

    Served through the backend rather than linked as a file:// URL, which a
    browser will not open from a page. Silence would leave Jack clicking a dead
    link with no idea why.
    """
    if lease is None or not lease.file_name:
        raise HTTPException(
            status_code=404, detail="No lease document is linked to this company.",
        )
    path = resolve_lease_path(lease.file_name)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{lease.file_name}' is linked to this company but is "
                "not in the leases folder. It may have been moved or renamed — "
                "check the LEASES_FOLDER setting, or upload it again."
            ),
        )
    return FileResponse(path, media_type="application/pdf", filename=lease.file_name)


@router.get("/companies/{company_pk}/file")
def open_lease_file(company_pk: int, db: Session = Depends(get_db)):
    """Serve the CURRENT lease's PDF."""
    company = _get_company(db, company_pk)
    return _serve(current_lease(db, company.id))


@router.get("/{lease_id}/file")
def open_lease_file_by_id(lease_id: int, db: Session = Depends(get_db)):
    """Serve any lease's PDF — how a prior term opens its own file."""
    return _serve(db.query(Lease).filter(Lease.id == lease_id).first())


# Removing a lease lives on the company router as
# DELETE /api/companies/{company_id}/lease?lease_id=N, next to the PATCH that
# sets the expiry by hand. There is deliberately only ONE way to remove a lease:
# an earlier unlink here cleared the fields but left the file on disk, and two
# remove-shaped endpoints differing only in whether the document survives is
# the kind of difference nobody remembers at the call site.
