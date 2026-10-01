"""What happened after Jack accepted an Intel card.

The decisions are recorded already; this reads what FOLLOWED them off the
timeline, so over a year or two it can say which kinds of card actually turn
into conversations and deals — evidence for tuning the weights instead of
guesses. Nothing is stored: every outcome is derived from the Activity Log and
the stage dividers each time it is asked for, so it can never drift from what
happened.
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.contact import Contact
from app.models.intel import IntelFeedback, IntelOpportunity

# How far a relationship got. Not Interested and Dormant are not progress.
STAGE_RANK = {"Sent": 0, "Replied": 1, "Interested": 2, "In Play": 3, "Closed": 4}
# A touch this soon after accepting counts as acting on the card.
ACTED_WITHIN_DAYS = 14


def _family(dedup_key: Optional[str]) -> str:
    kind = (dedup_key or "").rsplit(":", 1)[-1]
    return "lease" if kind in ("lease_expiring", "expiration_unverified") else kind


def _decided_on(db: Session, opp: IntelOpportunity) -> date:
    fb = (
        db.query(IntelFeedback)
        .filter(IntelFeedback.opportunity_id == opp.id)
        .order_by(IntelFeedback.created_at.desc())
        .first()
    )
    when = (fb.created_at if fb else None) or opp.surfaced_at or datetime.utcnow()
    return when.date()


def _people(db: Session, opp: IntelOpportunity) -> Tuple[List[int], Optional[int]]:
    """(contact ids, company id) a card is about."""
    if opp.entity_type == "company":
        ids = [cid for (cid,) in db.query(Contact.id).filter(Contact.company_id == opp.entity_id).all()]
        return ids, opp.entity_id
    log = db.query(ActivityLog).filter(ActivityLog.id == opp.entity_id).first()
    return ([log.contact_id] if log is not None and log.contact_id else []), None


def outcome_for(db: Session, opp: IntelOpportunity) -> Dict[str, object]:
    """What followed one accepted card."""
    since = _decided_on(db, opp)
    contact_ids, company_id = _people(db, opp)
    conditions = []
    if company_id is not None:
        conditions.append(ActivityLog.company_stamp_id == company_id)
    if contact_ids:
        conditions.append(ActivityLog.contact_id.in_(contact_ids))
    out: Dict[str, object] = {
        "decided_on": since, "first_touch": None, "first_touch_channel": None,
        "touches": 0, "best_stage": None, "closed": False,
    }
    if not conditions:
        return out
    from sqlalchemy import or_

    rows = (
        db.query(ActivityLog)
        .filter(or_(*conditions), ActivityLog.log_date >= since,
                ActivityLog.participation.isnot(True))
        .order_by(ActivityLog.log_date.asc(), ActivityLog.id.asc())
        .all()
    )
    best = -1
    for log in rows:
        if log.action_type == "STAGE_CHANGE":
            rank = STAGE_RANK.get(log.stage_to or "", -1)
            if rank > best:
                best, out["best_stage"] = rank, log.stage_to
            continue
        if log.archived:
            continue
        out["touches"] = int(out["touches"]) + 1
        if out["first_touch"] is None:
            out["first_touch"] = log.log_date
            out["first_touch_channel"] = log.channel
    out["closed"] = out["best_stage"] == "Closed"
    return out


def outcomes(db: Session, opps: List[IntelOpportunity]) -> Dict[int, Dict[str, object]]:
    return {opp.id: outcome_for(db, opp) for opp in opps if opp.status == "accepted"}


def results_summary(db: Session) -> List[Dict[str, object]]:
    """Per kind of card: how often accepting it led anywhere."""
    decided = (
        db.query(IntelOpportunity)
        .filter(IntelOpportunity.status.in_(("accepted", "rejected", "deferred")))
        .all()
    )
    by_family: Dict[str, Dict[str, int]] = {}
    for opp in decided:
        fam = by_family.setdefault(_family(opp.dedup_key), {
            "accepted": 0, "rejected": 0, "deferred": 0,
            "acted": 0, "interested": 0, "closed": 0,
        })
        fam[opp.status] += 1
        if opp.status != "accepted":
            continue
        result = outcome_for(db, opp)
        first = result["first_touch"]
        if first and (first - result["decided_on"]).days <= ACTED_WITHIN_DAYS:
            fam["acted"] += 1
        if STAGE_RANK.get(result["best_stage"] or "", -1) >= STAGE_RANK["Interested"]:
            fam["interested"] += 1
        if result["closed"]:
            fam["closed"] += 1
    return [{"family": name, **counts} for name, counts in sorted(by_family.items())]
