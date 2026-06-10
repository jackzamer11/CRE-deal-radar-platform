"""
Deal Radar OS — Output Engine
================================
Generates the daily briefing: who to call, why now, what to say.

Daily output:
  • Top 10 IMMEDIATE deals (ranked by score)
  • Top 5 PRE_MARKET predictions
  • Top 5 TENANT_DRIVEN opportunities
  • Statistics summary
"""

from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.opportunity import Opportunity
from app.models.property import Property
from app.models.company import Company
from app.models.outreach_log import OutreachLog
from app.services.match_scoring import compute_match
from app.schemas.dashboard import (
    DailyBriefing, DashboardStats, CallTarget, TenantMatchTarget,
    TenantMatchAction, AcquisitionTarget, ExpiredLease,
)


_SIGNAL_LABEL = {
    "sig_debt_pressure":        "Debt Pressure",
    "sig_hold_period":          "Long Hold",
    "sig_vacancy_trend":        "Vacancy Trend",
    "sig_lease_rollover":       "Lease Rollover",
    "sig_capex_gap":            "CapEx Gap",
    "sig_rent_gap":             "Rent Gap",
    "sig_dom_premium":          "DOM Premium",
    "sig_cap_rate_spread":      "Cap Rate Spread",
    "sig_occupancy_decline":    "Occupancy Decline",
    "sig_reinvestment_inactivity": "Reinvestment Gap",
    "sig_rent_stagnation":      "Rent Stagnation",
    "sig_ownership_duration":   "Long Ownership",
    "sig_leasing_drought":      "Leasing Drought",
}


def _dominant_signal_label(prop: Property) -> str:
    best_name, best_val = None, -1.0
    for col, label in _SIGNAL_LABEL.items():
        val = getattr(prop, col, None)
        if val is not None and val > best_val:
            best_val = val
            best_name = label
    return best_name or ""


def _is_snoozed(prop: Property) -> bool:
    """Return True if the property is currently under an active snooze."""
    return prop.snoozed_until is not None and prop.snoozed_until > date.today()


def _process_snooze_returns(db: Session) -> list:
    """
    On each briefing load, find properties whose snooze has just expired.
    - Clears snoozed_until / snooze_reason
    - Sets returned_from_snooze = True (historical record)
    Returns list of property_ids that returned this load (for badge display).
    """
    today = date.today()
    expiring = (
        db.query(Property)
        .filter(Property.snoozed_until.isnot(None), Property.snoozed_until <= today)
        .all()
    )
    prop_ids = []
    for prop in expiring:
        prop.snoozed_until        = None
        prop.snooze_reason        = None
        prop.returned_from_snooze = True
        prop_ids.append(prop.property_id)
    if expiring:
        db.commit()
    return prop_ids


def _is_company_snoozed(company: Company) -> bool:
    """Return True if the company is currently under an active snooze."""
    return company.snoozed_until is not None and company.snoozed_until > date.today()


def _process_company_snooze_returns(db: Session) -> list:
    """
    On each briefing load, find companies whose snooze has just expired.
    - Clears snoozed_until / snooze_reason
    - Sets returned_from_snooze = True (historical record)
    Returns list of company_ids that returned this load.
    Null-safe: companies with snoozed_until = NULL are never touched.
    """
    today = date.today()
    expiring = (
        db.query(Company)
        .filter(Company.snoozed_until.isnot(None), Company.snoozed_until <= today)
        .all()
    )
    company_ids = []
    for company in expiring:
        company.snoozed_until        = None
        company.snooze_reason        = None
        company.returned_from_snooze = True
        company_ids.append(company.company_id)
    if expiring:
        db.commit()
    return company_ids


def _compute_tenant_actions(db: Session) -> list:
    """Compute Section A: property+tenant match pairs for the briefing."""
    # Properties with sf_avail > 0 and dominant_score_type == tenant_match
    # Snoozed properties are excluded regardless of urgency.
    props = (
        db.query(Property)
        .filter(
            Property.dominant_score_type == "tenant_match",
            Property.sf_avail > 0,
            # Active snooze: snoozed_until IS NULL or snoozed_until <= today (already processed)
            (Property.snoozed_until == None),
        )
        .all()
    )
    # Properties listed for sale with sf_avail > 0 also qualify (for_sale_vacancy)
    for_sale_props = (
        db.query(Property)
        .filter(
            Property.listed_for_sale == True,
            Property.sf_avail > 0,
            (Property.snoozed_until == None),
        )
        .all()
    )
    all_props = {p.id: p for p in props}
    for p in for_sale_props:
        all_props[p.id] = p

    # Companies that could match — snoozed companies excluded.
    # Active snooze: snoozed_until IS NULL or <= today (expired snoozes already
    # auto-cleared to NULL on briefing load). Null-safe: NULL = active.
    companies = db.query(Company).filter(
        Company.estimated_sf_needed.isnot(None),
        Company.estimated_sf_needed > 0,
        (Company.snoozed_until == None),
    ).all()

    # Build set of (property_id_int, company_id_int) contacted pairs
    contacted = set()
    logs = db.query(OutreachLog).filter(
        OutreachLog.marked_contacted == True,
    ).all()
    for lg in logs:
        if lg.property_id and lg.company_id:
            contacted.add((lg.property_id, lg.company_id))

    actions: list = []
    for prop in all_props.values():
        avail_sf = prop.sf_avail or 0
        if avail_sf <= 0:
            continue
        for co in companies:
            sf_needed = co.estimated_sf_needed or 0
            if sf_needed <= 0:
                continue
            match = compute_match(
                tenant_submarket=co.current_submarket,
                property_submarket=prop.submarket,
                tenant_class=getattr(co, "current_building_class", None),
                property_class=prop.asset_class,
                sf_needed=sf_needed,
                sf_avail=avail_sf,
            )
            if match is None:
                continue
            score = match["score"]

            # Determine outreach type + target
            if prop.listed_for_sale:
                outreach_type = "for_sale_vacancy"
            else:
                outreach_type = "tenant_match"
            target_type = "broker" if prop.landlord_representative else "owner"

            # Contact status
            if (prop.id, co.id) in contacted:
                contact_status = "CONTACTED"
            else:
                contact_status = "NOT_CONTACTED"

            actions.append(TenantMatchAction(
                property_id=prop.property_id,
                address=prop.address,
                submarket=prop.submarket,
                sf_avail=prop.sf_avail,
                landlord_representative=prop.landlord_representative,
                listed_for_sale=bool(prop.listed_for_sale),
                outreach_type=outreach_type,
                target_type=target_type,
                tenant_company_id=co.company_id,
                tenant_name=co.name,
                tenant_industry=co.industry,
                tenant_headcount=co.current_headcount,
                tenant_sf_needed=sf_needed,
                match_score=round(score, 1),
                adjacent_submarket=match["adjacent"],
                lease_expiry_months=co.lease_expiry_months,
                contact_status=contact_status,
            ))

    # Deduplicate: keep only the highest-scoring tenant per property so each
    # property shows exactly one row (its best match) in Section A.
    best_per_prop: dict = {}
    for action in actions:
        pid = action.property_id
        if pid not in best_per_prop or action.match_score > best_per_prop[pid].match_score:
            best_per_prop[pid] = action
    actions = list(best_per_prop.values())

    # Sort: match_score DESC — the scoring curve already encodes expiry weight
    actions.sort(key=lambda a: -a.match_score)
    return actions


def _compute_acquisition_targets(db: Session) -> list:
    """Compute Section B: acquisition target properties. Excludes snoozed properties."""
    props = (
        db.query(Property)
        .filter(
            Property.signal_score >= 40,
            Property.dominant_score_type == "acquisition",
            (Property.snoozed_until == None),
        )
        .order_by(Property.signal_score.desc())
        .all()
    )

    # Build set of property_id_ints contacted for acquisition
    contacted_acq = set()
    logs = db.query(OutreachLog).filter(
        OutreachLog.marked_contacted == True,
        OutreachLog.outreach_type.in_(["acquisition"]),
    ).all()
    for lg in logs:
        if lg.property_id:
            contacted_acq.add(lg.property_id)

    targets = []
    for prop in props:
        contact_status = "CONTACTED" if prop.id in contacted_acq else "NOT_CONTACTED"
        targets.append(AcquisitionTarget(
            property_id=prop.property_id,
            address=prop.address,
            submarket=prop.submarket,
            year_built=prop.year_built,
            total_sf=prop.total_sf or 0,
            vacancy_pct=prop.vacancy_pct,
            signal_score=prop.signal_score or 0.0,
            dominant_signal=_dominant_signal_label(prop),
            asking_price=prop.asking_price,
            estimated_value=prop.asking_price,  # fallback to asking_price
            target_type="sales_broker" if prop.sales_contact else "owner",
            owner_name=prop.owner_name or "",
            sales_contact=prop.sales_contact,
            contact_status=contact_status,
        ))
    return targets


def _compute_expired_leases(db: Session) -> list:
    """Companies whose lease_expiry_date is today or in the past."""
    today = date.today()
    companies = (
        db.query(Company)
        .filter(
            Company.lease_expiry_date.isnot(None),
            Company.lease_expiry_date <= today,
            # Exclude snoozed companies (null-safe: NULL = active)
            (Company.snoozed_until == None),
        )
        .order_by(Company.lease_expiry_date.asc())
        .all()
    )
    return [
        ExpiredLease(
            company_id=co.company_id,
            name=co.name,
            industry=co.industry,
            sf_needed=co.estimated_sf_needed,
            submarket=co.current_submarket,
            headcount=co.current_headcount,
        )
        for co in companies
    ]


def _to_call_target(opp: Opportunity, rank: int) -> CallTarget:
    prop = opp.property
    company = opp.company

    return CallTarget(
        rank=rank,
        opportunity_id=opp.opportunity_id,
        deal_type=opp.deal_type,
        priority=opp.priority,
        score=opp.score,
        confidence_level=opp.confidence_level,
        property_id=prop.property_id if prop else None,
        company_id=company.company_id if company else None,
        property_address=prop.address if prop else None,
        property_submarket=prop.submarket if prop else None,
        company_name=company.name if company else None,
        owner_name=prop.owner_name if prop else None,
        thesis=opp.thesis,
        next_action=opp.next_action,
        call_script=opp.call_script,
        estimated_commission=opp.estimated_commission,
    )


def generate_daily_briefing(db: Session) -> DailyBriefing:
    """
    Produces the daily operational briefing from the current database state.
    On every load, expired snoozes are auto-cleared and the returned property
    IDs are included in returned_from_snooze_property_ids for badge display.
    """
    # ── Auto-clear expired snoozes ─────────────────────────────────────────
    returned_from_snooze_ids = _process_snooze_returns(db)
    # Companies whose snooze has expired return to the queue automatically.
    _process_company_snooze_returns(db)

    # ── Immediate deals (all types, sorted by score desc) ──────────────────
    immediate = (
        db.query(Opportunity)
        .filter(Opportunity.priority == "IMMEDIATE", Opportunity.is_active == True)
        .order_by(Opportunity.score.desc())
        .limit(10)
        .all()
    )

    # ── High-priority deals ────────────────────────────────────────────────
    high_priority = (
        db.query(Opportunity)
        .filter(Opportunity.priority == "HIGH", Opportunity.is_active == True)
        .order_by(Opportunity.score.desc())
        .limit(10)
        .all()
    )

    # ── Pre-market predictions ──────────────────────────────────────────────
    pre_market = (
        db.query(Opportunity)
        .filter(
            Opportunity.deal_type == "PRE_MARKET",
            Opportunity.priority.in_(["IMMEDIATE", "HIGH"]),
            Opportunity.is_active == True,
        )
        .order_by(Opportunity.score.desc())
        .limit(5)
        .all()
    )

    # ── Tenant-driven opportunities ─────────────────────────────────────────
    tenant_driven = (
        db.query(Opportunity)
        .filter(
            Opportunity.deal_type == "TENANT_DRIVEN",
            Opportunity.priority.in_(["IMMEDIATE", "HIGH"]),
            Opportunity.is_active == True,
        )
        .order_by(Opportunity.score.desc())
        .limit(5)
        .all()
    )

    # ── Tenant match properties ────────────────────────────────────────────
    tenant_match_props = (
        db.query(Property)
        .filter(Property.dominant_score_type == "tenant_match")
        .order_by(Property.tenant_match_score.desc())
        .limit(10)
        .all()
    )
    tenant_match_targets = [
        TenantMatchTarget(
            rank=i + 1,
            property_id=p.property_id,
            address=p.address,
            submarket=p.submarket,
            asset_class=p.asset_class or "",
            total_sf=p.total_sf or 0,
            owner_name=p.owner_name or "",
            vacancy_pct=p.vacancy_pct,
            sf_avail=p.sf_avail,
            tenant_match_score=p.tenant_match_score or 0.0,
            in_place_rent_psf=p.in_place_rent_psf,
            market_rent_psf=p.market_rent_psf,
        )
        for i, p in enumerate(tenant_match_props)
    ]

    # ── Stats ───────────────────────────────────────────────────────────────
    total_props     = db.query(Property).count()
    total_companies = db.query(Company).count()
    total_opps      = db.query(Opportunity).filter(Opportunity.is_active == True).count()
    immediate_count = db.query(Opportunity).filter(Opportunity.priority == "IMMEDIATE", Opportunity.is_active == True).count()
    high_count      = db.query(Opportunity).filter(Opportunity.priority == "HIGH", Opportunity.is_active == True).count()
    pre_mkt_count   = db.query(Opportunity).filter(Opportunity.deal_type == "PRE_MARKET", Opportunity.is_active == True).count()
    tenant_count    = db.query(Opportunity).filter(Opportunity.deal_type == "TENANT_DRIVEN", Opportunity.is_active == True).count()
    mispriced_count = db.query(Opportunity).filter(Opportunity.deal_type == "ACTIVE_MISPRICED", Opportunity.is_active == True).count()

    all_props = db.query(Property).all()
    avg_pred   = sum(p.prediction_score for p in all_props) / len(all_props) if all_props else 0
    avg_signal = sum(p.signal_score for p in all_props) / len(all_props) if all_props else 0

    stats = DashboardStats(
        total_properties=total_props,
        total_companies=total_companies,
        total_opportunities=total_opps,
        immediate_count=immediate_count,
        high_count=high_count,
        pre_market_count=pre_mkt_count,
        tenant_driven_count=tenant_count,
        active_mispriced_count=mispriced_count,
        avg_prediction_score=round(avg_pred, 1),
        avg_signal_score=round(avg_signal, 1),
    )

    tenant_match_actions = _compute_tenant_actions(db)
    acquisition_targets  = _compute_acquisition_targets(db)
    expired_leases       = _compute_expired_leases(db)

    return DailyBriefing(
        briefing_date=date.today(),
        stats=stats,
        immediate_deals=[_to_call_target(o, i + 1) for i, o in enumerate(immediate[:3])],
        high_priority_deals=[_to_call_target(o, i + 1) for i, o in enumerate(high_priority)],
        pre_market_predictions=[_to_call_target(o, i + 1) for i, o in enumerate(pre_market)],
        tenant_opportunities=[_to_call_target(o, i + 1) for i, o in enumerate(tenant_driven)],
        tenant_match_properties=tenant_match_targets,
        tenant_match_actions=tenant_match_actions,
        acquisition_targets=acquisition_targets,
        expired_leases=expired_leases,
        returned_from_snooze_property_ids=returned_from_snooze_ids,
        signal_refresh_timestamp=str(date.today()),
    )
