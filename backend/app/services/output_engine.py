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
from app.schemas.dashboard import DailyBriefing, DashboardStats, CallTarget


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
    """
    # ── Immediate deals (all types, sorted by score desc) ──────────────────
    immediate = (
        db.query(Opportunity)
        .filter(Opportunity.priority == "IMMEDIATE", Opportunity.is_active == True)
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

    return DailyBriefing(
        briefing_date=date.today(),
        stats=stats,
        immediate_deals=[_to_call_target(o, i + 1) for i, o in enumerate(immediate)],
        pre_market_predictions=[_to_call_target(o, i + 1) for i, o in enumerate(pre_market)],
        tenant_opportunities=[_to_call_target(o, i + 1) for i, o in enumerate(tenant_driven)],
        signal_refresh_timestamp=str(date.today()),
    )
