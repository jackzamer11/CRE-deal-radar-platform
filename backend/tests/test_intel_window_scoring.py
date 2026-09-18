"""Timing points peak across the 6-9 month pre-expiry window.

The engine originally scored "sooner is better", peaking on the expiry date
itself. That is backwards for this business: a lease expiring in three weeks
belongs to a tenant who has already re-signed or already has competing brokers
on them. The tenant worth calling today expires in roughly seven months.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all tables on Base.metadata
import app.models.outreach_log  # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.intel_signal_service import (
    SIGNAL_BASE_WEIGHT,
    URGENCY_MAX,
    _window_bonus,
    generate_opportunities,
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


def _expiry(db, entity_id, days, *, verified=True):
    exp = date.today() + timedelta(days=days)
    db.add(Observation(entity_type="company", entity_id=entity_id,
                       field="expiration_date", value=exp.isoformat(),
                       confidence=0.9, human_verified=verified,
                       source_doc="lease.pdf", source_page=2))
    db.commit()


def _expiry_opps(db):
    """Expiration cards only, keyed by entity.

    A seeded lease document also raises the (unrelated, pre-existing) stale_data
    card for the same entity, so the signal type has to be selected explicitly.
    """
    return {
        o.entity_id: o
        for o in db.query(IntelOpportunity).all()
        if "expir" in (o.dedup_key or "")
    }


def _expiry_scores(db):
    return {eid: o.score for eid, o in _expiry_opps(db).items()}


def test_the_window_scores_higher_than_either_edge():
    seven_months = _window_bonus(210)
    assert seven_months == URGENCY_MAX
    assert seven_months > _window_bonus(20)    # already too late
    assert seven_months > _window_bonus(350)   # still too early


def test_full_points_across_the_whole_band():
    assert _window_bonus(180) == URGENCY_MAX
    assert _window_bonus(225) == URGENCY_MAX
    assert _window_bonus(270) == URGENCY_MAX


def test_points_taper_on_both_sides_and_never_exceed_the_cap():
    for days in range(0, 400, 5):
        assert 0 <= _window_bonus(days) <= URGENCY_MAX
    # Ramping up toward the band.
    assert _window_bonus(60) < _window_bonus(120) < _window_bonus(180)
    # Tapering off past it.
    assert _window_bonus(280) > _window_bonus(320) > _window_bonus(360)
    # Inside a month and a half, timing adds nothing.
    assert _window_bonus(10) == 0


def test_a_seven_month_lease_outranks_a_three_week_one(db):
    _expiry(db, 1, 210)   # in the window
    _expiry(db, 2, 21)    # about to expire — too late to get ahead of
    generate_opportunities(db)

    scores = _expiry_scores(db)
    assert scores[1] > scores[2]


def test_verified_still_beats_unverified_at_every_distance(db):
    """The base-weight gap must stay wider than the timing bonus — otherwise a
    guess could outrank a confirmed fact just by being better timed."""
    assert SIGNAL_BASE_WEIGHT["lease_expiring"] - SIGNAL_BASE_WEIGHT["expiration_unverified"] > URGENCY_MAX

    _expiry(db, 1, 20, verified=True)     # worst possible timing, but verified
    _expiry(db, 2, 210, verified=False)   # perfect timing, but a guess
    generate_opportunities(db)

    opps = _expiry_opps(db)
    assert opps[1].score > opps[2].score
    assert opps[1].dedup_key.endswith("lease_expiring")
    assert opps[2].dedup_key.endswith("expiration_unverified")
