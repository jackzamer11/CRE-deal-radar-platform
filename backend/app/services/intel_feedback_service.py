"""Phase E — feedback loop: disposition capture + standing-rule detection.

Every opportunity disposition (accept/reject/defer) is recorded with its reason.
When the same durable-policy rejection reason recurs, we suggest promoting it to
a standing rule (criteria). v1 matching is exact text, by design.
"""

from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.intel import (
    DISPOSITIONS,
    IntelCriterion,
    IntelFeedback,
    IntelOpportunity,
)

# A durable-policy reason must recur this many times before we suggest a rule.
STANDING_RULE_THRESHOLD = 2


class FeedbackError(ValueError):
    """Invalid disposition input (e.g. missing required reason)."""


def disposition_opportunity(
    db: Session,
    opportunity_id: int,
    disposition: str,
    reason_category: Optional[str] = None,
    reason_text: Optional[str] = None,
) -> Tuple[IntelOpportunity, Optional[str]]:
    """Record a disposition and update the opportunity status.

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

    opp = db.query(IntelOpportunity).filter(IntelOpportunity.id == opportunity_id).first()
    if opp is None:
        raise FeedbackError("Opportunity not found.")

    clean_text = (reason_text or "").strip() or None

    feedback = IntelFeedback(
        opportunity_id=opportunity_id,
        disposition=disposition,
        reason_category=reason_category if disposition != "accepted" else None,
        reason_text=clean_text if disposition != "accepted" else None,
    )
    db.add(feedback)
    opp.status = disposition
    db.commit()
    db.refresh(opp)

    suggestion = _maybe_suggest_rule(db, disposition, reason_category, clean_text)
    return opp, suggestion


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
