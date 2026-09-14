import io
from datetime import datetime, date, timedelta
from typing import List, Optional

import pandas as pd
from dateutil import parser as _dateparser
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.company import Company
from app.models.outreach_log import OutreachLog
from app.models.property import Property
from app.schemas.company import CompanyOut, CompanyListOut, months_until_lease_expiry
from app.schemas.property import MatchedProperty
from app.schemas.outreach import OutreachDraft, OutreachLogCreate, OutreachLogOut, CallScript
from app.services import signal_engine as se
from app.services.scoring_model import score_property
from app.services.match_scoring import medical_mismatch_penalty
from app.services.lease_storage import delete_lease_file
from app.services.lease_records import (
    CONFIRMED_LEASE_SOURCES, LEASE_DOCUMENT_SOURCE, MANUAL_SOURCE, choose_current,
    company_leases, current_lease, sync_company_from_lease,
)
from app.services.submarket_service import get_or_create_submarket
from app.services.rep_classification import classify_rep

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
    "clarendon/courthouse":      "Arlington (Clarendon)",
    "i-395 corridor":            "Arlington (Columbia Pike)",
    "tysons corner/mclean":      "Tysons",
    "annandale":                 "Annandale",
    "arlington":                 "Arlington (Rosslyn)",
    "ballston/virginia square":  "Arlington (Ballston)",
    "douglas park":              "Arlington (Columbia Pike)",
    "falls church/bailey's":     "Falls Church",
    "landmark/van dorn":         "Alexandria (Old Town)",
    "merrifield":                "Merrifield",
    "n arlington/e fallschurch": "Arlington (Rosslyn)",
    "oakton":                    "Fairfax City",
    "old town":                  "Alexandria (Old Town)",
    "potomac yard":              "Crystal City",
    "tysons central":            "Tysons",
}

# Sources that represent user-verified data — never overwritten by automated
# imports. "lease_document" is a value read off the signed lease and confirmed
# by Jack in the extraction review panel: a lease outranks CoStar, so an import
# must never move it. "manual" is a value Jack typed — in that same panel, or by
# hand — and outranks CoStar for the same reason. See api/routes/leases.py.
# LEASE_DOCUMENT_SOURCE and MANUAL_SOURCE are defined in services/lease_records
# and re-exported here, where the import guard reads them.
PROTECTED_LEASE_SOURCES = frozenset(
    {
        "manual", "compstak", "sec_filing", "landlord_confirmed", "public_record",
        LEASE_DOCUMENT_SOURCE,
    }
)

COSTAR_TENANT_COLS = [
    "Address", "Tenant Name", "Industry", "Employees", "Website",
    "Submarket", "SF Occupied", "NAICS", "City", "State", "Zip",
    "Best Tenant Contact", "Best Tenant Phone", "Tenant Representative",
    "Next Break Date", "Rent/SF/year", "Future Move", "Future Move Type",
]

# The minimum occupied-square-footage floor for a tenant location to be
# imported lives in settings.TENANT_MIN_OCCUPIED_SF (config.py, default 0 =
# no floor). It is read at request time so the setting / env var can change
# the behaviour without a code edit. Rows with missing/blank "SF Occupied"
# always pass the floor — see Filter 3 in costar_tenant_import below.


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
        "current_sf_occupied":  _cs_int(row, "SF Occupied"),
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
    current_headcount: Optional[int] = None
    headcount_12mo_ago: Optional[int] = None
    # Null = never entered (abstain); 0 = explicitly confirmed zero.
    open_positions: Optional[int] = None
    current_address: Optional[str] = None
    current_submarket: Optional[str] = None
    current_sf_occupied: Optional[int] = None
    current_building_class: Optional[str] = None
    lease_expiry_months: Optional[int] = None
    effective_rent_psf: Optional[float] = None
    starting_rent_psf: Optional[float] = None
    building_asking_rent_psf: Optional[float] = None
    lease_signed_year: Optional[int] = None
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None


def _run_signals(company: Company) -> None:
    # lease_expiry_months can go stale relative to lease_expiry_date (e.g. a
    # direct data edit that only touched the date). Whenever a date is on
    # record, re-derive months from it — the exact same calculation the
    # display schemas use — so the scorer never abstains on a signal the UI
    # is showing a real value for. Self-heals the column for any other
    # consumer that reads it directly.
    if company.lease_expiry_date:
        company.lease_expiry_months = months_until_lease_expiry(company.lease_expiry_date)

    result = se.compute_tenant_opportunity_score(
        company.headcount_growth_pct,
        company.open_positions,          # None → hiring_velocity abstains correctly
        company.current_headcount,
        company.lease_expiry_months,
        company.current_sf_occupied,
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
    company.expiry_priority_override = se.compute_expiry_priority_override(
        company.insufficient_data, company.lease_expiry_months
    )

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
        if company.open_positions is not None and company.current_headcount > 0:
            company.hiring_velocity = round(
                company.open_positions / company.current_headcount * 100, 1
            )
        if company.current_sf_occupied and company.current_headcount > 0:
            company.sf_per_head = round(company.current_sf_occupied / company.current_headcount, 1)

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

    # Derived fields — null-safe: current_headcount may be left blank.
    growth_pct = None
    if payload.current_headcount and payload.headcount_12mo_ago and payload.headcount_12mo_ago > 0:
        growth_pct = round(
            (payload.current_headcount - payload.headcount_12mo_ago)
            / payload.headcount_12mo_ago * 100, 1
        )

    hiring_velocity = None
    if payload.open_positions is not None and payload.current_headcount and payload.current_headcount > 0:
        hiring_velocity = round(payload.open_positions / payload.current_headcount * 100, 1)

    sf_per_head = None
    if payload.current_sf_occupied and payload.current_headcount and payload.current_headcount > 0:
        sf_per_head = round(payload.current_sf_occupied / payload.current_headcount, 1)

    lease_expiry_date_val = None
    if payload.lease_expiry_months and payload.lease_expiry_months > 0:
        from dateutil.relativedelta import relativedelta
        lease_expiry_date_val = date.today() + relativedelta(months=payload.lease_expiry_months)

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
        current_sf_occupied   = payload.current_sf_occupied,
        current_building_class = payload.current_building_class,
        sf_per_head           = sf_per_head,
        lease_expiry_months   = payload.lease_expiry_months,
        lease_expiry_date     = lease_expiry_date_val,
        effective_rent_psf    = payload.effective_rent_psf,
        starting_rent_psf     = payload.starting_rent_psf,
        building_asking_rent_psf = payload.building_asking_rent_psf,
        lease_signed_year     = payload.lease_signed_year,
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
    rep_filter: Optional[str] = None,          # BLANK | MAJOR | OTHER
    outreach_status: Optional[str] = None,     # needs-outreach
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

    # Rep filter — applied post-query since rep_class is computed
    if rep_filter in ("BLANK", "MAJOR", "OTHER"):
        companies = [c for c in companies if classify_rep(c.tenant_representative) == rep_filter]

    # Outreach status filter: companies with no log entry in the last 90 days
    # Excludes MAJOR-rep tenants (resource-framing only, not direct-rep targets)
    if outreach_status == "needs-outreach":
        cutoff = datetime.utcnow() - timedelta(days=90)
        recent_company_ids = {
            row[0] for row in db.query(OutreachLog.company_id)
            .filter(OutreachLog.generated_at >= cutoff).all()
        }
        companies = [
            c for c in companies
            if c.id not in recent_company_ids
            and classify_rep(c.tenant_representative) != "MAJOR"
            and c.priority != "IGNORE"
        ]

    return companies


class CompanyPickerRow(BaseModel):
    """The minimum a company picker needs: which company, and its name."""
    id: int
    company_id: str
    name: str
    submarket: Optional[str] = None


@router.get("/search", response_model=List[CompanyPickerRow])
def search_companies(
    q: str = Query("", description="Type-ahead over company name and CO-nnn id"),
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Type-ahead for the company pickers — moving an entry's stamp, or setting
    a contact's employer.

    Declared before /{company_id} so the static path is not parsed as an id.
    Returns four columns rather than the full company row: the picker never
    needs scores or benchmarks, and GET /companies/ is an unpaginated fetch of
    every company.
    """
    term = (q or "").strip()
    if not term:
        return []
    like = f"%{term.lower()}%"
    rows = (
        db.query(Company)
        .filter(or_(
            func.lower(Company.name).like(like),
            func.lower(Company.company_id).like(like),
        ))
        .order_by(Company.name.asc())
        .limit(max(1, limit))
        .all()
    )
    return [
        CompanyPickerRow(
            id=c.id, company_id=c.company_id, name=c.name,
            submarket=c.current_submarket,
        )
        for c in rows
    ]


@router.post("/costar-import")
async def costar_tenant_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Import a CoStar Tenant Locations export (.csv or .xlsx).

    Filter pipeline:
      1. State != VA                              → filtered_state
      2. Submarket unmapped                       → filtered_submarket  (tracks unmapped_submarkets)
      3. SF Occupied < TENANT_MIN_OCCUPIED_SF     → filtered_size
         (blank/missing SF Occupied always passes; default floor is 0)

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

        # Filter 3: SF Occupied >= TENANT_MIN_OCCUPIED_SF (read live from settings).
        # Null-safe: a missing/blank/unparseable SF Occupied passes the floor
        # rather than crashing or being silently dropped.
        sf_occ = _cs_float(row, "SF Occupied")
        if sf_occ is not None and sf_occ < settings.TENANT_MIN_OCCUPIED_SF:
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
            # Address and SF confirmed from a lease outrank CoStar's values for
            # the same reason the expiry does — the document is the primary
            # record. That covers a value read off the page ("lease_document")
            # AND one Jack typed in the review panel ("manual"). No other
            # source is set on these two columns, so every other record keeps
            # the previous import behaviour exactly.
            if getattr(c, "current_address_source", None) not in CONFIRMED_LEASE_SOURCES:
                c.current_address   = payload["current_address"]
            c.current_submarket     = payload["current_submarket"]
            if getattr(c, "current_sf_occupied_source", None) not in CONFIRMED_LEASE_SOURCES:
                c.current_sf_occupied = payload["current_sf_occupied"]
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
            if payload["current_sf_occupied"] and payload["current_headcount"] and payload["current_headcount"] > 0:
                sf_per_head = round(payload["current_sf_occupied"] / payload["current_headcount"], 1)

            c = Company(
                company_id            = _next_company_id(db),
                name                  = name,
                industry              = payload["industry"],
                current_headcount     = payload["current_headcount"],
                open_positions        = 0,
                current_address       = payload["current_address"],
                current_submarket     = payload["current_submarket"],
                current_sf_occupied   = payload["current_sf_occupied"],
                sf_per_head           = sf_per_head,
                lease_expiry_months   = payload["lease_expiry_months"],
                lease_expiry_source   = "costar" if payload["lease_expiry_months"] is not None else None,
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


def _compute_matched_properties(company: Company, db: Session) -> list:
    from app.schemas.company import MatchedProperty
    from app.services.match_scoring import compute_match, lease_expiry_chip_label
    from sqlalchemy import or_

    # SF source is the company's real occupied SF — never calculated. Unknown SF
    # yields no matched-property cards on the company surface (existing behaviour).
    sf_occupied = company.current_sf_occupied or 0
    if sf_occupied <= 0:
        return []

    candidates = db.query(Property).filter(
        or_(
            Property.sf_avail > 0,
            Property.vacant_sf > 0,
        )
    ).all()

    scored = []
    for prop in candidates:
        # Fix 1: the SF delta filter uses AVAILABLE SF only (never total/vacant).
        # Bug fix: on the Company-card matched-properties display the SF delta is a
        # HARD data-quality filter — a pairing whose occupied-vs-available gap exceeds
        # MAX_SF_DELTA (e.g. 40,000 SF occupied vs 2,954 SF available) is never a real
        # match and must be suppressed regardless of contacted history. The composite
        # SF gate runs inside compute_match with NO exemption on this surface.
        avail = prop.sf_avail or 0
        match = compute_match(
            tenant_submarket=company.current_submarket,
            property_submarket=prop.submarket,
            tenant_class=getattr(company, "current_building_class", None),
            property_class=prop.asset_class,
            sf_needed=sf_occupied,
            sf_avail=avail,
            tenant_lease_expiry_months=company.lease_expiry_months,
        )
        if match is None:
            continue

        reasons = [
            f"SF fit {match['sf_fit_score']:.0f}/100 ({sf_occupied:,} occupied vs {avail:,} avail)",
            (f"Adjacent submarket ({prop.submarket})" if match["adjacent"]
             else f"Same submarket ({prop.submarket})"),
            f"Class fit {match['class_score']:.0f}/100",
            f"Lease: {lease_expiry_chip_label(company.lease_expiry_months)}",
        ]
        if company.current_rent_psf and prop.in_place_rent_psf:
            if prop.in_place_rent_psf <= company.current_rent_psf * 1.2:
                reasons.append(
                    f"Affordable rent (${prop.in_place_rent_psf:.0f} vs ${company.current_rent_psf:.0f} current)"
                )
        if prop.signal_score and prop.signal_score >= 60:
            reasons.append(f"High landlord motivation ({prop.signal_score:.0f})")
        # Soft medical/non-medical mismatch penalty — match still appears.
        penalty = medical_mismatch_penalty(prop, company)
        if penalty:
            match["score"] = round(match["score"] + penalty, 1)
            reasons.append("Medical/non-medical mismatch (−20)")
        scored.append((match, prop, reasons))

    scored.sort(key=lambda x: x[0]["score"], reverse=True)
    return [
        MatchedProperty(
            property_id=prop.property_id,
            address=prop.address,
            submarket=prop.submarket,
            sf_avail=prop.sf_avail or (int(prop.vacant_sf) if prop.vacant_sf else None),
            vacancy_pct=prop.vacancy_pct,
            in_place_rent_psf=prop.in_place_rent_psf,
            market_rent_psf=prop.market_rent_psf,
            landlord_representative=prop.landlord_representative,
            landlord_rep_contact=prop.landlord_rep_contact,
            sales_contact=prop.sales_contact,
            listed_for_sale=bool(prop.listed_for_sale or False),
            match_score=match["score"],
            match_reasons=reasons,
            adjacent_submarket=match["adjacent"],
            is_medical=bool(prop.is_medical),
        )
        for match, prop, reasons in scored[:3]
    ]


def _company_out(company: Company, db: Session):
    """Serialize a Company to CompanyOut WITH matched_properties computed.

    Every endpoint whose response the frontend uses to replace the selected
    company state must go through here — returning the raw ORM object lets
    matched_properties fall back to the schema default [] and the matched
    property cards vanish from the detail panel after the edit."""
    from app.schemas.company import CompanyOut as CompanyOutSchema
    out = CompanyOutSchema.model_validate(company)
    out.matched_properties = _compute_matched_properties(company, db)
    # The linked document is the CURRENT lease — a row in the leases table,
    # not a column on the company.
    lease = current_lease(db, company.id)
    out.lease_file_name = lease.file_name if lease else None
    out.lease_uploaded_at = lease.uploaded_at if lease else None
    return out


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: str, db: Session = Depends(get_db)):
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return _company_out(company, db)


@router.delete("/{company_id}", status_code=200)
def delete_company(company_id: str, db: Session = Depends(get_db)):
    """Hard-delete a company from the DB. No soft delete.

    Dependent opportunities / activity / outreach logs have their company_id
    nulled by the ORM relationship default. Returns 404 if the record is absent.
    """
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    db.delete(company)
    db.commit()
    return {"deleted": company_id}


class SnoozeRequest(BaseModel):
    snoozed_until: date                  # must be at least tomorrow (validated on frontend)
    snooze_reason: Optional[str] = None  # free text — e.g. "Just signed renewal — revisit next cycle"


@router.post("/{company_id}/snooze", response_model=CompanyOut)
def snooze_company(company_id: str, payload: SnoozeRequest, db: Session = Depends(get_db)):
    """Snooze a company — hide it from the Daily Briefing / outreach queue until snoozed_until date."""
    from app.models.activity import ActivityLog
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.snoozed_until        = payload.snoozed_until
    company.snooze_reason        = payload.snooze_reason
    company.returned_from_snooze = None
    reason_str = f": {payload.snooze_reason}" if payload.snooze_reason else ""
    db.add(ActivityLog(
        company_id=company.id,
        action_type="NOTE",
        action_taken=f"Snoozed until {payload.snoozed_until.isoformat()}{reason_str}",
        created_by="user",
    ))
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


@router.post("/{company_id}/unsnooze", response_model=CompanyOut)
def unsnooze_company(company_id: str, db: Session = Depends(get_db)):
    """Remove a snooze — company immediately returns to the Daily Briefing / outreach queue."""
    from app.models.activity import ActivityLog
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.snoozed_until        = None
    company.snooze_reason        = None
    company.returned_from_snooze = None
    db.add(ActivityLog(
        company_id=company.id,
        action_type="NOTE",
        action_taken="Unsnoozed manually",
        created_by="user",
    ))
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


VALID_LEASE_SOURCES = {
    "costar", "manual", "compstak", "sec_filing", "landlord_confirmed",
    "public_record", LEASE_DOCUMENT_SOURCE,
}


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
        if payload.lease_expiry_months > 0:
            from dateutil.relativedelta import relativedelta
            company.lease_expiry_date = date.today() + relativedelta(months=payload.lease_expiry_months)

    company.lease_expiry_source        = payload.lease_expiry_source
    company.lease_expiry_last_verified = date.today()
    company.last_modified_by_user      = datetime.utcnow()

    _run_signals(company)
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


class LeaseRemovalResult(BaseModel):
    """What became of the lease, its file, and which lease is current now."""
    company_id: str
    removed_lease_id: Optional[int] = None
    removed_file_name: Optional[str] = None
    # deleted | absent | refused | error: ...  — "absent" means the file was
    # already gone from the folder, which is a clean outcome, not a failure.
    file_outcome: str = "absent"
    # The lease promoted to current because the current one was removed. None
    # when a prior term was removed, or when no lease is left.
    promoted_lease_id: Optional[int] = None
    promoted_file_name: Optional[str] = None
    # Set when the lease was removed but the file could not be, so the UI can
    # say so instead of implying the document is gone.
    warning: Optional[str] = None


@router.delete("/{company_id}/lease", response_model=LeaseRemovalResult)
def remove_lease(
    company_id: str,
    lease_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Remove ONE lease: its record and its file. Other leases are untouched.

    lease_id names the lease; omitted, it is the current one. When the current
    lease is removed, the next most recent (latest commencement date) is
    promoted to current and its confirmed expiry, address and SF are written to
    the company, each with the source it was confirmed under.

    Jack uploaded a draft by mistake and had no way to clear it — this is that
    way out.

    Two things it deliberately does NOT do:

    1. **It does not roll back a confirmed value.** With no lease left to
       promote — or a promoted lease that does not hold a field — the company's
       lease_expiry_date, current_address and current_sf_occupied stay exactly
       as they are, and so do their source markers. Jack read those values and
       accepted them; removing the document does not un-know them, and silently
       reverting a verified expiry would move a tenant's place in the queue
       behind his back. Keeping the markers also keeps the CoStar import guard
       in force.
    2. **It does not fail on a missing file.** A file already gone from the
       folder is a clean outcome: the lease still goes. Nor does a file that
       cannot be deleted (open in a viewer, which on Windows locks it) block
       the removal — the result says what happened, because the alternative is
       Jack stuck with a document he cannot clear.

    Keyed by the CO-nnn business id, like every other route on this router
    (and like PATCH /{company_id}/lease directly above).
    """
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    leases = company_leases(db, company.id)
    if not leases:
        # A clean 404 naming the situation, never a 500 and never a silent OK.
        raise HTTPException(
            status_code=404,
            detail="No lease document is linked to this company.",
        )
    if lease_id is None:
        target = next((l for l in leases if l.is_current), leases[0])
    else:
        target = next((l for l in leases if l.id == lease_id), None)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail="That lease is not linked to this company.",
            )

    remaining = [l for l in leases if l is not target]
    file_name = target.file_name
    # Collision-safe naming means no two leases share a file, but a file another
    # lease still points at is never deleted out from under it.
    if file_name and any(l.file_name == file_name for l in remaining):
        outcome = "absent"
    else:
        outcome = delete_lease_file(file_name)

    was_current = bool(target.is_current)
    removed_id = target.id
    db.delete(target)

    promoted = None
    if remaining and (was_current or not any(l.is_current for l in remaining)):
        promoted = choose_current(remaining)
        sync_company_from_lease(company, promoted)
        _run_signals(company)
    promoted_id = promoted.id if promoted else None
    promoted_name = promoted.file_name if promoted else None

    company.last_modified_by_user = datetime.utcnow()
    db.commit()

    warning = None
    if outcome.startswith("error:"):
        warning = (
            f"The lease was removed, but '{file_name}' could not be deleted "
            f"({outcome[len('error: '):]}). It may be open in another program — "
            "delete it from the leases folder by hand."
        )
    elif outcome == "refused":
        warning = (
            f"The lease was removed. '{file_name}' was left alone because the "
            "stored value is not a plain filename."
        )

    return LeaseRemovalResult(
        company_id=company.company_id,
        removed_lease_id=removed_id,
        removed_file_name=file_name,
        file_outcome=outcome,
        promoted_lease_id=promoted_id,
        promoted_file_name=promoted_name,
        warning=warning,
    )


class SubmarketUpdate(BaseModel):
    # Null or blank clears the submarket back to unknown.
    current_submarket: Optional[str] = None


@router.patch("/{company_id}/submarket", response_model=CompanyOut)
def update_submarket(
    company_id: str,
    payload: SubmarketUpdate,
    db: Session = Depends(get_db),
):
    """Set a company's submarket. Any name not on the list joins it.

    Matched case-insensitively against the list first, so the company is given
    the canonical spelling ("sterling" -> "Sterling") and no duplicate is made.
    """
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    raw = (payload.current_submarket or "").strip()
    if raw:
        try:
            row, _created = get_or_create_submarket(db, raw, auto_created=False)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        company.current_submarket = row.name
    else:
        company.current_submarket = None

    company.last_modified_by_user = datetime.utcnow()
    _run_signals(company)
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


VALID_BUILDING_CLASSES = {"Class A", "Class B", "Class C"}


class BuildingClassUpdate(BaseModel):
    # None or "" clears the value back to unknown (class-fit factor → neutral 50)
    current_building_class: Optional[str] = None


@router.patch("/{company_id}/building-class", response_model=CompanyOut)
def update_building_class(
    company_id: str,
    payload: BuildingClassUpdate,
    db: Session = Depends(get_db),
):
    """Set or clear the tenant's current building class (drives the class-fit
    factor of the composite Match Score). Allows backfilling existing tenants
    without recreating them."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    value = (payload.current_building_class or "").strip() or None
    if value is not None and value not in VALID_BUILDING_CLASSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid current_building_class; must be one of {sorted(VALID_BUILDING_CLASSES)} or null",
        )
    company.current_building_class = value
    company.last_modified_by_user  = datetime.utcnow()
    db.commit()
    db.refresh(company)
    # Log to feedback table so the deriver learns from this correction and
    # won't re-guess the wrong class for this address on future runs.
    try:
        from app.services.tenant_class_deriver import record_building_class_feedback
        record_building_class_feedback(company, value, db)
    except Exception:
        pass  # feedback is best-effort; never fail the PATCH
    return _company_out(company, db)


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
    return _company_out(company, db)


class SfOccupiedUpdate(BaseModel):
    # Nullable: clearing the field (SF unknown) is a valid edit.
    current_sf_occupied: Optional[int] = None


@router.patch("/{company_id}/sf-occupied", response_model=CompanyOut)
def update_sf_occupied(
    company_id: str,
    payload: SfOccupiedUpdate,
    db: Session = Depends(get_db),
):
    """Set the company's real occupied SF (CoStar "SF Occupied"), the single SF
    field. Never calculated. Re-runs signals so sf_per_head / utilization update."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if payload.current_sf_occupied is not None and payload.current_sf_occupied < 0:
        raise HTTPException(status_code=422, detail="current_sf_occupied must be >= 0")

    company.current_sf_occupied   = payload.current_sf_occupied
    company.last_modified_by_user = datetime.utcnow()
    _run_signals(company)
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


class RentFieldsUpdate(BaseModel):
    # All nullable: clearing a field back to unknown is a valid edit.
    effective_rent_psf: Optional[float] = None
    starting_rent_psf: Optional[float] = None
    building_asking_rent_psf: Optional[float] = None
    lease_signed_year: Optional[int] = None


@router.patch("/{company_id}/rents", response_model=CompanyOut)
def update_rent_fields(
    company_id: str,
    payload: RentFieldsUpdate,
    db: Session = Depends(get_db),
):
    """Set or clear the tenant's rent economics: effective_rent_psf (actual
    effective rent $/SF/yr), starting_rent_psf (rent the lease started at
    $/SF/yr), building_asking_rent_psf (asking rent quoted at their building
    $/SF/yr), and lease_signed_year (year the current lease was signed).
    Drives the rent-gap ladder in tenant outreach."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    for field_name, value in (
        ("effective_rent_psf", payload.effective_rent_psf),
        ("starting_rent_psf", payload.starting_rent_psf),
        ("building_asking_rent_psf", payload.building_asking_rent_psf),
    ):
        if value is not None and value < 0:
            raise HTTPException(status_code=422, detail=f"{field_name} must be >= 0")
    if payload.lease_signed_year is not None and not (1900 <= payload.lease_signed_year <= 2100):
        raise HTTPException(status_code=422, detail="lease_signed_year must be a 4-digit year (1900-2100)")
    company.effective_rent_psf       = payload.effective_rent_psf
    company.starting_rent_psf        = payload.starting_rent_psf
    company.building_asking_rent_psf = payload.building_asking_rent_psf
    company.lease_signed_year        = payload.lease_signed_year
    company.last_modified_by_user    = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


class MedicalUpdate(BaseModel):
    is_medical: bool


@router.patch("/{company_id}/medical", response_model=CompanyOut)
def update_medical(
    company_id: str,
    payload: MedicalUpdate,
    db: Session = Depends(get_db),
):
    """Set or clear the Medical Tenant flag for a company."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.is_medical = payload.is_medical
    company.last_modified_by_user = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _company_out(company, db)


class CompanyTimelineEntry(BaseModel):
    id: int
    log_date: date
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    action_type: str
    action_taken: str
    outcome: Optional[str] = None
    notes: Optional[str] = None
    direction: Optional[str] = "outbound"
    channel: Optional[str] = "other"
    outreach_type: Optional[str] = None
    subject: Optional[str] = None

    class Config:
        from_attributes = True


class CompanyTimelinePage(BaseModel):
    total: int
    limit: int
    offset: int
    company_id: int
    company_name: str
    has_data_conflict: bool = False
    entries: List[CompanyTimelineEntry]


@router.get("/{company_id}/timeline", response_model=CompanyTimelinePage)
def company_timeline(
    company_id: str,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Every entry stamped to this company, interleaved by date across all
    contacts — including entries with a null contact_id (a voicemail to a main
    line, a note on the account).

    Reads company_stamp_id, not the contact's current employer: that is what
    keeps a departed contact's history on the old company's page.

    Accepts either the CO-nnn business key or the integer primary key, since
    both are in circulation. Paginated, no hard ceiling, and an empty timeline
    returns an empty page rather than a 500.
    """
    from sqlalchemy.orm import joinedload as _joinedload
    from app.models.activity import ActivityLog
    from app.models.contact import Contact

    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company and str(company_id).isdigit():
        company = db.query(Company).filter(Company.id == int(company_id)).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    from app.services.contact_service import STAGE_CHANGE_ACTION

    # Stage-change dividers belong to a person's thread, not to a company's
    # conversation history. They are never stamped, so this is belt and braces
    # for any database still carrying pre-cleanup rows.
    base = db.query(ActivityLog).filter(
        ActivityLog.company_stamp_id == company.id,
        ActivityLog.action_type != STAGE_CHANGE_ACTION,
    )
    total = base.count()
    rows = (
        base.options(_joinedload(ActivityLog.contact))
        .order_by(ActivityLog.log_date.desc(), ActivityLog.id.desc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )

    entries = []
    for log in rows:
        item = CompanyTimelineEntry.model_validate(log)
        # Null-safe: an unattached entry keeps a null contact_name, it does not
        # fall out of the timeline.
        item.contact_name = log.contact.name if log.contact is not None else None
        entries.append(item)

    return CompanyTimelinePage(
        total=total,
        limit=limit,
        offset=offset,
        company_id=company.id,
        company_name=company.name,
        has_data_conflict=bool(company.has_data_conflict),
        entries=entries,
    )


@router.post("/refresh-signals", response_model=dict)
def refresh_all_signals(db: Session = Depends(get_db)):
    companies = db.query(Company).all()
    for c in companies:
        _run_signals(c)
    db.commit()
    return {"refreshed": len(companies)}


@router.post("/{company_id}/draft-outreach")
def draft_outreach(company_id: str, db: Session = Depends(get_db)):
    """
    Generate a GPT-4o outreach draft for a company.
    Does NOT persist to the database — call /log-outreach to save.
    Requires OPENAI_API_KEY in the environment.
    """
    from app.services.outreach_service import generate_outreach

    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Fix 1: block outreach generation until real occupied SF is on record. SF is
    # never calculated, so an unknown figure means we cannot responsibly draft.
    if not company.current_sf_occupied:
        raise HTTPException(
            status_code=422,
            detail=(
                "SF: Unknown — set 'SF Occupied (CoStar)' on this company before "
                "generating outreach."
            ),
        )

    company_dict = {
        "name":                 company.name,
        "industry":             company.industry,
        "current_headcount":    company.current_headcount,
        "headcount_growth_pct": company.headcount_growth_pct,
        "current_sf_occupied":  company.current_sf_occupied,
        "current_building_class": company.current_building_class,
        "current_submarket":    company.current_submarket,
        "lease_expiry_months":  company.lease_expiry_months,
        "lease_expiry_date":    str(company.lease_expiry_date) if company.lease_expiry_date else None,
        "primary_contact_name": company.primary_contact_name,
        "primary_contact_title":company.primary_contact_title,
        "tenant_representative":company.tenant_representative,
        "current_rent_psf":     company.current_rent_psf,
        "effective_rent_psf":   company.effective_rent_psf,
        "starting_rent_psf":    company.starting_rent_psf,
        "building_asking_rent_psf": company.building_asking_rent_psf,
        "lease_signed_year":    company.lease_signed_year,
        "future_move_flag":     company.future_move_flag,
        "future_move_type":     company.future_move_type,
        "lease_trajectory":     company.lease_trajectory,
        "contraction_signal":   company.contraction_signal,
        "opportunity_score":    company.opportunity_score,
        "priority":             company.priority,
    }

    try:
        result = generate_outreach(company_dict)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    cs = result.get("call_script", {})
    email = result.get("email", {})

    return {
        "email_subject": email.get("subject", ""),
        "email_body":    email.get("body", ""),
        "call_script": {
            "opening":     cs.get("opening", ""),
            "data":        cs.get("data", ""),
            "angle":       cs.get("angle", ""),
            "core_message":cs.get("core_message", ""),
            "pain_probe":  cs.get("pain_probe", ""),
            "the_close":   cs.get("the_close", ""),
        },
        "projected_sf":  result.get("projected_sf"),
        "score":         company.opportunity_score,
        "priority":      company.priority,
        "generated_at":  datetime.utcnow().isoformat(),
    }


@router.post("/{company_id}/log-outreach", response_model=OutreachLogOut)
def log_outreach(
    company_id: str,
    payload: OutreachLogCreate,
    db: Session = Depends(get_db),
):
    """Persist a generated outreach package to the outreach_log table."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    log = OutreachLog(
        company_id             = company.id,
        email_subject          = payload.email_subject,
        email_body             = payload.email_body,
        call_script_opening    = payload.call_script_opening,
        call_script_hook       = payload.call_script_hook,
        call_script_data       = payload.call_script_data,
        call_script_core       = payload.call_script_core,
        call_script_pain_probe = payload.call_script_pain_probe,
        call_script_close      = payload.call_script_close,
        projected_sf           = payload.projected_sf,
        score_at_generation    = payload.score_at_generation,
        priority_at_generation = payload.priority_at_generation,
        email_sent             = payload.email_sent,
        call_made              = payload.call_made,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/{company_id}/outreach-history", response_model=List[OutreachLogOut])
def outreach_history(company_id: str, db: Session = Depends(get_db)):
    """Return all outreach log entries for a company, newest first."""
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    logs = (
        db.query(OutreachLog)
        .filter(OutreachLog.company_id == company.id)
        .order_by(OutreachLog.generated_at.desc())
        .all()
    )
    return logs
