import io
from typing import List, Optional
from datetime import datetime, date

import pandas as pd
from dateutil import parser as _dateparser
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.models.property import Property
from app.schemas.company import CompanyOut, CompanyListOut
from app.services import signal_engine as se
from app.services.scoring_model import score_property

router = APIRouter(prefix="/companies", tags=["companies"])

CURRENT_YEAR = 2026

# ── CoStar tenant import constants ────────────────────────────────────────────

COSTAR_SUBMARKET_MAP: dict = {
    "old town alexandria":  "Alexandria (Old Town)",
    "alexandria/old town":  "Alexandria (Old Town)",
    "falls church":         "Falls Church",
    "reston":               "Reston",
    "tysons":               "Tysons",
    "tysons corner":        "Tysons",
    "clarendon":            "Arlington (Clarendon)",
    "rosslyn":              "Arlington (Rosslyn)",
    "rosslyn/ballston":     None,
    "ballston":             "Arlington (Ballston)",
    "columbia pike":        "Arlington (Columbia Pike)",
    "mclean":               "McLean",
    "vienna":               "Vienna",
    "tysons/vienna":        "Vienna",
    "fairfax city":         "Fairfax City",
    "fairfax":              "Fairfax City",
    # Mappings observed from CoStar Tenant Locations exports as of 2026-04-30.
    # When new "unmapped submarket" warnings appear in import results, append new entries here.
    # I-395 Corridor: heuristic mapping to Arlington (Columbia Pike); CoStar's I-395 Corridor
    # without borough qualifier is ambiguous — verify per-row addresses if accuracy matters.
    "clarendon/courthouse":   "Arlington (Clarendon)",
    "i-395 corridor":         "Arlington (Columbia Pike)",
    "tysons corner/mclean":   "Tysons",
}

# Sources that represent user-verified data — never overwritten by automated imports.
PROTECTED_LEASE_SOURCES = frozenset(
    {"manual", "compstak", "sec_filing", "landlord_confirmed", "public_record"}
)

COSTAR_TENANT_COLS = [
    "Address", "Tenant Name", "Industry", "Employees", "Website",
    "Submarket", "SF Occupied", "NAICS", "City", "State", "Zip",
    "Best Tenant Contact", "Best Tenant Phone", "Tenant Representative",
    "Next Break Date", "Rent/SF/year", "Future Move", "Future Move Type",
]


# ── CoStar tenant import helpers ──────────────────────────────────────────────

def _cs_str(row: dict, col: str) -> Optional[str]:
    v = row.get(col)
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None


def _cs_float(row: dict, col: str) -> Optional[float]:
    raw = _cs_str(row, col)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def _cs_int(row: dict, col: str) -> Optional[int]:
    f = _cs_float(row, col)
    return int(f) if f is not None else None


def _parse_rent_psf(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "").replace("FS", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _months_until(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        expiry = _dateparser.parse(date_str, fuzzy=True).date()
        today = date.today()
        if expiry <= today:
            return 0
        return max(0, (expiry.year - today.year) * 12 + (expiry.month - today.month))
    except (ValueError, OverflowError, TypeError):
        return None


def _next_company_id(db: Session) -> str:
    existing_ids = [c.company_id for c in db.query(Company.company_id).all()]
    nums = []
    for cid in existing_ids:
        try:
            nums.append(int(cid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    return f"CO-{(max(nums) + 1) if nums else 1:03d}"


def _apply_costar_bonus(company: Company) -> None:
    """
    Post-signal bonuses from CoStar-specific fields.

    Rep adjustment is handled upstream in signal_engine.sig_tenant_rep —
    do NOT re-apply it here.  This function only applies the future-move
    bonus (+15) which has no signal-engine equivalent.
    """
    bonus = 0.0
    if company.future_move_flag:
        ft = (company.future_move_type or "").lower()
        if any(kw in ft for kw in ("reloc", "expan", "move", "requir")):
            bonus += 15.0
    if bonus > 0:
        company.opportunity_score = min(100.0, round(company.opportunity_score + bonus, 2))
        s = company.opportunity_score
        if s >= 75:   company.priority = "IMMEDIATE"
        elif s >= 62: company.priority = "HIGH"
        elif s >= 42: company.priority = "WORKABLE"
        else:         company.priority = "IGNORE"


def _parse_costar_tenant_row(row: dict, row_num: int) -> tuple:
    """
    Validate and parse one post-filter CoStar Tenant Locations row.
    Returns (payload_dict, None) or (None, error_dict).
    Caller has already applied state / submarket / SF-size filters.
    """
    err = {"row": row_num, "address": _cs_str(row, "Address") or "—"}

    name = _cs_str(row, "Tenant Name")
    if not name:
        return None, {**err, "reason": "Missing Tenant Name"}
    err["address"] = _cs_str(row, "Address") or "—"

    headcount = _cs_int(row, "Employees")  # None if blank or unparseable; row continues

    industry_raw = _cs_str(row, "Industry") or ""
    naics_raw    = _cs_str(row, "NAICS") or ""
    if industry_raw and naics_raw:
        industry = f"{industry_raw} ({naics_raw})"
    elif industry_raw:
        industry = industry_raw
    elif naics_raw:
        industry = naics_raw
    else:
        industry = "Unknown"

    cs_sub   = (_cs_str(row, "Submarket") or "").strip()
    submarket = COSTAR_SUBMARKET_MAP.get(cs_sub.lower())

    future_move_raw = (_cs_str(row, "Future Move") or "").strip().lower()
    future_move = future_move_raw in ("yes", "y", "true", "1")

    return {
        "name":                 name,
        "industry":             industry,
        "current_headcount":    headcount,
        "current_address":      _cs_str(row, "Address"),
        "current_submarket":    submarket,
        "current_sf":           _cs_int(row, "SF Occupied"),
        "lease_expiry_months":  _months_until(_cs_str(row, "Next Break Date")),
        "primary_contact_name": _cs_str(row, "Best Tenant Contact"),
        "primary_contact_phone":_cs_str(row, "Best Tenant Phone"),
        "tenant_representative":_cs_str(row, "Tenant Representative"),
        "current_rent_psf":     _parse_rent_psf(_cs_str(row, "Rent/SF/year")),
        "future_move_flag":     future_move,
        "future_move_type":     _cs_str(row, "Future Move Type"),
        "website":              _cs_str(row, "Website"),
    }, None


# ── Pydantic schema for manual create ─────────────────────────────────────────

class CompanyManualCreate(BaseModel):
    name: str
    industry: str
    description: Optional[str] = None
    current_headcount: int
    headcount_12mo_ago: Optional[int] = None
    open_positions: int = 0
    current_address: Optional[str] = None
    current_submarket: Optional[str] = None
    current_sf: Optional[int] = None
    lease_expiry_months: Optional[int] = None
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None


def _run_signals(company: Company) -> None:
    result = se.compute_tenant_opportunity_score(
        company.headcount_growth_pct,
        company.open_positions or 0,
        company.current_headcount,
        company.lease_expiry_months,
        company.current_sf,
        company.current_submarket,
        tenant_representative=company.tenant_representative,
        nearby_company_count=1,
    )
    breakdown = result["breakdown"]
    # Store sub-scores; None (abstain) persisted as 0.0
    company.sig_headcount_growth  = breakdown["headcount_growth"]  or 0.0
    company.sig_hiring_velocity   = breakdown["hiring_velocity"]   or 0.0
    company.sig_lease_expiry      = breakdown["lease_expiry"]      or 0.0
    company.sig_space_utilization = breakdown["space_utilization"] or 0.0
    company.sig_geo_clustering    = breakdown["geo_clustering"]    or 0.0

    composite = result["composite"]
    company.opportunity_score     = composite
    company.signals_scored_count  = result["signals_scored"]
    company.insufficient_data     = result["insufficient_data"]

    if composite >= 75:
        company.priority = "IMMEDIATE"
    elif composite >= 62:
        company.priority = "HIGH"
    elif composite >= 42:
        company.priority = "WORKABLE"
    else:
        company.priority = "IGNORE"

    # Derived fields
    if company.current_headcount:
        if company.headcount_12mo_ago and company.headcount_12mo_ago > 0:
            company.headcount_growth_pct = round(
                (company.current_headcount - company.headcount_12mo_ago)
                / company.headcount_12mo_ago * 100, 1
            )
        if company.current_headcount > 0:
            company.hiring_velocity = round(
                (company.open_positions or 0) / company.current_headcount * 100, 1
            )
        if company.current_sf and company.current_headcount > 0:
            company.sf_per_head = round(company.current_sf / company.current_headcount, 1)

    # Set expansion signal
    company.expansion_signal = (
        (company.headcount_growth_pct or 0) >= 15
        and (company.lease_expiry_months or 999) <= 24
        and (company.sf_per_head or 999) <= 150
    )


@router.post("/", response_model=CompanyOut)
def create_company(payload: CompanyManualCreate, db: Session = Depends(get_db)):
    """Manually add a new company. Signals are computed immediately after creation."""
    company_id = _next_company_id(db)

    # Derived fields
    growth_pct = None
    if payload.headcount_12mo_ago and payload.headcount_12mo_ago > 0:
        growth_pct = round(
            (payload.current_headcount - payload.headcount_12mo_ago)
            / payload.headcount_12mo_ago * 100, 1
        )

    hiring_velocity = None
    if payload.current_headcount > 0:
        hiring_velocity = round(payload.open_positions / payload.current_headcount * 100, 1)

    sf_per_head = None
    if payload.current_sf and payload.current_headcount > 0:
        sf_per_head = round(payload.current_sf / payload.current_headcount, 1)

    estimated_sf_needed = None
    if payload.current_headcount:
        growth_factor = 1 + ((growth_pct or 0) / 100.0) * 1.25
        estimated_sf_needed = int(payload.current_headcount * growth_factor * 175)

    company = Company(
        company_id            = company_id,
        name                  = payload.name,
        industry              = payload.industry,
        description           = payload.description,
        current_headcount     = payload.current_headcount,
        headcount_12mo_ago    = payload.headcount_12mo_ago,
        headcount_growth_pct  = growth_pct,
        open_positions        = payload.open_positions,
        hiring_velocity       = hiring_velocity,
        current_address       = payload.current_address,
        current_submarket     = payload.current_submarket,
        current_sf            = payload.current_sf,
        sf_per_head           = sf_per_head,
        lease_expiry_months   = payload.lease_expiry_months,
        estimated_sf_needed   = estimated_sf_needed,
        primary_contact_name  = payload.primary_contact_name,
        primary_contact_title = payload.primary_contact_title,
        primary_contact_phone = payload.primary_contact_phone,
        linkedin_url          = payload.linkedin_url,
        website               = payload.website,
    )
    db.add(company)
    db.flush()

    # Run signals immediately
    _run_signals(company)
    db.commit()
    db.refresh(company)
    return company


@router.get("/", response_model=List[CompanyListOut])
def list_companies(
    submarket: Optional[str] = None,
    priority: Optional[str] = None,
    expansion_only: bool = False,
    min_score: Optional[float] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Company)
    if submarket:
        q = q.filter(Company.current_submarket == submarket)
    if priority:
        q = q.filter(Company.priority == priority)
    if expansion_only:
        q = q.filter(Company.expansion_signal == True)
    if min_score is not None:
        q = q.filter(Company.opportunity_score >= min_score)
    companies = q.order_by(Company.opportunity_score.desc()).all()
    return companies


@router.post("/costar-import")
async def costar_tenant_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Import a CoStar Tenant Locations export (.csv or .xlsx).

    Filter pipeline:
      1. State != VA             → filtered_state
      2. Submarket unmapped      → filtered_submarket  (tracks unmapped_submarkets)
      3. SF Occupied < 2,500     → filtered_size

    Dedupe key: (Tenant Name, Address) — case-insensitive, whitespace-trimmed.
    Auto-links to an existing Property when Address matches exactly.
    Returns {total_rows, filtered_state, filtered_submarket, filtered_size,
             inserted, updated, skipped, unmapped_submarkets, errors}
    """
    fname = (file.filename or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".xlsx") or fname.endswith(".xls")):
        raise HTTPException(status_code=400, detail="File must be .csv or .xlsx")

    contents = await file.read()
    try:
        if fname.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(io.BytesIO(contents), dtype=str, keep_default_na=False, engine="openpyxl")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")

    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in COSTAR_TENANT_COLS if c not in set(df.columns)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing CoStar columns: {', '.join(missing)}")

    df = df.replace("", None)
    rows = df.to_dict(orient="records")
    total_rows = len(rows)

    filtered_state     = 0
    filtered_submarket = 0
    filtered_size      = 0
    unmapped_submarkets: set = set()
    inserted = updated = 0
    errors: list = []

    # Build dedupe index: (name.lower(), address.lower()) → Company
    existing: dict = {}
    for c in db.query(Company).all():
        key = (c.name.strip().lower(), (c.current_address or "").strip().lower())
        existing[key] = c

    for idx, row in enumerate(rows, start=2):
        # Filter 1: State must be VA
        if (_cs_str(row, "State") or "").strip().upper() != "VA":
            filtered_state += 1
            continue

        # Filter 2: Submarket must map
        cs_sub  = (_cs_str(row, "Submarket") or "").strip()
        sub_key = cs_sub.lower()
        if sub_key not in COSTAR_SUBMARKET_MAP or COSTAR_SUBMARKET_MAP[sub_key] is None:
            unmapped_submarkets.add(cs_sub or "(blank)")
            filtered_submarket += 1
            continue

        # Filter 3: SF Occupied >= 2,500
        sf_occ = _cs_float(row, "SF Occupied")
        if sf_occ is None or sf_occ < 2500:
            filtered_size += 1
            continue

        payload, err = _parse_costar_tenant_row(row, row_num=idx)
        if err:
            errors.append(err)
            continue

        name    = payload["name"]
        address = payload["current_address"] or ""
        key     = (name.strip().lower(), address.strip().lower())

        # Auto-link to matching property
        linked_prop_id = None
        if address:
            prop = db.query(Property).filter(
                func.lower(Property.address) == address.strip().lower()
            ).first()
            if prop:
                linked_prop_id = prop.id

        if key in existing:
            c = existing[key]
            c.industry              = payload["industry"]
            c.current_headcount     = payload["current_headcount"]
            c.current_address       = payload["current_address"]
            c.current_submarket     = payload["current_submarket"]
            c.current_sf            = payload["current_sf"]
            # Guard: never overwrite user-verified lease data with CoStar's value.
            # If the existing record has a protected source AND a verified date,
            # the user has manually confirmed this data — CoStar cannot override it.
            _lease_protected = (
                c.lease_expiry_source in PROTECTED_LEASE_SOURCES
                and c.lease_expiry_last_verified is not None
            )
            if not _lease_protected:
                c.lease_expiry_months = payload["lease_expiry_months"]
                if payload["lease_expiry_months"] is not None:
                    c.lease_expiry_source = "costar"
            c.primary_contact_name  = payload["primary_contact_name"] or c.primary_contact_name
            c.primary_contact_phone = payload["primary_contact_phone"] or c.primary_contact_phone
            c.tenant_representative = payload["tenant_representative"]
            c.current_rent_psf      = payload["current_rent_psf"]
            c.future_move_flag      = payload["future_move_flag"]
            c.future_move_type      = payload["future_move_type"]
            c.website               = payload["website"] or c.website
            if linked_prop_id:
                c.linked_property_id = linked_prop_id
            _run_signals(c)
            _apply_costar_bonus(c)
            updated += 1
        else:
            # Derived fields
            sf_per_head = None
            if payload["current_sf"] and payload["current_headcount"] and payload["current_headcount"] > 0:
                sf_per_head = round(payload["current_sf"] / payload["current_headcount"], 1)
            estimated_sf_needed = int(payload["current_headcount"] * 1.25 * 175) if payload["current_headcount"] else None

            c = Company(
                company_id            = _next_company_id(db),
                name                  = name,
                industry              = payload["industry"],
                current_headcount     = payload["current_headcount"],
                open_positions        = 0,
                current_address       = payload["current_address"],
                current_submarket     = payload["current_submarket"],
                current_sf            = payload["current_sf"],
                sf_per_head           = sf_per_head,
                lease_expiry_months   = payload["lease_expiry_months"],
                lease_expiry_source   = "costar" if payload["lease_expiry_months"] is not None else None,
                estimated_sf_needed   = estimated_sf_needed,
                primary_contact_name  = payload["primary_contact_name"],
                primary_contact_phone = payload["primary_contact_phone"],
                website               = payload["website"],
                tenant_representative = payload["tenant_representative"],
                current_rent_psf      = payload["current_rent_psf"],
                future_move_flag      = payload["future_move_flag"],
                future_move_type      = payload["future_move_type"],
                linked_property_id    = linked_prop_id,
            )
            db.add(c)
            db.flush()
            _run_signals(c)
            _apply_costar_bonus(c)
            existing[key] = c
            inserted += 1

    db.commit()

    return {
        "total_rows":          total_rows,
        "filtered_state":      filtered_state,
        "filtered_submarket":  filtered_submarket,
        "filtered_size":       filtered_size,
        "inserted":            inserted,
        "updated":             updated,
        "skipped":             len(errors),
        "unmapped_submarkets": sorted(unmapped_submarkets),
        "errors":              errors,
    }


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: str, db: Session = Depends(get_db)):
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


VALID_LEASE_SOURCES = {"costar", "manual", "compstak", "sec_filing", "landlord_confirmed", "public_record"}


class LeaseExpiryUpdate(BaseModel):
    lease_expiry_months: Optional[int] = None
    lease_expiry_date: Optional[str] = None   # ISO date "YYYY-MM-DD"; used to compute months when provided
    lease_expiry_source: str = "manual"


@router.patch("/{company_id}/lease", response_model=CompanyOut)
def update_lease_expiry(
    company_id: str,
    payload: LeaseExpiryUpdate,
    db: Session = Depends(get_db),
):
    """
    Manually set lease expiry for a company that has no CoStar data.
    Accepts either lease_expiry_months directly, or an ISO date string
    from which months are computed.  Re-runs signals after saving.
    """
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if payload.lease_expiry_source not in VALID_LEASE_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid lease_expiry_source; must be one of {sorted(VALID_LEASE_SOURCES)}",
        )

    if payload.lease_expiry_date:
        try:
            expiry_date = date.fromisoformat(payload.lease_expiry_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="lease_expiry_date must be YYYY-MM-DD")
        today = date.today()
        months = max(0, (expiry_date.year - today.year) * 12 + (expiry_date.month - today.month))
        company.lease_expiry_date   = expiry_date
        company.lease_expiry_months = months
    elif payload.lease_expiry_months is not None:
        company.lease_expiry_months = payload.lease_expiry_months

    company.lease_expiry_source        = payload.lease_expiry_source
    company.lease_expiry_last_verified = date.today()
    company.last_modified_by_user      = datetime.utcnow()

    _run_signals(company)
    db.commit()
    db.refresh(company)
    return company


VALID_TRAJECTORIES = {"AUTO", "CONTRACTING", "FLAT", "GROWING"}


class TrajectoryUpdate(BaseModel):
    lease_trajectory: str


@router.patch("/{company_id}/trajectory", response_model=CompanyOut)
def update_lease_trajectory(
    company_id: str,
    payload: TrajectoryUpdate,
    db: Session = Depends(get_db),
):
    """Set the broker-defined lease trajectory override for a company."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if payload.lease_trajectory not in VALID_TRAJECTORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid lease_trajectory; must be one of {sorted(VALID_TRAJECTORIES)}",
        )
    company.lease_trajectory      = payload.lease_trajectory
    company.last_modified_by_user = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return company


@router.post("/refresh-signals", response_model=dict)
def refresh_all_signals(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    for c in companies:
        _run_signals(c)
    db.commit()
    return {"refreshed": len(companies)}
