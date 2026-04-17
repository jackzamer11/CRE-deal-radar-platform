import io
import csv as _csv
from typing import List, Optional
from datetime import datetime, date

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.property import Property
from app.schemas.property import PropertyOut, PropertyListOut, PropertyCreate, SignalBreakdown
from app.services import signal_engine as se
from app.services.scoring_model import score_property
from app.config import settings

CURRENT_YEAR = 2026

# ── Bulk upload constants ──────────────────────────────────────────────────

VALID_SUBMARKETS = {
    "Arlington (Clarendon)", "Arlington (Rosslyn)", "Arlington (Ballston)",
    "Arlington (Columbia Pike)", "Alexandria (Old Town)",
    "Tysons", "Reston", "Falls Church",
}

ASSET_CLASS_MAP = {
    "a": "Class A", "class a": "Class A",
    "b": "Class B", "class b": "Class B",
    "c": "Class C", "class c": "Class C",
}

OWNER_TYPE_MAP = {
    "llc": "LLC", "limited liability company": "LLC",
    "corp": "Corporation", "corporation": "Corporation", "inc": "Corporation",
    "lp": "LP", "limited partnership": "LP",
    "individual": "Individual", "person": "Individual",
}

TEMPLATE_HEADERS = [
    "Street Address", "Submarket", "Asset Class", "Total SF", "Year Built",
    "Last Renovation Year", "Owner Name", "Owner Type", "Owner Phone", "Owner Email",
    "Year Acquired", "Acquisition Price", "Loan Maturity Year", "In-Place Rent",
    "Current Occupancy", "Asking Price", "SF Expiring 12mo", "SF Expiring 24mo",
    "Last New Lease Signed", "Listed For Sale", "Intel Notes",
]

TEMPLATE_EXAMPLE = [
    "1234 Wilson Blvd Suite 200", "Arlington (Clarendon)", "Class B", "8500", "1998",
    "2015", "Clarendon LLC", "LLC", "703-555-0100", "owner@example.com",
    "2018", "2850000", "2028", "28.50",
    "87", "", "1200", "3000", "2022", "No", "Corner unit; strong natural light",
]

# CSV header (stripped, lowercase) → PropertyManualCreate field name
_COL = {
    "street address":       "address",
    "submarket":            "submarket",
    "asset class":          "asset_class",
    "total sf":             "total_sf",
    "year built":           "year_built",
    "last renovation year": "last_renovation_year",
    "owner name":           "owner_name",
    "owner type":           "owner_type",
    "owner phone":          "owner_phone",
    "owner email":          "owner_email",
    "year acquired":        "acquisition_year",
    "acquisition price":    "acquisition_price",
    "loan maturity year":   "estimated_loan_maturity_year",
    "in-place rent":        "in_place_rent_psf",
    "current occupancy":    "occupancy_pct",
    "asking price":         "asking_price",
    "sf expiring 12mo":     "sf_expiring_12mo",
    "sf expiring 24mo":     "sf_expiring_24mo",
    "last new lease signed": "last_lease_signed_year",
    "listed for sale":      "is_listed",
    "intel notes":          "notes",
}

_REQUIRED_INTERNAL = {
    "address", "submarket", "total_sf", "year_built",
    "owner_name", "in_place_rent_psf", "occupancy_pct",
}


class PropertyManualCreate(BaseModel):
    address: str
    submarket: str
    asset_class: str = "Class B"
    total_sf: int
    year_built: int
    last_renovation_year: Optional[int] = None
    owner_name: str
    owner_type: str = "LLC"
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None
    acquisition_year: Optional[int] = None
    acquisition_price: Optional[float] = None
    in_place_rent_psf: float
    occupancy_pct: float
    sf_expiring_12mo: float = 0.0
    sf_expiring_24mo: float = 0.0
    last_lease_signed_year: Optional[int] = None
    is_listed: bool = False
    asking_price: Optional[float] = None
    days_on_market: Optional[int] = None
    estimated_loan_maturity_year: Optional[int] = None
    notes: Optional[str] = None


router = APIRouter(prefix="/properties", tags=["properties"])


# ── Shared helpers ─────────────────────────────────────────────────────────

def _run_signals(prop: Property) -> None:
    pred_result = se.compute_prediction_score(
        prop.lease_rollover_pct, prop.vacancy_pct, prop.vacancy_12mo_ago,
        prop.years_owned or 0, prop.years_since_last_lease or 0,
        prop.year_built, prop.last_renovation_year,
    )
    owner_result = se.compute_owner_behavior_score(
        prop.years_owned or 0, prop.vacancy_pct, prop.vacancy_12mo_ago,
        prop.in_place_rent_psf, prop.market_rent_psf,
        prop.year_built, prop.last_renovation_year,
        prop.estimated_loan_maturity_year,
    )
    misp_result = se.compute_mispricing_score(
        prop.in_place_rent_psf, prop.market_rent_psf, prop.asking_price_psf,
        settings.submarket_avg_psf.get(prop.submarket, 250),
        prop.days_on_market, prop.submarket_avg_dom,
        prop.cap_rate, prop.market_cap_rate, prop.is_listed,
    )
    pred_comp  = pred_result["composite"]
    owner_comp = owner_result["composite"]
    misp_comp  = misp_result["composite"]
    scored     = score_property(pred_comp, owner_comp, misp_comp, 0, prop.is_listed)

    pb, ob, mb = pred_result["breakdown"], owner_result["breakdown"], misp_result["breakdown"]
    prop.sig_lease_rollover          = pb["lease_rollover"]
    prop.sig_vacancy_trend           = pb["vacancy_trend"]
    prop.sig_ownership_duration      = pb["ownership_duration"]
    prop.sig_leasing_drought         = pb["leasing_drought"]
    prop.sig_capex_gap               = pb["capex_gap"]
    prop.sig_hold_period             = ob["hold_period"]
    prop.sig_occupancy_decline       = ob["occupancy_decline"]
    prop.sig_rent_stagnation         = ob["rent_stagnation"]
    prop.sig_reinvestment_inactivity = ob["reinvestment_inactivity"]
    prop.sig_debt_pressure           = ob["debt_pressure"]
    prop.sig_rent_gap                = mb["rent_gap"]
    prop.sig_price_psf               = mb["price_psf"]
    prop.sig_dom_premium             = mb["dom_premium"]
    prop.sig_cap_rate_spread         = mb["cap_rate_spread"]
    prop.prediction_score     = pred_comp
    prop.owner_behavior_score = owner_comp
    prop.mispricing_score     = misp_comp
    prop.signal_score         = scored["score"]
    prop.priority             = scored["priority"]
    prop.deal_type            = scored["deal_type"]
    prop.last_signal_run      = datetime.utcnow()


def _enrich(prop: Property) -> PropertyOut:
    breakdown = SignalBreakdown(
        lease_rollover=prop.sig_lease_rollover, vacancy_trend=prop.sig_vacancy_trend,
        ownership_duration=prop.sig_ownership_duration, leasing_drought=prop.sig_leasing_drought,
        capex_gap=prop.sig_capex_gap, hold_period=prop.sig_hold_period,
        occupancy_decline=prop.sig_occupancy_decline, rent_stagnation=prop.sig_rent_stagnation,
        reinvestment_inactivity=prop.sig_reinvestment_inactivity, debt_pressure=prop.sig_debt_pressure,
        rent_gap=prop.sig_rent_gap, price_psf=prop.sig_price_psf,
        dom_premium=prop.sig_dom_premium, cap_rate_spread=prop.sig_cap_rate_spread,
    )
    out = PropertyOut.model_validate(prop)
    out.signal_breakdown = breakdown
    return out


def _next_property_id(db: Session) -> str:
    existing_ids = [p.property_id for p in db.query(Property.property_id).all()]
    nums = []
    for pid in existing_ids:
        try:
            nums.append(int(pid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    return f"NVA-{(max(nums) + 1) if nums else 1:03d}"


def _build_property(payload: PropertyManualCreate, property_id: str) -> Property:
    """Construct a Property ORM object from a validated payload."""
    vacancy_pct  = round(100.0 - payload.occupancy_pct, 2)
    leased_sf    = payload.total_sf * (payload.occupancy_pct / 100.0)
    vacant_sf    = payload.total_sf * (vacancy_pct / 100.0)
    rollover_pct = round(payload.sf_expiring_12mo / payload.total_sf * 100, 2) if payload.total_sf else 0.0

    market_rent = settings.submarket_market_rent.get(payload.submarket, 26.0)
    market_cap  = settings.submarket_cap_rate.get(payload.submarket, 6.5)
    avg_dom     = settings.submarket_avg_dom.get(payload.submarket, 120)

    acq_date    = date(payload.acquisition_year, 1, 1) if payload.acquisition_year else None
    years_owned = round((date.today() - acq_date).days / 365.25, 1) if acq_date else 0.0

    if payload.last_lease_signed_year:
        years_since = round(CURRENT_YEAR - payload.last_lease_signed_year, 1)
        last_lease  = date(payload.last_lease_signed_year, 6, 1)
    else:
        years_since = 0.0
        last_lease  = None

    asking_psf = (
        round(payload.asking_price / payload.total_sf, 2)
        if payload.asking_price and payload.total_sf else None
    )
    cap_rate = None
    if payload.asking_price and payload.in_place_rent_psf and leased_sf:
        cap_rate = round(payload.in_place_rent_psf * leased_sf * 0.55 / payload.asking_price * 100, 2)

    return Property(
        property_id=property_id, address=payload.address, submarket=payload.submarket,
        asset_class=payload.asset_class, total_sf=payload.total_sf,
        year_built=payload.year_built, last_renovation_year=payload.last_renovation_year,
        owner_name=payload.owner_name, owner_type=payload.owner_type,
        owner_phone=payload.owner_phone, owner_email=payload.owner_email,
        acquisition_date=acq_date, acquisition_price=payload.acquisition_price,
        years_owned=years_owned, asking_price=payload.asking_price,
        asking_price_psf=asking_psf, in_place_rent_psf=payload.in_place_rent_psf,
        market_rent_psf=market_rent, market_cap_rate=market_cap,
        cap_rate=cap_rate, occupancy_pct=payload.occupancy_pct,
        vacancy_pct=vacancy_pct, leased_sf=leased_sf, vacant_sf=vacant_sf,
        sf_expiring_12mo=payload.sf_expiring_12mo, sf_expiring_24mo=payload.sf_expiring_24mo,
        lease_rollover_pct=rollover_pct, last_lease_signed_date=last_lease,
        years_since_last_lease=years_since, is_listed=payload.is_listed,
        days_on_market=payload.days_on_market, submarket_avg_dom=avg_dom,
        estimated_loan_maturity_year=payload.estimated_loan_maturity_year,
        notes=payload.notes,
    )


# ── Bulk upload helpers ────────────────────────────────────────────────────

def _str_val(row: dict, field: str) -> Optional[str]:
    """Return stripped string or None for empty / NaN values."""
    v = row.get(field)
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None


def _float_val(row: dict, field: str) -> Optional[float]:
    raw = _str_val(row, field)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _int_val(row: dict, field: str) -> Optional[int]:
    f = _float_val(row, field)
    return int(f) if f is not None else None


def _bool_val(row: dict, field: str) -> Optional[bool]:
    raw = _str_val(row, field)
    if raw is None:
        return None
    return raw.lower() in ("yes", "true", "1", "y")


def _parse_row(row: dict, row_num: int) -> tuple:
    """
    Validate and parse one CSV/XLSX row.
    Returns (PropertyManualCreate, None) on success or (None, error_dict) on failure.
    """
    err = {"row": row_num, "address": _str_val(row, "address") or "—"}

    # ── Required fields ───────────────────────────────────────────────────
    address = _str_val(row, "address")
    if not address:
        return None, {**err, "reason": "Missing required field: Street Address"}

    err["address"] = address

    submarket = _str_val(row, "submarket")
    if not submarket:
        return None, {**err, "reason": "Missing required field: Submarket"}
    if submarket not in VALID_SUBMARKETS:
        return None, {**err, "reason": f"Submarket '{submarket}' not in allowed list"}

    total_sf = _int_val(row, "total_sf")
    if not total_sf:
        return None, {**err, "reason": "Missing required field: Total SF"}

    year_built = _int_val(row, "year_built")
    if not year_built:
        return None, {**err, "reason": "Missing required field: Year Built"}

    owner_name = _str_val(row, "owner_name")
    if not owner_name:
        return None, {**err, "reason": "Missing required field: Owner Name"}

    in_place_rent = _float_val(row, "in_place_rent_psf")
    if in_place_rent is None:
        return None, {**err, "reason": "Missing required field: In-Place Rent"}

    occupancy = _float_val(row, "occupancy_pct")
    if occupancy is None:
        return None, {**err, "reason": "Missing required field: Current Occupancy"}

    # ── Asset Class normalization ─────────────────────────────────────────
    raw_ac = _str_val(row, "asset_class")
    if raw_ac is not None:
        asset_class = ASSET_CLASS_MAP.get(raw_ac.lower())
        if asset_class is None:
            return None, {**err, "reason": f"Asset Class '{raw_ac}' not recognised (use Class A / B / C)"}
    else:
        asset_class = "Class B"

    # ── Owner Type normalization ──────────────────────────────────────────
    raw_ot = _str_val(row, "owner_type")
    if raw_ot is not None:
        owner_type = OWNER_TYPE_MAP.get(raw_ot.lower(), raw_ot)  # store as-is if unknown
    else:
        owner_type = "LLC"

    return PropertyManualCreate(
        address=address,
        submarket=submarket,
        asset_class=asset_class,
        total_sf=total_sf,
        year_built=year_built,
        last_renovation_year=_int_val(row, "last_renovation_year"),
        owner_name=owner_name,
        owner_type=owner_type,
        owner_phone=_str_val(row, "owner_phone"),
        owner_email=_str_val(row, "owner_email"),
        acquisition_year=_int_val(row, "acquisition_year"),
        acquisition_price=_float_val(row, "acquisition_price"),
        in_place_rent_psf=in_place_rent,
        occupancy_pct=occupancy,
        sf_expiring_12mo=_float_val(row, "sf_expiring_12mo") or 0.0,
        sf_expiring_24mo=_float_val(row, "sf_expiring_24mo") or 0.0,
        last_lease_signed_year=_int_val(row, "last_lease_signed_year"),
        is_listed=_bool_val(row, "is_listed") or False,
        asking_price=_float_val(row, "asking_price"),
        estimated_loan_maturity_year=_int_val(row, "estimated_loan_maturity_year"),
        notes=_str_val(row, "notes"),
    ), None


def _apply_update(prop: Property, row: dict) -> None:
    """
    Apply non-empty CSV values to an existing property, then recompute
    all derived fields and re-run signals so scores stay consistent.
    Only fields present and non-empty in the CSV are touched.
    """
    def sv(f): return _str_val(row, f)
    def fv(f): return _float_val(row, f)
    def iv(f): return _int_val(row, f)
    def bv(f): return _bool_val(row, f)

    if sv("owner_name"):       prop.owner_name = sv("owner_name")
    if sv("owner_phone"):      prop.owner_phone = sv("owner_phone")
    if sv("owner_email"):      prop.owner_email = sv("owner_email")
    if sv("notes"):            prop.notes = sv("notes")
    if fv("asking_price"):     prop.asking_price = fv("asking_price")
    if fv("acquisition_price"): prop.acquisition_price = fv("acquisition_price")
    if iv("last_renovation_year"): prop.last_renovation_year = iv("last_renovation_year")
    if iv("estimated_loan_maturity_year"): prop.estimated_loan_maturity_year = iv("estimated_loan_maturity_year")
    if bv("is_listed") is not None: prop.is_listed = bv("is_listed")

    # Fields that require derived recomputation
    raw_ot = sv("owner_type")
    if raw_ot:
        prop.owner_type = OWNER_TYPE_MAP.get(raw_ot.lower(), raw_ot)

    if fv("in_place_rent_psf"): prop.in_place_rent_psf = fv("in_place_rent_psf")
    if fv("occupancy_pct"):
        prop.occupancy_pct = fv("occupancy_pct")
    if iv("total_sf"):
        prop.total_sf = iv("total_sf")
    if fv("sf_expiring_12mo") is not None: prop.sf_expiring_12mo = fv("sf_expiring_12mo")
    if fv("sf_expiring_24mo") is not None: prop.sf_expiring_24mo = fv("sf_expiring_24mo")
    if iv("acquisition_year"):
        prop.acquisition_date = date(iv("acquisition_year"), 1, 1)
    if iv("last_lease_signed_year"):
        prop.last_lease_signed_date = date(iv("last_lease_signed_year"), 6, 1)
        prop.years_since_last_lease = round(CURRENT_YEAR - iv("last_lease_signed_year"), 1)

    # Recompute derived fields from current prop state
    prop.vacancy_pct      = round(100.0 - prop.occupancy_pct, 2)
    prop.leased_sf        = prop.total_sf * (prop.occupancy_pct / 100.0)
    prop.vacant_sf        = prop.total_sf * (prop.vacancy_pct / 100.0)
    prop.lease_rollover_pct = (
        round(prop.sf_expiring_12mo / prop.total_sf * 100, 2) if prop.total_sf else 0.0
    )
    if prop.acquisition_date:
        prop.years_owned  = round((date.today() - prop.acquisition_date).days / 365.25, 1)
    if prop.asking_price and prop.total_sf:
        prop.asking_price_psf = round(prop.asking_price / prop.total_sf, 2)
    if prop.asking_price and prop.in_place_rent_psf and prop.leased_sf:
        prop.cap_rate = round(
            prop.in_place_rent_psf * prop.leased_sf * 0.55 / prop.asking_price * 100, 2
        )


# ── Routes — fixed paths first, then parameterised ────────────────────────

@router.get("/", response_model=List[PropertyListOut])
def list_properties(
    submarket: Optional[str] = None,
    priority: Optional[str] = None,
    is_listed: Optional[bool] = None,
    min_score: Optional[float] = None,
    sort_by: str = Query("signal_score", pattern="^(signal_score|prediction_score|vacancy_pct|years_owned)$"),
    db: Session = Depends(get_db),
):
    q = db.query(Property)
    if submarket:   q = q.filter(Property.submarket == submarket)
    if priority:    q = q.filter(Property.priority == priority)
    if is_listed is not None: q = q.filter(Property.is_listed == is_listed)
    if min_score is not None: q = q.filter(Property.signal_score >= min_score)
    col = getattr(Property, sort_by, Property.signal_score)
    return q.order_by(col.desc()).all()


@router.post("/", response_model=PropertyOut)
def create_property(payload: PropertyManualCreate, db: Session = Depends(get_db)):
    """Manually add a single property. Signals are computed immediately."""
    prop = _build_property(payload, _next_property_id(db))
    db.add(prop)
    db.flush()
    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    return _enrich(prop)


# NOTE: /bulk-template and /bulk-upload must be registered before /{property_id}
# so FastAPI does not swallow them as a property_id path parameter.

@router.get("/bulk-template")
def download_bulk_template():
    """Return a CSV file with the correct column headers and one example row."""
    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(TEMPLATE_EXAMPLE)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=property_upload_template.csv"},
    )


@router.post("/bulk-upload")
async def bulk_upload_properties(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a .csv or .xlsx file to insert/update properties in bulk.

    Deduplication key: Street Address (case-insensitive, whitespace-trimmed).
    - Match found  → update only non-empty CSV fields, re-run signals.
    - No match     → insert as new property via standard create logic.
    - Validation fail → skipped with reason.

    Returns: { inserted, updated, skipped, errors: [{row, address, reason}] }
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

    # Normalise column names → internal field names
    df.columns = [_COL.get(c.strip().lower(), c.strip()) for c in df.columns]

    # Check required columns are present
    missing_cols = _REQUIRED_INTERNAL - set(df.columns)
    if missing_cols:
        internal_to_display = {v: k.title() for k, v in _COL.items()}
        labels = sorted(internal_to_display.get(m, m) for m in missing_cols)
        raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(labels)}")

    # Replace empty strings with None so _str_val / _float_val behave correctly
    df = df.replace("", None)

    # Build dedupe index from existing properties
    existing: dict = {
        p.address.strip().lower(): p
        for p in db.query(Property).all()
    }

    inserted = updated = 0
    errors: list = []

    for idx, raw_row in enumerate(df.to_dict(orient="records"), start=2):  # row 1 = header
        payload, err = _parse_row(raw_row, row_num=idx)
        if err:
            errors.append(err)
            continue

        dedupe_key = payload.address.strip().lower()

        if dedupe_key in existing:
            # UPDATE path — only touch non-empty CSV fields
            prop = existing[dedupe_key]
            _apply_update(prop, raw_row)
            _run_signals(prop)
            updated += 1
        else:
            # INSERT path — full create with auto-generated ID and signals
            prop = _build_property(payload, _next_property_id(db))
            db.add(prop)
            db.flush()
            _run_signals(prop)
            existing[dedupe_key] = prop   # prevent duplicate inserts within same upload
            inserted += 1

    db.commit()

    return {
        "inserted": inserted,
        "updated":  updated,
        "skipped":  len(errors),
        "errors":   errors,
    }


@router.post("/refresh-signals", response_model=dict)
def refresh_all_signals(db: Session = Depends(get_db)):
    """Re-run signal engine on all properties."""
    props = db.query(Property).all()
    for prop in props:
        _run_signals(prop)
    db.commit()
    return {"refreshed": len(props), "timestamp": str(datetime.utcnow())}


@router.get("/{property_id}", response_model=PropertyOut)
def get_property(property_id: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return _enrich(prop)


@router.post("/{property_id}/refresh-signals", response_model=PropertyOut)
def refresh_property_signals(property_id: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    return _enrich(prop)
