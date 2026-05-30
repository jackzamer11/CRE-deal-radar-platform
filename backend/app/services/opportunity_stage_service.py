"""
Opportunity stage auto-advancement.

Wires the "Save & Mark Contacted" outreach action to the deal pipeline: when an
outreach is marked contacted, the matching Opportunity advances
IDENTIFIED -> CONTACTED automatically.

Contract:
  - Only advance a deal still at IDENTIFIED (or with a null/malformed stage).
  - NEVER regress a deal already at CONTACTED/ACTIVE/UNDER_LOI/CLOSED/DEAD.
  - Idempotent: re-running on an already-CONTACTED deal is a no-op.
  - Missing Opportunity -> create one at CONTACTED (defensive; must not 500).

The caller owns the transaction. This function only mutates / adds to the
session; it does NOT commit. That keeps the log update and the stage update a
single atomic commit at the call site.
"""
import logging
import uuid

from sqlalchemy.orm import Session

from app.models.opportunity import Opportunity

logger = logging.getLogger(__name__)

STAGE_IDENTIFIED = "IDENTIFIED"
STAGE_CONTACTED = "CONTACTED"

# Stages at or beyond CONTACTED — reaching any of these means the deal must not
# be regressed back to CONTACTED. DEAD is terminal and is also never regressed.
_NON_REGRESS_STAGES = frozenset(
    {"CONTACTED", "ACTIVE", "UNDER_LOI", "CLOSED", "DEAD"}
)


def advance_opportunity_to_contacted(
    db: Session,
    *,
    property_id=None,
    company_id=None,
) -> Opportunity:
    """Advance the Opportunity matching this property/company to CONTACTED.

    Matches on whichever of property_id / company_id is provided (both are the
    integer FKs shared by OutreachLog and Opportunity). Returns the affected
    Opportunity (existing-and-advanced, existing-and-unchanged, or newly created).

    Does not commit — the caller's transaction persists the change atomically.
    """
    if property_id is None and company_id is None:
        # Nothing to key on; defensively no-op rather than scanning the table.
        logger.warning(
            "advance_opportunity_to_contacted called with no property_id/company_id"
        )
        return None

    query = db.query(Opportunity)
    if property_id is not None:
        query = query.filter(Opportunity.property_id == property_id)
    if company_id is not None:
        query = query.filter(Opportunity.company_id == company_id)
    opp = query.first()

    if opp is None:
        # Defensive create — normal path is that the row exists, but a missing
        # row must not 500. Populate NOT NULL columns with neutral placeholders.
        opp = _create_contacted_opportunity(
            db, property_id=property_id, company_id=company_id
        )
        logger.info(
            "Auto-created Opportunity %s at CONTACTED (property_id=%s, company_id=%s)",
            opp.opportunity_id, property_id, company_id,
        )
        return opp

    current = (opp.stage or "").strip().upper()
    if current in _NON_REGRESS_STAGES:
        # Already CONTACTED or further along — leave it (idempotent / no regress).
        return opp

    # IDENTIFIED, null, empty, or any unrecognized/malformed value -> CONTACTED.
    opp.stage = STAGE_CONTACTED
    return opp


def _create_contacted_opportunity(
    db: Session,
    *,
    property_id,
    company_id,
) -> Opportunity:
    """Create a minimal valid Opportunity at stage CONTACTED.

    Used only when no matching Opportunity exists. Fills every NOT NULL column
    with a neutral placeholder so the insert cannot fail on a constraint.
    """
    is_tenant = company_id is not None
    opp = Opportunity(
        opportunity_id=f"OPP-AUTO-{uuid.uuid4().hex[:12]}",
        deal_type="TENANT_DRIVEN" if is_tenant else "PRE_MARKET",
        opportunity_category="TENANT_REP" if is_tenant else "LANDLORD_REP",
        property_id=property_id,
        company_id=company_id,
        score=0.0,
        confidence_level="LOW",
        priority="WORKABLE",
        thesis="Auto-created from outreach marked contacted.",
        next_action="Follow up on outreach.",
        stage=STAGE_CONTACTED,
        is_active=True,
    )
    db.add(opp)
    return opp
