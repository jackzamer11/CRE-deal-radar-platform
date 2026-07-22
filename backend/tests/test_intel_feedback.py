"""Phase E — feedback loop contract."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.models.outreach_log  # noqa: F401 — Property relationship mapping
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.intel import IntelCriterion, IntelFeedback, IntelOpportunity
from app.services.intel_feedback_service import (
    FeedbackError,
    disposition_opportunity,
    save_criterion,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _make_opp(db, entity_id=1, dedup="company:1:lease_expiring") -> IntelOpportunity:
    opp = IntelOpportunity(
        title="Lease expiring — Test", entity_type="company", entity_id=entity_id,
        score=120.0, rationale="…", signals_json="[]", dedup_key=dedup, status="open",
    )
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return opp


def test_accept_needs_no_reason_and_updates_status(db):
    opp = _make_opp(db)
    result, suggestion = disposition_opportunity(db, opp.id, "accepted")
    assert result.status == "accepted"
    assert suggestion is None
    fb = db.query(IntelFeedback).filter_by(opportunity_id=opp.id).one()
    assert fb.disposition == "accepted"
    assert fb.reason_category is None  # accept records no reason


@pytest.mark.parametrize("disposition", ["rejected", "deferred"])
def test_reject_and_defer_require_reason(db, disposition):
    opp = _make_opp(db)
    with pytest.raises(FeedbackError):
        disposition_opportunity(db, opp.id, disposition, reason_category=None)
    # Opportunity remains open when the disposition is rejected for missing reason.
    db.refresh(opp)
    assert opp.status == "open"


def test_disposition_stored_and_moves_out_of_open(db):
    opp = _make_opp(db)
    disposition_opportunity(db, opp.id, "rejected",
                            reason_category="timing", reason_text="Too early")
    db.refresh(opp)
    assert opp.status == "rejected"
    fb = db.query(IntelFeedback).filter_by(opportunity_id=opp.id).one()
    assert fb.reason_category == "timing"
    assert fb.reason_text == "Too early"


def test_standing_rule_suggested_after_two_matching_durable_rejections(db):
    reason = "We never represent tenants under 5,000 SF"
    opp1 = _make_opp(db, entity_id=1, dedup="company:1:lease_expiring")
    opp2 = _make_opp(db, entity_id=2, dedup="company:2:lease_expiring")

    _, s1 = disposition_opportunity(db, opp1.id, "rejected",
                                    reason_category="durable_policy", reason_text=reason)
    assert s1 is None  # first occurrence: no suggestion yet

    _, s2 = disposition_opportunity(db, opp2.id, "rejected",
                                    reason_category="durable_policy", reason_text=reason)
    assert s2 == reason  # second matching occurrence: suggest saving as a rule


def test_different_reasons_do_not_trigger_suggestion(db):
    opp1 = _make_opp(db, entity_id=1, dedup="a")
    opp2 = _make_opp(db, entity_id=2, dedup="b")
    disposition_opportunity(db, opp1.id, "rejected",
                            reason_category="durable_policy", reason_text="Reason A")
    _, s = disposition_opportunity(db, opp2.id, "rejected",
                                   reason_category="durable_policy", reason_text="Reason B")
    assert s is None


def test_save_criterion_is_exact_text_idempotent(db):
    c1 = save_criterion(db, "No sub-5k SF tenants", "durable_policy")
    c2 = save_criterion(db, "No sub-5k SF tenants", "durable_policy")
    assert c1.id == c2.id
    assert db.query(IntelCriterion).count() == 1


def test_no_suggestion_once_rule_already_saved(db):
    reason = "No industrial deals"
    save_criterion(db, reason, "durable_policy")
    opp1 = _make_opp(db, entity_id=1, dedup="a")
    opp2 = _make_opp(db, entity_id=2, dedup="b")
    disposition_opportunity(db, opp1.id, "rejected",
                            reason_category="durable_policy", reason_text=reason)
    _, s = disposition_opportunity(db, opp2.id, "rejected",
                                   reason_category="durable_policy", reason_text=reason)
    assert s is None  # already a standing rule — don't nag
