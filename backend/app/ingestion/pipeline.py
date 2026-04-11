"""
Deal Radar OS — ETL Pipeline
==============================
Orchestrates all data ingestion and signal recalculation.

Daily pipeline (runs at 06:00 EST):
  1. Refresh CoStar listings (active + new)
  2. Refresh public records (ownership changes, permits)
  3. Refresh company intelligence (headcount, job postings)
  4. Recalculate all property signals
  5. Recalculate all company signals
  6. Run deal creation engine (find new matches)
  7. Generate daily briefing

On-demand: /api/properties/refresh-signals, /api/companies/refresh-signals
"""

import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.property import Property
from app.models.company import Company
from app.models.opportunity import Opportunity
from app.models.activity import ActivityLog
from app.services import signal_engine as se
from app.services.scoring_model import score_property
from app.services.deal_creation_engine import (
    create_opportunity_from_match,
    _is_nearby,
    _estimated_sf_needed,
    ADJACENT_SUBMARKETS,
)
from app.config import settings

logger = logging.getLogger(__name__)

CURRENT_YEAR = 2026


def _compute_years_owned(acquisition_date: Optional[date]) -> float:
    if not acquisition_date:
        return 0.0
    today = date.today()
    return round((today - acquisition_date).days / 365.25, 2)


def refresh_property_signals(db: Session, prop: Property) -> None:
    """Full signal recompute for a single property."""
    if prop.acquisition_date:
        prop.years_owned = _compute_years_owned(prop.acquisition_date)

    pred = se.compute_prediction_score(
        prop.lease_rollover_pct,
        prop.vacancy_pct,
        prop.vacancy_12mo_ago,
        prop.years_owned or 0,
        prop.years_since_last_lease or 0,
        prop.year_built,
        prop.last_renovation_year,
    )
    owner = se.compute_owner_behavior_score(
        prop.years_owned or 0,
        prop.vacancy_pct,
        prop.vacancy_12mo_ago,
        prop.in_place_rent_psf,
        prop.market_rent_psf,
        prop.year_built,
        prop.last_renovation_year,
        prop.estimated_loan_maturity_year,
    )
    misp = se.compute_mispricing_score(
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

    pred_comp  = pred["composite"]
    owner_comp = owner["composite"]
    misp_comp  = misp["composite"]
    scored     = score_property(pred_comp, owner_comp, misp_comp, 0, prop.is_listed)

    pb, ob, mb = pred["breakdown"], owner["breakdown"], misp["breakdown"]

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
    prop.prediction_score            = pred_comp
    prop.owner_behavior_score        = owner_comp
    prop.mispricing_score            = misp_comp
    prop.signal_score                = scored["score"]
    prop.priority                    = scored["priority"]
    prop.deal_type                   = scored["deal_type"]
    prop.last_signal_run             = datetime.utcnow()


def refresh_company_signals(db: Session, company: Company) -> None:
    """Full signal recompute for a single company."""
    if company.current_sf and company.current_headcount:
        company.sf_per_head = round(company.current_sf / company.current_headcount, 1)

    if company.headcount_12mo_ago and company.headcount_12mo_ago > 0:
        company.headcount_growth_pct = round(
            (company.current_headcount - company.headcount_12mo_ago)
            / company.headcount_12mo_ago * 100, 1
        )

    if company.current_headcount > 0:
        company.hiring_velocity = round(
            (company.open_positions or 0) / company.current_headcount * 100, 1
        )

    result = se.compute_tenant_opportunity_score(
        company.headcount_growth_pct,
        company.open_positions or 0,
        company.current_headcount or 1,
        company.lease_expiry_months,
        company.current_sf,
        company.current_submarket,
        nearby_company_count=1,
    )
    breakdown = result["breakdown"]
    company.sig_headcount_growth  = breakdown["headcount_growth"]
    company.sig_hiring_velocity   = breakdown["hiring_velocity"]
    company.sig_lease_expiry      = breakdown["lease_expiry"]
    company.sig_space_utilization = breakdown["space_utilization"]
    company.sig_geo_clustering    = breakdown["geo_clustering"]
    company.opportunity_score     = result["composite"]

    composite = result["composite"]
    if composite >= 75:
        company.priority = "IMMEDIATE"
    elif composite >= 62:
        company.priority = "HIGH"
    elif composite >= 42:
        company.priority = "WORKABLE"
    else:
        company.priority = "IGNORE"

    company.expansion_signal = (
        (company.headcount_growth_pct or 0) >= 15
        and (company.lease_expiry_months or 999) <= 24
        and (company.sf_per_head or 999) <= 150
    )
    company.estimated_sf_needed = _estimated_sf_needed(company)


def run_deal_creation(db: Session) -> int:
    """
    Match growing companies to available properties and create opportunities.
    Only creates new opportunities — does not overwrite existing ones.
    """
    created_count = 0

    # Get companies with expansion signal
    companies = (
        db.query(Company)
        .filter(
            Company.expansion_signal == True,
            Company.lease_expiry_months <= 24,
        )
        .all()
    )

    # Get properties with meaningful vacancy
    properties = (
        db.query(Property)
        .filter(Property.vacancy_pct >= 15)
        .all()
    )

    existing_pairs = set(
        (o.property_id, o.company_id)
        for o in db.query(Opportunity.property_id, Opportunity.company_id)
        .filter(Opportunity.is_active == True).all()
    )

    for company in companies:
        tenant_result = se.compute_tenant_opportunity_score(
            company.headcount_growth_pct,
            company.open_positions or 0,
            company.current_headcount or 1,
            company.lease_expiry_months,
            company.current_sf,
            company.current_submarket,
        )
        tenant_composite = tenant_result["composite"]

        if tenant_composite < 45:
            continue

        for prop in properties:
            if not _is_nearby(prop.submarket, company.current_submarket):
                continue

            if (prop.id, company.id) in existing_pairs:
                continue

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
                prop.in_place_rent_psf, prop.market_rent_psf,
                prop.asking_price_psf,
                settings.submarket_avg_psf.get(prop.submarket, 250),
                prop.days_on_market, prop.submarket_avg_dom,
                prop.cap_rate, prop.market_cap_rate, prop.is_listed,
            )

            signal_results = {
                "prediction":    pred_result,
                "owner_behavior": owner_result,
                "mispricing":    misp_result,
                "tenant":        tenant_result,
            }

            opp_data = create_opportunity_from_match(prop, company, signal_results)
            if opp_data:
                new_opp = Opportunity(**opp_data)
                db.add(new_opp)
                existing_pairs.add((prop.id, company.id))
                created_count += 1

    # Also create standalone property opportunities (no tenant)
    for prop in properties:
        if prop.prediction_score >= 60 or prop.mispricing_score >= 50:
            exists = db.query(Opportunity).filter(
                Opportunity.property_id == prop.id,
                Opportunity.company_id == None,
                Opportunity.is_active == True,
            ).first()
            if not exists:
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
                    prop.in_place_rent_psf, prop.market_rent_psf,
                    prop.asking_price_psf,
                    settings.submarket_avg_psf.get(prop.submarket, 250),
                    prop.days_on_market, prop.submarket_avg_dom,
                    prop.cap_rate, prop.market_cap_rate, prop.is_listed,
                )
                signal_results = {
                    "prediction":     pred_result,
                    "owner_behavior": owner_result,
                    "mispricing":     misp_result,
                }
                opp_data = create_opportunity_from_match(prop, None, signal_results)
                if opp_data:
                    new_opp = Opportunity(**opp_data)
                    db.add(new_opp)
                    created_count += 1

    db.commit()
    logger.info(f"[Pipeline] Deal creation complete — {created_count} new opportunities created")
    return created_count


def run_full_pipeline(db: Session = None) -> dict:
    """
    Full daily pipeline. Called by APScheduler or manually via API.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        start = datetime.utcnow()
        logger.info("[Pipeline] Starting daily refresh")

        # 1. Refresh property signals
        props = db.query(Property).all()
        for prop in props:
            refresh_property_signals(db, prop)
        db.commit()
        logger.info(f"[Pipeline] Refreshed signals for {len(props)} properties")

        # 2. Refresh company signals
        companies = db.query(Company).all()
        for company in companies:
            refresh_company_signals(db, company)
        db.commit()
        logger.info(f"[Pipeline] Refreshed signals for {len(companies)} companies")

        # 3. Run deal creation
        new_opps = run_deal_creation(db)

        elapsed = (datetime.utcnow() - start).seconds
        result = {
            "status":               "success",
            "properties_refreshed": len(props),
            "companies_refreshed":  len(companies),
            "new_opportunities":    new_opps,
            "elapsed_seconds":      elapsed,
            "timestamp":            start.isoformat(),
        }
        logger.info(f"[Pipeline] Complete: {result}")
        return result

    finally:
        if close_db:
            db.close()
