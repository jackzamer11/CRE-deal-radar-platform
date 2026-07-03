import io
import csv as _csv
from typing import List, Optional
from datetime import datetime, date

import pandas as pd
from dateutil import parser as _dateparser
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.property import Property
from app.models.company import Company
from app.models.outreach_log import OutreachLog
from app.schemas.property import PropertyOut, PropertyListOut, PropertyCreate, SignalBreakdown, InPlaceRentUpdate, MatchedTenant
from app.schemas.outreach import OutreachLogCreate, OutreachLogOut, OutreachDraft, CallScript
from app.services import signal_engine as se
from app.services.scoring_model import score_property
from app.services.match_scoring import medical_mismatch_penalty
from app.services.property_outreach_service import generate_property_outreach
from app.config import settings

CURRENT_YEAR = 2026

# ── Bulk upload constants ──────────────────────────────────────────────────

VALID_SUBMARKETS = {
    "Arlington (Clarendon)", "Arlington (Rosslyn)", "Arlington (Ballston)",
    "Arlington (Columbia Pike)", "Alexandria (Old Town)",
    "Tysons", "Reston", "Falls Church",
    "McLean", "Vienna", "Fairfax City",
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
    "listed for sale":      "listed_for_sale",
    "intel notes":          "notes",
}

_REQUIRED_INTERNAL = {
    "address", "submarket", "total_sf", "year_built",
    "owner_name", "in_place_rent_psf", "occupancy_pct",
}

# ── CoStar import constants ────────────────────────────────────────────────

# Key = CoStar submarket name (lowercase, stripped).
# Value = platform submarket string, or None for ambiguous (skip with reason).
COSTAR_SUBMARKET_MAP: dict = {
    "old town alexandria":       "Alexandria (Old Town)",
    "alexandria/old town":       "Alexandria (Old Town)",
    "falls church":              "Falls Church",
    "reston":                    "Reston",
    "herndon":                   "Reston",
    "tysons":                    "Tysons",
    "tysons corner":             "Tysons",
    "clarendon":                 "Arlington (Clarendon)",
    "rosslyn":                   "Arlington (Rosslyn)",
    "rosslyn/ballston":          None,   # ambiguous — specific error message
    "ballston":                  "Arlington (Ballston)",
    "columbia pike":             "Arlington (Columbia Pike)",
    "mclean":                    "McLean",
    "vienna":                    "Vienna",
    "tysons/vienna":             "Vienna",
    "fairfax city":              "Fairfax City",
    "fairfax":                   "Fairfax City",
    "springfield/burke":         "Springfield",
    "route 28 corridor north":   "Dulles Corridor",
    "route 28 corridor south":   "Dulles Corridor",
    # ── Additional CoStar submarkets (12) — case-insensitive lookup ──────────
    "annandale":                 "Annandale",
    "clarendon/courthouse":      "Arlington (Clarendon)",
    "crystal city":              "Crystal City",
    "del ray":                   "Alexandria (Old Town)",
    "i-395 corridor":            "Springfield",
    "merrifield":                "Merrifield",
    "n arlington/e fallschurch": "Falls Church",
    "newington":                 "Springfield",
    "oakton":                    "Vienna",
    "oakton/vienna":             "Vienna",
    "centreville":               "Centreville",
    "springfield":               "Springfield",
    "tysons corner/mclean":      "Tysons",
}

COSTAR_CLASS_MAP: dict = {
    "a": "Class A",
    "b": "Class B",
    "c": "Class C",
}

# Exact CoStar export column names (order matches CoStar default export).
# Used to validate the file has the right headers.
COSTAR_REQUIRED_COLS = [
    "Property Address", "Building Class", "RBA", "Submarket Name",
    "City", "State", "Zip", "Year Built", "Year Renovated",
    "Last Sale Date", "Last Sale Price", "Origination Amount",
    "Origination Date", "Maturity Date", "Percent Leased",
    "Rent/SF/Yr", "Building Status", "For Sale Status",
    "For Sale Price", "True Owner Contact", "True Owner Name",
    "True Owner Phone",
]

# Optional CoStar enrichment columns (Part 2). Importer maps any that are
# present; absent columns are silently skipped (kept null).
COSTAR_OPTIONAL_COLS = [
    "Star Rating", "Total Available Space (SF)", "SF Avail",
    "Landlord Representative", "Landlord Rep Contact",
    "Sales Company", "Sales Contact",
    "Price/SF", "Tenancy", "Stories", "Parking Ratio",
]

# Fields on Property that are protected from CoStar overwrite once a user has
# manually edited them. Mirrors the tenant-side PROTECTED_LEASE_SOURCES guard.
PROTECTED_RENT_SOURCES = frozenset(
    {"manual", "compstak", "sec_filing", "landlord_confirmed", "public_record"}
)


def _normalize_tenancy(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    r = raw.strip().lower()
    if r.startswith("multi") or r in ("multiple", "multi-tenant", "multi tenant"):
        return "multi"
    if r.startswith("single") or r in ("single tenant", "single-tenant"):
        return "single"
    return None


def _costar_str(row: dict, col: str) -> Optional[str]:
    v = row.get(col)
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None


def _costar_float(row: dict, col: str) -> Optional[float]:
    raw = _costar_str(row, col)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def _costar_int(row: dict, col: str) -> Optional[int]:
    f = _costar_float(row, col)
    return int(f) if f is not None else None


def _extract_year(raw: Optional[str]) -> Optional[int]:
    """Parse a date string in any common format and return the year."""
    if not raw:
        return None
    try:
        return _dateparser.parse(raw, fuzzy=True).year
    except (ValueError, OverflowError, TypeError):
        return None


def _costar_bool(row: dict, col: str) -> bool:
    raw = _costar_str(row, col)
    if not raw:
        return False
    return raw.lower() in ("y", "yes", "true", "1")


def _parse_costar_row(row: dict, row_num: int) -> tuple:
    """
    Validate and parse one post-filter CoStar row.
    Returns (PropertyManualCreate, None) or (None, error_dict).
    Caller has already applied state / submarket / status filters.
    """
    err = {"row": row_num, "address": _costar_str(row, "Property Address") or "—"}

    address = _costar_str(row, "Property Address")
    if not address:
        return None, {**err, "reason": "Missing Property Address"}

    err["address"] = address

    # Submarket (already filtered, but re-resolve to platform name)
    cs_sub = _costar_str(row, "Submarket Name") or ""
    submarket = COSTAR_SUBMARKET_MAP.get(cs_sub.lower())
    if not submarket:
        return None, {**err, "reason": f"Submarket '{cs_sub}' could not be resolved"}

    # Building Class
    raw_class = _costar_str(row, "Building Class") or ""
    asset_class = COSTAR_CLASS_MAP.get(raw_class.strip().lower())
    if not asset_class:
        return None, {**err, "reason": f"Building Class '{raw_class}' not recognised (expected A, B, or C)"}

    # RBA (total_sf)
    total_sf = _costar_int(row, "RBA")
    if not total_sf:
        return None, {**err, "reason": "Missing or zero RBA"}

    # Year Built
    year_built = _costar_int(row, "Year Built")
    if not year_built:
        return None, {**err, "reason": "Missing Year Built"}

    # Occupancy (optional — abstains from vacancy signals when blank)
    occupancy = _costar_float(row, "Percent Leased")
    if occupancy is not None and 0.0 < occupancy <= 1.0:
        occupancy = round(occupancy * 100, 2)

    # Owner name: "Contact, Entity" or just "Entity" or "Unknown"
    contact    = _costar_str(row, "True Owner Contact")
    owner_raw  = _costar_str(row, "True Owner Name")
    if contact and owner_raw:
        owner_name = f"{contact}, {owner_raw}"
    elif owner_raw:
        owner_name = owner_raw
    elif contact:
        owner_name = contact
    else:
        owner_name = "Unknown"

    # Dates → years
    acq_year      = _extract_year(_costar_str(row, "Last Sale Date"))
    maturity_year = _extract_year(_costar_str(row, "Maturity Date"))

    # Notes: flag that in-place rent is missing
    notes = "in_place_rent_psf not imported from CoStar — update manually."

    return PropertyManualCreate(
        address=address,
        submarket=submarket,
        asset_class=asset_class,
        total_sf=total_sf,
        year_built=year_built,
        last_renovation_year=_costar_int(row, "Year Renovated"),
        owner_name=owner_name,
        owner_type="",                                    # not in CoStar export
        owner_phone=_costar_str(row, "True Owner Phone"),
        owner_email=None,
        acquisition_year=acq_year,
        acquisition_price=_costar_float(row, "Last Sale Price"),
        in_place_rent_psf=0.0,                           # asking rent ignored per spec
        occupancy_pct=occupancy,
        sf_expiring_12mo=0.0,
        sf_expiring_24mo=0.0,
        last_lease_signed_year=None,
        listed_for_sale=_costar_bool(row, "For Sale Status"),
        asking_price=_costar_float(row, "For Sale Price"),
        estimated_loan_maturity_year=maturity_year,
        notes=notes,
        # Optional enrichment columns (Part 2)
        star_rating=_costar_int(row, "Star Rating"),
        sf_avail=_costar_int(row, "Total Available Space (SF)") or _costar_int(row, "SF Avail"),
        landlord_representative=_costar_str(row, "Landlord Representative"),
        landlord_rep_contact=_costar_str(row, "Landlord Rep Contact"),
        sales_company=_costar_str(row, "Sales Company"),
        sales_contact=_costar_str(row, "Sales Contact"),
        asking_price_psf=_costar_float(row, "Price/SF"),
        tenancy=_normalize_tenancy(_costar_str(row, "Tenancy")),
        stories=_costar_int(row, "Stories"),
        parking_ratio=_costar_float(row, "Parking Ratio"),
    ), None


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
    occupancy_pct: Optional[float] = None
    sf_expiring_12mo: float = 0.0
    sf_expiring_24mo: float = 0.0
    last_lease_signed_year: Optional[int] = None
    listed_for_sale: bool = False
    asking_price: Optional[float] = None
    days_on_market: Optional[int] = None
    estimated_loan_maturity_year: Optional[int] = None
    notes: Optional[str] = None
    # CoStar enrichment (optional)
    star_rating: Optional[int] = None
    sf_avail: Optional[int] = None
    landlord_representative: Optional[str] = None
    landlord_rep_contact: Optional[str] = None
    sales_company: Optional[str] = None
    sales_contact: Optional[str] = None
    asking_price_psf: Optional[float] = None
    tenancy: Optional[str] = None
    stories: Optional[int] = None
    parking_ratio: Optional[float] = None


class SnoozeRequest(BaseModel):
    snoozed_until: date                  # must be at least tomorrow (validated on frontend)
    snooze_reason: Optional[str] = None  # free text — e.g. "Under contract — PSA signed"


class PropertyUpdate(BaseModel):
    """All fields optional — only fields present in the request body are updated."""
    address:                      Optional[str]   = None
    submarket:                    Optional[str]   = None
    asset_class:                  Optional[str]   = None
    total_sf:                     Optional[int]   = None
    year_built:                   Optional[int]   = None
    last_renovation_year:         Optional[int]   = None
    owner_name:                   Optional[str]   = None
    owner_type:                   Optional[str]   = None
    owner_phone:                  Optional[str]   = None
    owner_email:                  Optional[str]   = None
    acquisition_year:             Optional[int]   = None
    acquisition_price:            Optional[float] = None
    in_place_rent_psf:            Optional[float] = None
    occupancy_pct:                Optional[float] = None
    sf_expiring_12mo:             Optional[float] = None
    sf_expiring_24mo:             Optional[float] = None
    last_lease_signed_year:       Optional[int]   = None
    listed_for_sale:              Optional[bool]  = None
    asking_price:                 Optional[float] = None
    days_on_market:               Optional[int]   = None
    estimated_loan_maturity_year: Optional[int]   = None
    notes:                        Optional[str]   = None
    owner_confirmed_leasing:      Optional[bool]  = None  # hard trigger for tenant-match outreach
    is_medical:                   Optional[bool]  = None


class TenantOutreachResult(BaseModel):
    company_id:         str
    company_name:       str
    contact_name:       Optional[str] = None
    sf_needed:          Optional[int] = None
    lease_expiry_months: Optional[int] = None
    email_draft:        str
    call_script:        str


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
        prop.cap_rate, prop.market_cap_rate,
        listed_for_sale=bool(prop.listed_for_sale or False),
    )
    pred_comp  = pred_result["composite"]
    owner_comp = owner_result["composite"]
    misp_comp  = misp_result["composite"]
    scored     = score_property(pred_comp, owner_comp, misp_comp, 0, bool(prop.listed_for_sale or False))

    pb, ob, mb = pred_result["breakdown"], owner_result["breakdown"], misp_result["breakdown"]
    # Store sub-scores; None (abstain) persisted as 0.0
    prop.sig_lease_rollover          = pb["lease_rollover"]          or 0.0
    prop.sig_vacancy_trend           = pb["vacancy_trend"]           or 0.0
    prop.sig_ownership_duration      = pb["ownership_duration"]      or 0.0
    prop.sig_leasing_drought         = pb["leasing_drought"]         or 0.0
    prop.sig_capex_gap               = pb["capex_gap"]               or 0.0
    prop.sig_hold_period             = ob["hold_period"]             or 0.0
    prop.sig_occupancy_decline       = ob["occupancy_decline"]       or 0.0
    prop.sig_rent_stagnation         = ob["rent_stagnation"]         or 0.0
    prop.sig_reinvestment_inactivity = ob["reinvestment_inactivity"] or 0.0
    prop.sig_debt_pressure           = ob["debt_pressure"]           or 0.0
    prop.sig_rent_gap                = mb["rent_gap"]                or 0.0
    prop.sig_price_psf               = mb["price_psf"]               or 0.0
    prop.sig_dom_premium             = mb["dom_premium"]             or 0.0
    prop.sig_cap_rate_spread         = mb["cap_rate_spread"]         or 0.0
    prop.prediction_score     = pred_comp
    prop.owner_behavior_score = owner_comp
    prop.mispricing_score     = misp_comp
    prop.signal_score         = scored["score"]
    prop.priority             = scored["priority"]
    prop.deal_type            = scored["deal_type"]
    prop.last_signal_run      = datetime.utcnow()
    prop.signals_scored_count = (
        pred_result["signals_scored"] +
        owner_result["signals_scored"] +
        misp_result["signals_scored"]
    )
    prop.insufficient_data    = prop.signals_scored_count < 3

    # Property-side outreach scores (Parts 3 / 4)
    tm = se.compute_tenant_match_score(
        vacancy_pct=prop.vacancy_pct,
        sf_avail=prop.sf_avail,
        total_sf=prop.total_sf or 1,
        submarket_vacancy_avg=None,
        market_rent_psf=prop.market_rent_psf or 0.0,
        in_place_rent_psf=prop.in_place_rent_psf,
        asking_rent_psf=None,
        tenancy=prop.tenancy,
    )
    lr = se.compute_listing_rep_score(
        years_owned=prop.years_owned,
        in_place_rent_psf=prop.in_place_rent_psf,
        market_rent_psf=prop.market_rent_psf or 0.0,
        year_built=prop.year_built or 1980,
        last_renovation_year=prop.last_renovation_year,
        estimated_loan_maturity_year=prop.estimated_loan_maturity_year,
        owner_type=prop.owner_type,
        listed_for_sale=bool(prop.listed_for_sale or False),
    )
    ac = se.compute_acquisition_score(
        cap_rate=prop.cap_rate,
        market_cap_rate=prop.market_cap_rate or 6.5,
        asking_price_psf=prop.asking_price_psf,
        submarket_avg_psf=settings.submarket_avg_psf.get(prop.submarket, 250),
        sf_avail=prop.sf_avail,
        total_sf=prop.total_sf or 1,
        listed_for_sale=bool(prop.listed_for_sale or False),
        years_owned=prop.years_owned,
        star_rating=prop.star_rating,
        year_built=prop.year_built or 1980,
        last_renovation_year=prop.last_renovation_year,
    )
    # Preserve None (abstain) so dashboards can render "—" instead of 0.
    prop.tenant_match_score  = tm
    prop.listing_rep_score   = lr
    prop.acquisition_score   = ac
    prop.dominant_score_type = se.determine_dominant_score_type(tm, lr, ac)


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


# ── Matched-tenant helper ──────────────────────────────────────────────────

def _sf_needed_display(sf_needed):
    """Card label for SF needed: the real number when known, else 'Unknown'."""
    return f"{sf_needed:,} SF" if sf_needed else "Unknown"


def _compute_matched_tenants(prop: Property, db: Session) -> list:
    from app.models.company import Company
    from app.schemas.property import MatchedTenant
    from app.config import MATCH_SCORE_WEIGHTS, CLASS_NEUTRAL_POINTS, SUBMARKET_ADJACENT_POINTS
    from app.services.match_scoring import (
        compute_match, submarket_score, class_score,
        lease_expiry_chip_label, LEASE_EXPIRY_NULL_POINTS, LEASE_EXPIRY_FLOOR_COMPOSITE,
    )
    from app.services.signal_engine import sig_lease_expiry_proximity
    from app.services.opportunity_stage_service import pair_is_contacted

    # Fix 1: the SF delta filter compares against AVAILABLE SF only — never total
    # or vacant SF. If available SF is null/zero the property cannot host a sized
    # match, so suppress entirely (no tenants surface).
    avail_sf = prop.sf_avail or 0
    if avail_sf <= 0:
        return []

    # SF (= real occupied SF) may be unknown; such tenants can still match on
    # submarket / class / lease timing and are shown with "SF: Unknown"
    # (outreach for them is blocked separately until the figure is populated).
    candidates = db.query(Company).all()

    scored = []
    for co in candidates:
        sf_occupied = co.current_sf_occupied or 0
        if sf_occupied > 0:
            # Composite Match Score gates (submarket -> class -> SF). The SF hard
            # gate is bypassed when this exact pair has already been contacted
            # (contacted history is never disturbed) — it then scores SF at the
            # gate floor instead of being excluded.
            match = compute_match(
                tenant_submarket=co.current_submarket,
                property_submarket=prop.submarket,
                tenant_class=getattr(co, "current_building_class", None),
                property_class=prop.asset_class,
                sf_needed=sf_occupied,
                sf_avail=avail_sf,
                sf_gate_exempt=pair_is_contacted(db, prop.id, co.id),
                tenant_lease_expiry_months=co.lease_expiry_months,
            )
            if match is None:
                continue
            reasons = [
                f"SF fit {match['sf_fit_score']:.0f}/100 ({sf_occupied:,} occupied vs {avail_sf:,} avail)",
                (f"Adjacent submarket ({co.current_submarket})" if match["adjacent"]
                 else f"Same submarket ({prop.submarket})"),
                f"Class fit {match['class_score']:.0f}/100",
                f"Lease: {lease_expiry_chip_label(co.lease_expiry_months)}",
            ]
        else:
            # Unknown occupied SF: the pair cannot be sized, but the card is kept
            # (main behaviour) — submarket and class gates still apply; the SF
            # factor scores neutral until the figure is populated.
            sub = submarket_score(co.current_submarket, prop.submarket)
            if sub is None:
                continue
            cls = class_score(getattr(co, "current_building_class", None), prop.asset_class)
            if cls is None:
                continue
            raw_lease = sig_lease_expiry_proximity(co.lease_expiry_months)
            lease = raw_lease if raw_lease is not None else LEASE_EXPIRY_NULL_POINTS
            w = MATCH_SCORE_WEIGHTS
            composite = (
                w["lease_expiry"] * lease
                + w["submarket"] * sub
                + w["class"] * cls
                + w["sf_fit"] * CLASS_NEUTRAL_POINTS
            )
            if raw_lease == 0.0:
                composite = max(composite, LEASE_EXPIRY_FLOOR_COMPOSITE)
            match = {
                "score": round(composite, 1),
                "adjacent": sub == SUBMARKET_ADJACENT_POINTS,
                "class_score": cls,
                "sf_fit_score": None,
                "lease_expiry_score": lease,
            }
            reasons = [
                "SF unknown — sized fit pending",
                (f"Adjacent submarket ({co.current_submarket})" if match["adjacent"]
                 else f"Same submarket ({prop.submarket})"),
                f"Class fit {cls:.0f}/100",
                f"Lease: {lease_expiry_chip_label(co.lease_expiry_months)}",
            ]
        if co.expansion_signal:
            reasons.append("Expansion signal active")
        # Soft medical/non-medical mismatch penalty — match still appears.
        penalty = medical_mismatch_penalty(prop, co)
        if penalty:
            match["score"] = round(match["score"] + penalty, 1)
            reasons.append("Medical/non-medical mismatch (−20)")
        scored.append((match, co, reasons))

    scored.sort(key=lambda x: x[0]["score"], reverse=True)
    return [
        MatchedTenant(
            company_id=co.company_id,
            name=co.name,
            industry=co.industry,
            headcount=co.current_headcount,
            sf_needed=co.current_sf_occupied if co.current_sf_occupied else None,
            sf_display=_sf_needed_display(co.current_sf_occupied),
            submarket=co.current_submarket,
            match_score=match["score"],
            match_reasons=reasons,
            adjacent_submarket=match["adjacent"],
            is_medical=bool(co.is_medical),
        )
        for match, co, reasons in scored[:3]
    ]



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
    occ          = payload.occupancy_pct
    vacancy_pct  = round(100.0 - occ, 2) if occ is not None else None
    leased_sf    = payload.total_sf * (occ / 100.0) if occ is not None else None
    vacant_sf    = payload.total_sf * (vacancy_pct / 100.0) if vacancy_pct is not None else None
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

    # Prefer payload.asking_price_psf (e.g. CoStar Price/SF) over the derived value.
    final_asking_psf = payload.asking_price_psf if payload.asking_price_psf is not None else asking_psf

    return Property(
        property_id=property_id, address=payload.address, submarket=payload.submarket,
        asset_class=payload.asset_class, total_sf=payload.total_sf,
        year_built=payload.year_built, last_renovation_year=payload.last_renovation_year,
        owner_name=payload.owner_name, owner_type=payload.owner_type,
        owner_phone=payload.owner_phone, owner_email=payload.owner_email,
        acquisition_date=acq_date, acquisition_price=payload.acquisition_price,
        years_owned=years_owned, asking_price=payload.asking_price,
        asking_price_psf=final_asking_psf, in_place_rent_psf=payload.in_place_rent_psf,
        market_rent_psf=market_rent, market_cap_rate=market_cap,
        cap_rate=cap_rate, occupancy_pct=payload.occupancy_pct,
        vacancy_pct=vacancy_pct, leased_sf=leased_sf, vacant_sf=vacant_sf,
        sf_expiring_12mo=payload.sf_expiring_12mo, sf_expiring_24mo=payload.sf_expiring_24mo,
        lease_rollover_pct=rollover_pct, last_lease_signed_date=last_lease,
        years_since_last_lease=years_since, listed_for_sale=payload.listed_for_sale,
        days_on_market=payload.days_on_market, submarket_avg_dom=avg_dom,
        estimated_loan_maturity_year=payload.estimated_loan_maturity_year,
        notes=payload.notes,
        # CoStar enrichment fields
        star_rating=payload.star_rating,
        sf_avail=payload.sf_avail,
        landlord_representative=payload.landlord_representative,
        landlord_rep_contact=payload.landlord_rep_contact,
        sales_company=payload.sales_company,
        sales_contact=payload.sales_contact,
        tenancy=payload.tenancy,
        stories=payload.stories,
        parking_ratio=payload.parking_ratio,
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
        listed_for_sale=_bool_val(row, "listed_for_sale") or False,
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
    if bv("listed_for_sale") is not None: prop.listed_for_sale = bv("listed_for_sale")

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
    if prop.occupancy_pct is not None:
        prop.vacancy_pct = round(100.0 - prop.occupancy_pct, 2)
        prop.leased_sf   = prop.total_sf * (prop.occupancy_pct / 100.0)
        prop.vacant_sf   = prop.total_sf * (prop.vacancy_pct / 100.0)
    else:
        prop.vacancy_pct = None
        prop.leased_sf   = None
        prop.vacant_sf   = None
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
    listed_for_sale: Optional[bool] = None,
    min_score: Optional[float] = None,
    sort_by: str = Query("signal_score", pattern="^(signal_score|prediction_score|vacancy_pct|years_owned)$"),
    dominant_score_type: Optional[str] = None,
    needs_outreach: Optional[bool] = None,
    owner_confirmed_leasing: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Property)
    if submarket:   q = q.filter(Property.submarket == submarket)
    if priority:    q = q.filter(Property.priority == priority)
    if listed_for_sale is not None: q = q.filter(Property.listed_for_sale == listed_for_sale)
    if min_score is not None: q = q.filter(Property.signal_score >= min_score)
    if dominant_score_type:   q = q.filter(Property.dominant_score_type == dominant_score_type)
    if owner_confirmed_leasing is not None:
        q = q.filter(Property.owner_confirmed_leasing == owner_confirmed_leasing)
    if needs_outreach:
        # Properties with no outreach logged in the last 90 days, sorted by composite score
        from sqlalchemy import func as sqlfunc
        ninety_days_ago = (datetime.utcnow().replace(hour=0, minute=0, second=0)
                           .isoformat()[:10])
        recent_ids = (
            db.query(OutreachLog.property_id)
            .filter(
                OutreachLog.property_id.isnot(None),
                OutreachLog.generated_at >= ninety_days_ago,
            )
            .subquery()
        )
        q = (q.filter(~Property.id.in_(recent_ids))
              .filter(Property.dominant_score_type.isnot(None))
              .order_by(
                  (Property.tenant_match_score + Property.listing_rep_score + Property.acquisition_score).desc()
              )
              .limit(20))
        return q.all()
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


@router.post("/costar-import")
async def costar_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Import a CoStar raw export (.csv or .xlsx).

    Filtering pipeline (in order):
      1. State != VA          → filtered_state
      2. Submarket unmapped   → filtered_submarket (tracks unmapped_submarkets set)
      3. Building Status != Existing → filtered_status

    Deduplication key: Property Address (case-insensitive, whitespace-trimmed).
    Returns: {total_rows, filtered_state, filtered_submarket, filtered_status,
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

    print(f"[CoStar import] Columns found: {list(df.columns)}")

    # Check required columns
    col_set = set(df.columns)
    missing = [c for c in COSTAR_REQUIRED_COLS if c not in col_set]
    if missing:
        print(f"[CoStar import] Missing required columns: {missing}")
        raise HTTPException(status_code=400, detail=f"Missing CoStar columns: {', '.join(missing)}")

    df = df.replace("", None)
    rows = df.to_dict(orient="records")
    total_rows = len(rows)

    print(f"[CoStar import] Total rows before filter: {total_rows}")

    filtered_state     = 0
    filtered_submarket = 0
    filtered_status    = 0
    unmapped_submarkets: set = set()
    inserted = updated = 0
    errors: list = []

    existing: dict = {
        p.address.strip().lower(): p
        for p in db.query(Property).all()
    }

    print(f"[CoStar import] Existing properties in DB: {len(existing)}")

    for idx, row in enumerate(rows, start=2):
      # Wrap the full per-row pipeline so ANY unexpected failure (null parse,
      # type cast, missing field, DB error) is captured with its reason string
      # and logged, instead of being swallowed or crashing the whole import.
      try:
        # Filter 1: State must be VA
        state_val = _costar_str(row, "State") or ""
        if state_val.strip().upper() != "VA":
            filtered_state += 1
            continue

        # Filter 2: Submarket must map to a platform submarket
        # Null-safe + case-insensitive: a blank submarket cell becomes "" and
        # is treated as unmapped rather than raising.
        cs_sub = (_costar_str(row, "Submarket Name") or "").strip()
        sub_key = cs_sub.lower()
        if sub_key not in COSTAR_SUBMARKET_MAP:
            unmapped_submarkets.add(cs_sub or "(blank)")
            filtered_submarket += 1
            continue
        if COSTAR_SUBMARKET_MAP[sub_key] is None:
            # Ambiguous mapping (e.g. rosslyn/ballston)
            unmapped_submarkets.add(cs_sub)
            filtered_submarket += 1
            continue

        # Filter 3: Building Status must be "Existing"
        bldg_status = (_costar_str(row, "Building Status") or "").strip().lower()
        if bldg_status != "existing":
            filtered_status += 1
            continue

        # Parse row
        payload, err = _parse_costar_row(row, row_num=idx)
        if err:
            print(
                f"[CoStar import] ROW ERROR row={err.get('row', idx)} "
                f"address={err.get('address', '—')} reason={err.get('reason', 'unknown')}"
            )
            errors.append(err)
            continue

        dedupe_key = payload.address.strip().lower()

        if dedupe_key in existing:
            prop = existing[dedupe_key]
            # Update with CoStar data
            prop.owner_name   = payload.owner_name
            prop.owner_phone  = payload.owner_phone or prop.owner_phone
            prop.total_sf     = payload.total_sf
            prop.year_built   = payload.year_built
            prop.last_renovation_year = payload.last_renovation_year or prop.last_renovation_year
            prop.occupancy_pct = payload.occupancy_pct
            if payload.occupancy_pct is not None:
                prop.vacancy_pct = round(100.0 - payload.occupancy_pct, 2)
                prop.leased_sf   = payload.total_sf * (payload.occupancy_pct / 100.0)
                prop.vacant_sf   = payload.total_sf * (prop.vacancy_pct / 100.0)
            else:
                prop.vacancy_pct = None
                prop.leased_sf   = None
                prop.vacant_sf   = None
            prop.listed_for_sale = payload.listed_for_sale
            if payload.asking_price:
                prop.asking_price     = payload.asking_price
                prop.asking_price_psf = round(payload.asking_price / payload.total_sf, 2) if payload.total_sf else None
            # CoStar Price/SF takes precedence when present
            if payload.asking_price_psf is not None:
                prop.asking_price_psf = payload.asking_price_psf
            if payload.acquisition_price:
                prop.acquisition_price = payload.acquisition_price
            if payload.acquisition_year:
                acq_date = date(payload.acquisition_year, 1, 1)
                prop.acquisition_date = acq_date
                prop.years_owned = round((date.today() - acq_date).days / 365.25, 1)
            if payload.estimated_loan_maturity_year:
                prop.estimated_loan_maturity_year = payload.estimated_loan_maturity_year

            # ── User-data protection (Part 2) ─────────────────────────────────
            # Never overwrite an in-place rent that was manually entered or
            # sourced from a trusted feed (compstak, public_record, etc.) —
            # CoStar exports often have a stale or aggregated value.
            _rent_protected = (
                prop.in_place_rent_source in PROTECTED_RENT_SOURCES
                and prop.in_place_rent_last_verified is not None
            )
            # CoStar import doesn't carry an in_place_rent value (see notes
            # below), so we only re-stamp the source if the field is empty.
            if not _rent_protected and prop.in_place_rent_psf in (None, 0, 0.0):
                # Fall through — manual update required (notes flag already added).
                pass

            # Enrichment fields: only fill blanks (do not stomp on user edits).
            if payload.star_rating is not None and prop.star_rating is None:
                prop.star_rating = payload.star_rating
            if payload.sf_avail is not None and prop.sf_avail is None:
                prop.sf_avail = payload.sf_avail
            if payload.landlord_representative and not prop.landlord_representative:
                prop.landlord_representative = payload.landlord_representative
            if payload.landlord_rep_contact and not prop.landlord_rep_contact:
                prop.landlord_rep_contact = payload.landlord_rep_contact
            if payload.sales_company and not prop.sales_company:
                prop.sales_company = payload.sales_company
            if payload.sales_contact and not prop.sales_contact:
                prop.sales_contact = payload.sales_contact
            if payload.tenancy and not prop.tenancy:
                prop.tenancy = payload.tenancy
            if payload.stories is not None and prop.stories is None:
                prop.stories = payload.stories
            if payload.parking_ratio is not None and prop.parking_ratio is None:
                prop.parking_ratio = payload.parking_ratio

            # Append CoStar note without overwriting existing notes
            costar_note = "in_place_rent_psf not imported from CoStar — update manually."
            if not prop.notes:
                prop.notes = costar_note
            elif costar_note not in prop.notes:
                prop.notes = f"{prop.notes}\n{costar_note}"
            _run_signals(prop)
            updated += 1
        else:
            prop = _build_property(payload, _next_property_id(db))
            db.add(prop)
            db.flush()
            _run_signals(prop)
            existing[dedupe_key] = prop
            inserted += 1
      except Exception as exc:
        # Self-reporting diagnostic — capture whatever the failure is so it can
        # be surfaced and followed up on, without crashing the import loop.
        reason = str(exc).strip() or repr(exc)
        addr = _costar_str(row, "Property Address") or "—"
        print(f"[CoStar import] ROW ERROR row={idx} address={addr} reason={reason}")
        errors.append({"row": idx, "address": addr, "reason": reason})
        continue

    db.commit()

    print(
        f"[CoStar import] Result — inserted: {inserted}, updated: {updated}, "
        f"errors: {len(errors)}, filtered_state: {filtered_state}, "
        f"filtered_submarket: {filtered_submarket}, filtered_status: {filtered_status}, "
        f"unmapped_submarkets: {sorted(unmapped_submarkets)}"
    )

    return {
        "total_rows":          total_rows,
        "filtered_state":      filtered_state,
        "filtered_submarket":  filtered_submarket,
        "filtered_status":     filtered_status,
        "inserted":            inserted,
        "updated":             updated,
        "skipped":             len(errors),
        "unmapped_submarkets": sorted(unmapped_submarkets),
        "errors":              errors,
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
    out = _enrich(prop)
    out.matched_tenants = _compute_matched_tenants(prop, db)
    return out


@router.put("/{property_id}", response_model=PropertyOut)
def update_property(property_id: str, payload: PropertyUpdate, db: Session = Depends(get_db)):
    """Partial update — only fields present in the request body are applied.
    Derived fields (vacancy, rollover, cap_rate, etc.) are recomputed automatically.
    Signal scores are refreshed after every update."""
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # ── Apply direct-mapped fields ─────────────────────────────────────────
    if payload.address is not None:       prop.address   = payload.address
    if payload.asset_class is not None:   prop.asset_class = payload.asset_class
    if payload.total_sf is not None:      prop.total_sf  = payload.total_sf
    if payload.year_built is not None:    prop.year_built = payload.year_built
    if payload.last_renovation_year is not None:
        prop.last_renovation_year = payload.last_renovation_year
    if payload.owner_name is not None:    prop.owner_name  = payload.owner_name
    if payload.owner_type is not None:    prop.owner_type  = payload.owner_type
    if payload.owner_phone is not None:   prop.owner_phone = payload.owner_phone
    if payload.owner_email is not None:   prop.owner_email = payload.owner_email
    if payload.acquisition_price is not None:
        prop.acquisition_price = payload.acquisition_price
    if payload.in_place_rent_psf is not None:
        prop.in_place_rent_psf = payload.in_place_rent_psf
    if payload.occupancy_pct is not None: prop.occupancy_pct = payload.occupancy_pct
    if payload.sf_expiring_12mo is not None: prop.sf_expiring_12mo = payload.sf_expiring_12mo
    if payload.sf_expiring_24mo is not None: prop.sf_expiring_24mo = payload.sf_expiring_24mo
    if payload.listed_for_sale is not None:  prop.listed_for_sale  = payload.listed_for_sale
    if payload.asking_price is not None:     prop.asking_price     = payload.asking_price
    if payload.days_on_market is not None:   prop.days_on_market   = payload.days_on_market
    if payload.estimated_loan_maturity_year is not None:
        prop.estimated_loan_maturity_year = payload.estimated_loan_maturity_year
    if payload.notes is not None:            prop.notes = payload.notes
    # Owner confirmed leasing — auto-set date on first confirmation; clear when unchecked
    if payload.owner_confirmed_leasing is not None:
        prop.owner_confirmed_leasing = payload.owner_confirmed_leasing
        if payload.owner_confirmed_leasing:
            if not prop.owner_confirmed_leasing_date:
                prop.owner_confirmed_leasing_date = date.today()
        else:
            prop.owner_confirmed_leasing_date = None
    if payload.is_medical is not None:
        prop.is_medical = payload.is_medical

    # ── Submarket: also refresh market benchmarks ───────────────────────────
    if payload.submarket is not None:
        prop.submarket        = payload.submarket
        prop.market_rent_psf  = settings.submarket_market_rent.get(payload.submarket, 26.0)
        prop.market_cap_rate  = settings.submarket_cap_rate.get(payload.submarket, 6.5)
        prop.submarket_avg_dom = settings.submarket_avg_dom.get(payload.submarket, 120)

    # ── Year-based derived fields ───────────────────────────────────────────
    if payload.acquisition_year is not None:
        acq_date = date(payload.acquisition_year, 1, 1)
        prop.acquisition_date = acq_date
        prop.years_owned = round((date.today() - acq_date).days / 365.25, 1)
    if payload.last_lease_signed_year is not None:
        prop.last_lease_signed_date = date(payload.last_lease_signed_year, 6, 1)
        prop.years_since_last_lease = round(CURRENT_YEAR - payload.last_lease_signed_year, 1)

    # ── Recompute all derived numeric fields from current prop state ────────
    if prop.occupancy_pct is not None:
        prop.vacancy_pct = round(100.0 - prop.occupancy_pct, 2)
        prop.leased_sf   = prop.total_sf * (prop.occupancy_pct / 100.0)
        prop.vacant_sf   = prop.total_sf * (prop.vacancy_pct   / 100.0)
    else:
        prop.vacancy_pct = None
        prop.leased_sf   = None
        prop.vacant_sf   = None
    if prop.total_sf and prop.sf_expiring_12mo is not None:
        prop.lease_rollover_pct = round(prop.sf_expiring_12mo / prop.total_sf * 100, 2)
    if prop.asking_price and prop.total_sf:
        prop.asking_price_psf = round(prop.asking_price / prop.total_sf, 2)
    if prop.asking_price and prop.in_place_rent_psf and prop.leased_sf:
        prop.cap_rate = round(
            prop.in_place_rent_psf * prop.leased_sf * 0.55 / prop.asking_price * 100, 2
        )

    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    out = _enrich(prop)
    out.matched_tenants = _compute_matched_tenants(prop, db)
    return out


@router.delete("/{property_id}", status_code=200)
def delete_property(property_id: str, db: Session = Depends(get_db)):
    """Hard-delete a property (and its cascade-owned opportunities) from the DB.

    No soft delete. Dependent activity/outreach logs have their property_id
    nulled by the ORM relationship default. Returns 404 if the record is absent.
    """
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    db.delete(prop)
    db.commit()
    return {"deleted": property_id}


@router.post("/{property_id}/snooze", response_model=PropertyOut)
def snooze_property(property_id: str, payload: SnoozeRequest, db: Session = Depends(get_db)):
    """Snooze a property — hide it from Daily Briefing and Section A until snoozed_until date."""
    from app.models.activity import ActivityLog
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    prop.snoozed_until        = payload.snoozed_until
    prop.snooze_reason        = payload.snooze_reason
    prop.returned_from_snooze = None
    reason_str = f": {payload.snooze_reason}" if payload.snooze_reason else ""
    db.add(ActivityLog(
        property_id=prop.id,
        action_type="NOTE",
        action_taken=f"Snoozed until {payload.snoozed_until.isoformat()}{reason_str}",
        created_by="user",
    ))
    db.commit()
    db.refresh(prop)
    out = _enrich(prop)
    out.matched_tenants = _compute_matched_tenants(prop, db)
    return out


@router.post("/{property_id}/unsnooze", response_model=PropertyOut)
def unsnooze_property(property_id: str, db: Session = Depends(get_db)):
    """Remove a snooze — property immediately returns to Daily Briefing / Section A."""
    from app.models.activity import ActivityLog
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    prop.snoozed_until        = None
    prop.snooze_reason        = None
    prop.returned_from_snooze = None
    db.add(ActivityLog(
        property_id=prop.id,
        action_type="NOTE",
        action_taken="Unsnoozed manually",
        created_by="user",
    ))
    db.commit()
    db.refresh(prop)
    out = _enrich(prop)
    out.matched_tenants = _compute_matched_tenants(prop, db)
    return out


@router.get("/{property_id}/tenant-outreach", response_model=List[TenantOutreachResult])
def get_tenant_outreach(property_id: str, db: Session = Depends(get_db)):
    """Generate tenant-side outreach drafts for all matched tenants on an
    owner-confirmed-leasing property.  Returns [] when no tenants match.
    Returns 400 if owner_confirmed_leasing is not set."""
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    if not prop.owner_confirmed_leasing:
        raise HTTPException(
            status_code=400,
            detail="owner_confirmed_leasing is not set for this property",
        )

    matched = _compute_matched_tenants(prop, db)
    if not matched:
        return []

    results: list = []
    for mt in matched:
        company = db.query(Company).filter(Company.company_id == mt.company_id).first()
        if not company:
            continue

        prop_dict = {
            "address":              prop.address,
            "submarket":            prop.submarket,
            "asset_class":          prop.asset_class,
            "total_sf":             prop.total_sf,
            "sf_avail":             prop.sf_avail,
            "market_rent_psf":      prop.market_rent_psf,
            "in_place_rent_psf":    prop.in_place_rent_psf,
            "vacancy_pct":          prop.vacancy_pct,
            "listed_for_sale":      bool(prop.listed_for_sale),
            "days_on_market":       prop.days_on_market,
            "owner_name":           prop.owner_name,
            "landlord_representative": prop.landlord_representative,
            "sales_contact":        prop.sales_contact,
            "years_owned":          prop.years_owned,
            "dominant_score_type":  prop.dominant_score_type,
        }
        # Fix 1: block outreach for a tenant with unknown occupied SF. The tenant
        # still appears as a match card (with "SF: Unknown") via
        # _compute_matched_tenants — only the outreach draft is withheld here.
        if not company.current_sf_occupied:
            continue

        # headcount excluded — tenant-side emails must not reference headcount;
        # occupied SF and lease expiry are the permitted identifiers.
        tenant_dict = {
            "name":                 company.name,
            "industry":             company.industry or "professional services",
            "current_sf_occupied":  company.current_sf_occupied,
            "lease_expiry_months":  company.lease_expiry_months,
            "current_submarket":    company.current_submarket,
            "primary_contact_name": company.primary_contact_name,
        }
        outreach_type = (
            "for_sale_vacancy"
            if prop.listed_for_sale and (prop.sf_avail or 0) > 0
            else "tenant_match"
        )
        try:
            draft = generate_property_outreach(
                property_dict=prop_dict,
                outreach_type=outreach_type,
                direction="tenant_side",
                tenant_dict=tenant_dict,
            )
            email_body = draft["email_body"]

            call_parts = [
                f"Opening: {draft.get('call_script_opening', '')}",
                f"Core: {draft.get('call_script_core', '')}",
                f"Pain Probe: {draft.get('call_script_pain_probe', '')}",
                f"Close: {draft.get('call_script_close', '')}",
            ]
            call_script = "\n\n".join(p for p in call_parts if p.split(": ", 1)[-1].strip())

            results.append(TenantOutreachResult(
                company_id=company.company_id,
                company_name=company.name,
                contact_name=company.primary_contact_name,
                sf_needed=company.current_sf_occupied if company.current_sf_occupied else None,
                lease_expiry_months=company.lease_expiry_months,
                email_draft=email_body,
                call_script=call_script,
            ))
        except Exception as e:
            # GPT unavailable / rate-limited — skip gracefully, don't 500
            print(f"[tenant-outreach] error for {company.company_id}: {e}")
            continue

    return results


@router.post("/{property_id}/refresh-signals", response_model=PropertyOut)
def refresh_property_signals(property_id: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    out = _enrich(prop)
    out.matched_tenants = _compute_matched_tenants(prop, db)
    return out


# ── Property outreach endpoints (Part 4) ───────────────────────────────────

@router.post("/{property_id}/draft-outreach", response_model=OutreachDraft)
def draft_property_outreach(
    property_id: str,
    outreach_type: str = Query("tenant_match", regex="^(tenant_match|for_sale_vacancy|listing_rep|acquisition)$"),
    tenant_context: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    intel_context_raw: Optional[str] = Query(None),
    direction: str = Query("property_side", regex="^(property_side|tenant_side)$"),
    company_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    import json
    from app.models.company import Company

    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    intel_list = json.loads(intel_context_raw) if intel_context_raw else None
    prop_dict = {c.key: getattr(prop, c.key) for c in prop.__table__.columns}

    # Always do fresh Company lookup when company_id provided — ensures the email
    # uses the correct tenant profile even when tabs switch (frontend tenant_context
    # is frozen at modal-open time and may be stale).
    tenant_dict = None
    if company_id:
        comp = db.query(Company).filter(Company.company_id == company_id).first()
        if comp:
            # Fix 1 & Fix 2: for a tenant-paired draft, block outreach when the
            # tenant's occupied SF is unknown, and suppress it when the SF gap to
            # the property exceeds MAX_SF_DELTA — unless the pair is already
            # contacted (contacted pairs are never disturbed).
            if outreach_type in ("tenant_match", "for_sale_vacancy"):
                from app.services.match_scoring import sf_match_suppressed, MAX_SF_DELTA
                from app.services.opportunity_stage_service import pair_is_contacted
                already_contacted = pair_is_contacted(db, prop.id, comp.id)
                if not already_contacted:
                    if not comp.current_sf_occupied:
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                "SF: Unknown — set 'SF Occupied (CoStar)' on this tenant "
                                "before generating outreach."
                            ),
                        )
                    if sf_match_suppressed(comp.current_sf_occupied, prop.sf_avail):
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                "SF mismatch — occupied SF and available SF differ by "
                                f"more than {MAX_SF_DELTA} SF; pairing suppressed."
                            ),
                        )
            tenant_dict = {c.key: getattr(comp, c.key) for c in comp.__table__.columns}
            hc  = comp.current_headcount
            sf  = comp.current_sf_occupied
            exp = comp.lease_expiry_months
            tenant_context = (
                f"Headcount: {hc if hc is not None else 'N/A'}; "
                f"SF Occupied: {f'{int(sf):,}' if sf else 'N/A'}; "
                f"Lease Expiry: {f'{exp}mo' if exp is not None else 'N/A'}"
            )

    # Detect secondary demand: more than one matched tenant on this property
    try:
        matched = _compute_matched_tenants(prop, db)
        has_secondary_demand = len(matched) > 1
    except Exception:
        has_secondary_demand = False

    result = generate_property_outreach(
        prop_dict, outreach_type, target_type, tenant_context,
        intel_context=intel_list,
        direction=direction,
        tenant_dict=tenant_dict,
        has_secondary_demand=has_secondary_demand,
    )

    score_map = {
        "tenant_match": prop.tenant_match_score or 0.0,
        "listing_rep":  prop.listing_rep_score  or 0.0,
        "acquisition":  prop.acquisition_score  or 0.0,
    }
    score = score_map.get(outreach_type, prop.signal_score or 0.0)
    priority = prop.priority or "Medium"

    return OutreachDraft(
        email_subject=result["email_subject"],
        email_body=result["email_body"],
        call_script=CallScript(
            opening=result["call_script_opening"],
            core_message=result["call_script_core"],
            pain_probe=result["call_script_pain_probe"],
            the_close=result["call_script_close"],
        ),
        score=score,
        priority=priority,
        generated_at=datetime.utcnow(),
        outreach_type=outreach_type,
        target_type=result.get("target_type"),
    )


@router.post("/{property_id}/log-outreach", response_model=OutreachLogOut)
def log_property_outreach(
    property_id: str,
    payload: OutreachLogCreate,
    db: Session = Depends(get_db),
):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    log = OutreachLog(
        property_id=prop.id,
        company_id=None,
        outreach_type=payload.outreach_type or "broker",
        email_subject=payload.email_subject,
        email_body=payload.email_body,
        call_script_opening=payload.call_script_opening,
        call_script_hook=payload.call_script_hook,
        call_script_data=payload.call_script_data,
        call_script_core=payload.call_script_core,
        call_script_pain_probe=payload.call_script_pain_probe,
        call_script_close=payload.call_script_close,
        projected_sf=payload.projected_sf,
        score_at_generation=payload.score_at_generation,
        priority_at_generation=payload.priority_at_generation,
        email_sent=int(payload.email_sent),
        call_made=int(payload.call_made),
        generated_at=datetime.utcnow(),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/{property_id}/outreach-history", response_model=List[OutreachLogOut])
def property_outreach_history(
    property_id: str,
    db: Session = Depends(get_db),
):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    logs = (
        db.query(OutreachLog)
        .filter(OutreachLog.property_id == prop.id)
        .order_by(OutreachLog.generated_at.desc())
        .all()
    )
    return logs


# ── In-Place Rent pencil update (Part 7) ───────────────────────────────────

@router.patch("/{property_id}/in-place-rent", response_model=PropertyOut)
def update_in_place_rent(
    property_id: str,
    payload: InPlaceRentUpdate,
    db: Session = Depends(get_db),
):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    prop.in_place_rent_psf          = payload.in_place_rent_psf
    prop.in_place_rent_source       = payload.in_place_rent_source
    prop.in_place_rent_last_verified = datetime.utcnow().date().isoformat()
    prop.last_modified_by_user      = datetime.utcnow().isoformat()

    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    return _enrich(prop)
