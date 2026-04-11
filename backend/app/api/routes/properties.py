from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.property import Property
from app.schemas.property import PropertyOut, PropertyListOut, PropertyCreate, SignalBreakdown
from app.services import signal_engine as se
from app.services.scoring_model import score_property

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
