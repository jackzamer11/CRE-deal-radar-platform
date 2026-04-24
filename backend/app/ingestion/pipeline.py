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
import logging.handlers
import os
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

# ── Pipeline logger — writes to both console and pipeline.log ──────────────
logger = logging.getLogger("deal_radar.pipeline")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _log_path = os.path.join(os.path.dirname(__file__), "..", "..", "pipeline.log")
    _log_path = os.path.normpath(_log_path)
    _fh = logging.handlers.RotatingFileHandler(
        _log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logger.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logger.addHandler(_sh)

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
    prop.prediction_score            = pred_comp
    prop.owner_behavior_score        = owner_comp
    prop.mispricing_score            = misp_comp
    prop.signal_score                = scored["score"]
    prop.priority                    = scored["priority"]
    prop.deal_type                   = scored["deal_type"]
    prop.last_signal_run             = datetime.utcnow()
    prop.signals_scored_count        = (
        pred["signals_scored"] + owner["signals_scored"] + misp["signals_scored"]
    )
    prop.insufficient_data           = prop.signals_scored_count < 3


def refresh_company_signals(db: Session, company: Company) -> None:
    """Full signal recompute for a single company."""
    if company.current_headcount:
        if company.current_sf:
            company.sf_per_head = round(company.current_sf / company.current_headcount, 1)

        if company.headcount_12mo_ago and company.headcount_12mo_ago > 0:
            company.headcount_growth_pct = round(
                (company.current_headcount - company.headcount_12mo_ago)
                / company.headcount_12mo_ago * 100, 1
            )

        company.hiring_velocity = round(
            (company.open_positions or 0) / company.current_headcount * 100, 1
        )

    result = se.compute_tenant_opportunity_score(
        company.headcount_growth_pct,
        company.open_positions or 0,
        company.current_headcount,          # None → signal abstains correctly
        company.lease_expiry_months,
        company.current_sf,
        company.current_submarket,
        nearby_company_count=1,
    )
    breakdown = result["breakdown"]
    # Store sub-scores; None (abstain) persisted as 0.0
    company.sig_headcount_growth  = breakdown["headcount_growth"]  or 0.0
    company.sig_hiring_velocity   = breakdown["hiring_velocity"]   or 0.0
    company.sig_lease_expiry      = breakdown["lease_expiry"]      or 0.0
    company.sig_space_utilization = breakdown["space_utilization"] or 0.0
    company.sig_geo_clustering    = breakdown["geo_clustering"]    or 0.0
    company.opportunity_score     = result["composite"]
    company.signals_scored_count  = result["signals_scored"]
    company.insufficient_data     = result["insufficient_data"]

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


def run_deal_creation(db: Session) -> dict:
    """
    Match growing companies to available properties and create opportunities.

    Runs a full cross-product of ALL companies × ALL properties:
      - Submarket match or adjacency (ADJACENT_SUBMARKETS map)
      - SF fit: company current_sf ±30% vs property RBA (skipped when current_sf unknown)
      - Final threshold applied by create_opportunity_from_match (IGNORE priority → skip)
    Companies with missing lease expiry or headcount (insufficient_data=True) are
    included — their opportunities are scored on available signals only.
    """
    pairings_evaluated    = 0
    passed_submarket      = 0
    passed_sf_fit         = 0
    created_count         = 0

    # Full scan — no pre-filtering; let scoring engine decide quality
    all_companies  = db.query(Company).all()
    all_properties = db.query(Property).all()

    logger.info(
        "[Pipeline] Deal creation starting: %d companies × %d properties = %d potential pairings",
        len(all_companies), len(all_properties), len(all_companies) * len(all_properties),
    )

    existing_pairs = set(
        (o.property_id, o.company_id)
        for o in db.query(Opportunity.property_id, Opportunity.company_id)
        .filter(Opportunity.is_active == True).all()
    )

    for company in all_companies:
        tenant_result = se.compute_tenant_opportunity_score(
            company.headcount_growth_pct,
            company.open_positions or 0,
            company.current_headcount,
            company.lease_expiry_months,
            company.current_sf,
            company.current_submarket,
        )
        tenant_composite = tenant_result["composite"]

        # Skip companies with virtually no tenant signal (only geo_clustering base).
        # geo_clustering always returns 15.0 when submarket is set; weight is 5%
        # → composite = 15.0 when all other signals abstain. Threshold of 10 lets
        # everyone with a submarket through and lets the scorer handle quality.
        if tenant_composite < 10:
            continue

        for prop in all_properties:
            pairings_evaluated += 1

            # Filter 1: submarket proximity
            if not _is_nearby(prop.submarket, company.current_submarket):
                continue
            passed_submarket += 1

            # Filter 2: SF fit ±30% of tenant's current footprint
            if company.current_sf:
                lo = company.current_sf * 0.70
                hi = company.current_sf * 1.30
                if not (lo <= prop.total_sf <= hi):
                    continue
            passed_sf_fit += 1

            # Filter 3: skip already-existing active pairs
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
                "prediction":     pred_result,
                "owner_behavior": owner_result,
                "mispricing":     misp_result,
                "tenant":         tenant_result,
            }

            opp_data = create_opportunity_from_match(prop, company, signal_results)
            if opp_data:
                new_opp = Opportunity(**opp_data)
                db.add(new_opp)
                existing_pairs.add((prop.id, company.id))
                created_count += 1

    # Standalone property opportunities (no tenant match needed)
    standalone_created = 0
    for prop in all_properties:
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
                    standalone_created += 1

    db.commit()

    stats = {
        "companies_considered":  len(all_companies),
        "properties_considered": len(all_properties),
        "pairings_evaluated":    pairings_evaluated,
        "passed_submarket":      passed_submarket,
        "passed_sf_fit":         passed_sf_fit,
        "tenant_matches_created": created_count,
        "standalone_created":    standalone_created,
        "total_created":         created_count + standalone_created,
    }
    logger.info(
        "[Pipeline] Deal creation complete — %d pairings evaluated | "
        "%d passed submarket | %d passed SF fit | "
        "%d tenant matches + %d standalone = %d total new opportunities",
        pairings_evaluated, passed_submarket, passed_sf_fit,
        created_count, standalone_created, created_count + standalone_created,
    )
    return stats


def refresh_public_records(db: Session) -> int:
    """
    Enrich properties with live data from:
      - Arlington County Open Data (building permits + assessments)
      - Fairfax County iCARE / GIS REST API (CAMA assessments)

    Updates: assessed_value (estimated_value), last_renovation_year,
             owner_name (if blank), acquisition_price (if blank).
    Runs before signal recalculation so enriched data feeds into scores.
    Failures are logged but never raise — pipeline continues regardless.
    """
    from app.ingestion.adapters.arlington_opendata import (
        fetch_building_permits,
        fetch_property_assessment,
        get_last_major_permit_year,
    )
    from app.ingestion.adapters.fairfax_icare import enrich_property_from_fairfax
    from datetime import date as _date

    updated = 0
    props = db.query(Property).all()

    for prop in props:
        submarket = (prop.submarket or "").lower()
        assessment = None

        try:
            if "arlington" in submarket:
                # ── Arlington County ──────────────────────────────────────
                assessment = fetch_property_assessment(prop.address)

                # Permit history → last renovation year
                permits = fetch_building_permits(
                    address_fragment=prop.address.split(",")[0][:30]
                )
                reno_year = get_last_major_permit_year(permits)
                if reno_year and (
                    not prop.last_renovation_year
                    or reno_year > prop.last_renovation_year
                ):
                    prop.last_renovation_year = reno_year
                    logger.info(
                        "[Pipeline] Arlington permit → %s renovation year: %d",
                        prop.property_id, reno_year,
                    )

            elif any(s in submarket for s in ("tysons", "reston", "falls church", "fairfax")):
                # ── Fairfax County ────────────────────────────────────────
                assessment = enrich_property_from_fairfax(prop.address)

                if assessment and assessment.get("effective_year"):
                    eff = assessment["effective_year"]
                    if not prop.last_renovation_year or eff > prop.last_renovation_year:
                        prop.last_renovation_year = eff

            # Apply assessment data (common for both counties)
            if assessment:
                if assessment.get("assessed_value"):
                    prop.estimated_value = assessment["assessed_value"]

                if assessment.get("owner_name") and not prop.owner_name:
                    prop.owner_name = assessment["owner_name"]

                if assessment.get("last_sale_price") and not prop.acquisition_price:
                    prop.acquisition_price = assessment["last_sale_price"]

                if assessment.get("last_sale_date") and not prop.acquisition_date:
                    try:
                        prop.acquisition_date = _date.fromisoformat(
                            str(assessment["last_sale_date"])[:10]
                        )
                    except (ValueError, TypeError):
                        pass

                updated += 1

        except Exception as exc:
            logger.warning(
                "[Pipeline] Public records failed for %s: %s", prop.property_id, exc
            )

    db.commit()
    logger.info("[Pipeline] Public records refresh — %d/%d properties enriched", updated, len(props))
    return updated


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
        logger.info("[Pipeline] ═══════════════════════════════════════════")
        logger.info("[Pipeline] Starting full pipeline run at %s", start.isoformat())

        # 1. Enrich from public records (Arlington + Fairfax APIs)
        enriched = refresh_public_records(db)
        logger.info("[Pipeline] Public records: %d properties enriched", enriched)

        # 2. Refresh property signals
        props = db.query(Property).all()
        for prop in props:
            refresh_property_signals(db, prop)
        db.commit()
        logger.info("[Pipeline] Property signals refreshed: %d properties", len(props))

        # 3. Refresh company signals
        companies = db.query(Company).all()
        for company in companies:
            refresh_company_signals(db, company)
        db.commit()
        logger.info("[Pipeline] Company signals refreshed: %d companies", len(companies))

        # 4. Run deal creation
        deal_stats = run_deal_creation(db)

        elapsed = (datetime.utcnow() - start).seconds
        result = {
            "status":                "success",
            "properties_enriched":   enriched,
            "properties_refreshed":  len(props),
            "companies_refreshed":   len(companies),
            "new_opportunities":     deal_stats["total_created"],
            "elapsed_seconds":       elapsed,
            "timestamp":             start.isoformat(),
            "pipeline_detail": {
                "companies_considered":   deal_stats["companies_considered"],
                "properties_considered":  deal_stats["properties_considered"],
                "pairings_evaluated":     deal_stats["pairings_evaluated"],
                "passed_submarket":       deal_stats["passed_submarket"],
                "passed_sf_fit":          deal_stats["passed_sf_fit"],
                "tenant_matches_created": deal_stats["tenant_matches_created"],
                "standalone_created":     deal_stats["standalone_created"],
            },
        }
        logger.info("[Pipeline] Complete in %ds: %s", elapsed, result)
        logger.info("[Pipeline] ═══════════════════════════════════════════")
        return result

    except Exception as exc:
        logger.exception("[Pipeline] FATAL ERROR: %s", exc)
        raise

    finally:
        if close_db:
            db.close()
