from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.property import Property
from app.schemas.property import PropertyOut, PropertyListOut, PropertyCreate, SignalBreakdown
from app.services import signal_engine as se
from app.services.scoring_model import score_property
from app.config import settings

CURRENT_YEAR = 2026


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


def _run_signals(prop: Property) -> None:
    """Recompute all signals for a property and persist sub-scores."""
    pred_result = se.compute_prediction_score(
        prop.lease_rollover_pct,
        prop.vacancy_pct,
        prop.vacancy_12mo_ago,
        prop.years_owned or 0,
        prop.years_since_last_lease or 0,
        prop.year_built,
        prop.last_renovation_year,
    )
    owner_result = se.compute_owner_behavior_score(
        prop.years_owned or 0,
        prop.vacancy_pct,
        prop.vacancy_12mo_ago,
        prop.in_place_rent_psf,
        prop.market_rent_psf,
        prop.year_built,
        prop.last_renovation_year,
        prop.estimated_loan_maturity_year,
    )
    from app.config import settings
    misp_result = se.compute_mispricing_score(
        prop.in_place_rent_psf,
        prop.market_rent_psf,
        prop.asking_price_psf,
        settings.submarket_avg_psf.get(prop.submarket, 250),
        prop.days_on_market,
        prop.submarket_avg_dom,
        prop.cap_rate,
        prop.market_cap_rate,
        prop.is_listed,
    )

    pred_comp  = pred_result["composite"]
    owner_comp = owner_result["composite"]
    misp_comp  = misp_result["composite"]
    scored     = score_property(pred_comp, owner_comp, misp_comp, 0, prop.is_listed)

    # Store sub-scores
    pb = pred_result["breakdown"]
    ob = owner_result["breakdown"]
    mb = misp_result["breakdown"]

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
        lease_rollover           = prop.sig_lease_rollover,
        vacancy_trend            = prop.sig_vacancy_trend,
        ownership_duration       = prop.sig_ownership_duration,
        leasing_drought          = prop.sig_leasing_drought,
        capex_gap                = prop.sig_capex_gap,
        hold_period              = prop.sig_hold_period,
        occupancy_decline        = prop.sig_occupancy_decline,
        rent_stagnation          = prop.sig_rent_stagnation,
        reinvestment_inactivity  = prop.sig_reinvestment_inactivity,
        debt_pressure            = prop.sig_debt_pressure,
        rent_gap                 = prop.sig_rent_gap,
        price_psf                = prop.sig_price_psf,
        dom_premium              = prop.sig_dom_premium,
        cap_rate_spread          = prop.sig_cap_rate_spread,
    )
    out = PropertyOut.model_validate(prop)
    out.signal_breakdown = breakdown
    return out


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
    if submarket:
        q = q.filter(Property.submarket == submarket)
    if priority:
        q = q.filter(Property.priority == priority)
    if is_listed is not None:
        q = q.filter(Property.is_listed == is_listed)
    if min_score is not None:
        q = q.filter(Property.signal_score >= min_score)

    col = getattr(Property, sort_by, Property.signal_score)
    props = q.order_by(col.desc()).all()
    return props


@router.post("/", response_model=PropertyOut)
def create_property(payload: PropertyManualCreate, db: Session = Depends(get_db)):
    """Manually add a new property. Signals are computed immediately after creation."""

    # Auto-generate property_id (NVA-XXX)
    existing_ids = [p.property_id for p in db.query(Property.property_id).all()]
    nums = []
    for pid in existing_ids:
        try:
            nums.append(int(pid.split("-")[1]))
        except (IndexError, ValueError):
            pass
    next_num = (max(nums) + 1) if nums else 1
    property_id = f"NVA-{next_num:03d}"

    # Derived fields
    vacancy_pct   = round(100.0 - payload.occupancy_pct, 2)
    leased_sf     = payload.total_sf * (payload.occupancy_pct / 100.0)
    vacant_sf     = payload.total_sf * (vacancy_pct / 100.0)
    rollover_pct  = round(payload.sf_expiring_12mo / payload.total_sf * 100, 2) if payload.total_sf else 0.0

    # Pull market benchmarks from config
    market_rent   = settings.submarket_market_rent.get(payload.submarket, 26.0)
    market_cap    = settings.submarket_cap_rate.get(payload.submarket, 6.5)
    avg_dom       = settings.submarket_avg_dom.get(payload.submarket, 120)

    # Acquisition date + years owned
    acq_date = date(payload.acquisition_year, 1, 1) if payload.acquisition_year else None
    years_owned = round((date.today() - acq_date).days / 365.25, 1) if acq_date else 0.0

    # Years since last lease
    if payload.last_lease_signed_year:
        years_since_last_lease = round(CURRENT_YEAR - payload.last_lease_signed_year, 1)
        last_lease_date = date(payload.last_lease_signed_year, 6, 1)
    else:
        years_since_last_lease = 0.0
        last_lease_date = None

    # Asking price PSF
    asking_psf = None
    if payload.asking_price and payload.total_sf:
        asking_psf = round(payload.asking_price / payload.total_sf, 2)

    # In-place cap rate estimate
    cap_rate = None
    if payload.asking_price and payload.in_place_rent_psf and leased_sf:
        noi_estimate = payload.in_place_rent_psf * leased_sf * 0.55
        cap_rate = round(noi_estimate / payload.asking_price * 100, 2)

    prop = Property(
        property_id              = property_id,
        address                  = payload.address,
        submarket                = payload.submarket,
        asset_class              = payload.asset_class,
        total_sf                 = payload.total_sf,
        year_built               = payload.year_built,
        last_renovation_year     = payload.last_renovation_year,
        owner_name               = payload.owner_name,
        owner_type               = payload.owner_type,
        owner_phone              = payload.owner_phone,
        owner_email              = payload.owner_email,
        acquisition_date         = acq_date,
        acquisition_price        = payload.acquisition_price,
        years_owned              = years_owned,
        asking_price             = payload.asking_price,
        asking_price_psf         = asking_psf,
        in_place_rent_psf        = payload.in_place_rent_psf,
        market_rent_psf          = market_rent,
        market_cap_rate          = market_cap,
        cap_rate                 = cap_rate,
        occupancy_pct            = payload.occupancy_pct,
        vacancy_pct              = vacancy_pct,
        leased_sf                = leased_sf,
        vacant_sf                = vacant_sf,
        sf_expiring_12mo         = payload.sf_expiring_12mo,
        sf_expiring_24mo         = payload.sf_expiring_24mo,
        lease_rollover_pct       = rollover_pct,
        last_lease_signed_date   = last_lease_date,
        years_since_last_lease   = years_since_last_lease,
        is_listed                = payload.is_listed,
        days_on_market           = payload.days_on_market,
        submarket_avg_dom        = avg_dom,
        estimated_loan_maturity_year = payload.estimated_loan_maturity_year,
        notes                    = payload.notes,
    )
    db.add(prop)
    db.flush()

    # Run signals immediately so the new property has scores
    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    return _enrich(prop)


@router.get("/{property_id}", response_model=PropertyOut)
def get_property(property_id: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return _enrich(prop)


@router.post("/refresh-signals", response_model=dict)
def refresh_all_signals(db: Session = Depends(get_db)):
    """Re-run signal engine on all properties."""
    props = db.query(Property).all()
    for prop in props:
        _run_signals(prop)
    db.commit()
    return {"refreshed": len(props), "timestamp": str(datetime.utcnow())}


@router.post("/{property_id}/refresh-signals", response_model=PropertyOut)
def refresh_property_signals(property_id: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.property_id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    _run_signals(prop)
    db.commit()
    db.refresh(prop)
    return _enrich(prop)
