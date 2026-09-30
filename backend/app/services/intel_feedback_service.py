"""Phase E — feedback loop: disposition capture + standing-rule detection.

Every opportunity disposition (accept/reject/defer) is recorded with its reason.
When the same durable-policy rejection reason recurs, we suggest promoting it to
a standing rule (criteria). v1 matching is exact text, by design.
"""

from datetime import date, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.intel import (
    DISPOSITIONS,
    NOT_A_TENANT,
    REASON_CATEGORIES,
    IntelCriterion,
    IntelFeedback,
    IntelOpportunity,
)

# A durable-policy reason must recur this many times before we suggest a rule.
STANDING_RULE_THRESHOLD = 2

# A deferral with no date comes back after this many days. A deferral with no
# return date at all used to be a rejection in practice.
DEFAULT_DEFER_DAYS = 30


class FeedbackError(ValueError):
    """Invalid disposition input (e.g. missing required reason)."""


def disposition_opportunity(
    db: Session,
    opportunity_id: int,
    disposition: str,
    reason_category: Optional[str] = None,
    reason_text: Optional[str] = None,
    *,
    resurface_at: Optional[date] = None,
    today: Optional[date] = None,
) -> Tuple[IntelOpportunity, Optional[str]]:
    """Record a disposition and update the opportunity status.

    What each decision means over time — see intel_signal_service._blocks:

      * accepted — done for this lease cycle (a requirement card: until the
        tenant says something new).
      * rejected — the same, except reason "not_a_tenant", which is permanent
        and marks the firm as a counterparty so nothing tenant-side is ever
        made for it again.
      * deferred — back on `resurface_at` (default: DEFAULT_DEFER_DAYS out).

    Returns (opportunity, suggested_rule_text). suggested_rule_text is non-None
    only when a durable-policy rejection reason has now recurred enough times to
    propose saving it as a standing rule.

    Raises FeedbackError if disposition is unknown, or if a reject/defer arrives
    without a reason category (accept needs no reason).
    """
    if disposition not in DISPOSITIONS:
        raise FeedbackError(f"Unknown disposition '{disposition}'.")

    if disposition in ("rejected", "deferred") and not reason_category:
        raise FeedbackError(f"A reason category is required to {disposition[:-2]} an opportunity.")

    if reason_category and disposition != "accepted" and reason_category not in REASON_CATEGORIES:
        raise FeedbackError(f"Unknown reason category '{reason_category}'.")

    if reason_category == NOT_A_TENANT and disposition != "rejected":
        raise FeedbackError("'Not a tenant' is a reason to reject, not to defer.")

    today = today or date.today()
    if disposition == "deferred":
        resurface_at = resurface_at or today + timedelta(days=DEFAULT_DEFER_DAYS)
        if resurface_at <= today:
            raise FeedbackError("A deferral has to come back on a future date.")
    else:
        resurface_at = None

    opp = db.query(IntelOpportunity).filter(IntelOpportunity.id == opportunity_id).first()
    if opp is None:
        raise FeedbackError("Opportunity not found.")

    clean_text = (reason_text or "").strip() or None

    feedback = IntelFeedback(
        opportunity_id=opportunity_id,
        disposition=disposition,
        reason_category=reason_category if disposition != "accepted" else None,
        reason_text=clean_text if disposition != "accepted" else None,
        resurface_at=resurface_at,
    )
    db.add(feedback)
    opp.status = disposition
    opp.resurface_at = resurface_at

    if disposition == "rejected" and reason_category == NOT_A_TENANT:
        _mark_not_a_tenant(db, opp)

    db.commit()
    db.refresh(opp)

    suggestion = _maybe_suggest_rule(db, disposition, reason_category, clean_text)
    return opp, suggestion


def _mark_not_a_tenant(db: Session, opp: IntelOpportunity) -> None:
    """A card rejected as "not a tenant" marks its firm a counterparty.

    Goes through the same writer the contact list's "whole firm" button uses,
    so everyone at the firm still unconfirmed takes the type too. A card not
    about a company (a note with no company behind it) has no firm to mark;
    the rejection alone still keeps it from ever coming back. A firm Jack
    already marked is left as he set it.
    """
    if opp.entity_type != "company":
        return
    company = db.query(Company).filter(Company.id == opp.entity_id).first()
    if company is None or company.company_type:
        return
    # Imported here: contact_type_service imports models only, but keeping
    # the feedback service's module-level imports to the intel layer avoids a
    # cycle if that ever changes.
    from app.services.contact_type_service import mark_firm

    mark_firm(db, company, "counterparty")


def _maybe_suggest_rule(
    db: Session,
    disposition: str,
    reason_category: Optional[str],
    reason_text: Optional[str],
) -> Optional[str]:
    """Suggest a standing rule when a durable-policy rejection reason recurs."""
    if disposition != "rejected" or reason_category != "durable_policy" or not reason_text:
        return None

    # Already saved as a rule? Don't nag.
    existing = (
        db.query(IntelCriterion)
        .filter(IntelCriterion.statement == reason_text, IntelCriterion.active.is_(True))
        .first()
    )
    if existing:
        return None

    count = (
        db.query(IntelFeedback)
        .filter(
            IntelFeedback.disposition == "rejected",
            IntelFeedback.reason_category == "durable_policy",
            IntelFeedback.reason_text == reason_text,
        )
        .count()
    )
    return reason_text if count >= STANDING_RULE_THRESHOLD else None


def save_criterion(db: Session, statement: str, criterion_type: Optional[str] = None) -> IntelCriterion:
    """Persist a standing rule. Exact-text idempotent among active rules."""
    statement = statement.strip()
    existing = (
        db.query(IntelCriterion)
        .filter(IntelCriterion.statement == statement, IntelCriterion.active.is_(True))
        .first()
    )
    if existing:
        return existing
    criterion = IntelCriterion(statement=statement, criterion_type=criterion_type, active=True)
    db.add(criterion)
    db.commit()
    db.refresh(criterion)
    return criterion
