"""A tenant who stated what they want is an opportunity on its own.

Before this rule, only `expiration_date` could produce a card — so hundreds of
requirement facts mined from call notes (SF, submarket, budget, timing) drove
nothing whatsoever. A tenant who said "3,000-5,000 SF in Alexandria, needs an
elevator, deciding in Q1" is a live deal whether or not a lease date was ever
captured.
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
from app.models.activity import ActivityLog
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.intel_signal_service import generate_opportunities, generate_with_stats


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


def _note(db, log_id, days_ago=10):
    log = ActivityLog(
        id=log_id,
        log_date=date.today() - timedelta(days=days_ago),
        action_type="CALL",
        action_taken="Spoke with tenant",
    )
    db.add(log)
    db.commit()
    return log


def _facts(db, entity_id, fields, log_id=1):
    for field, value in fields.items():
        db.add(Observation(entity_type="company", entity_id=entity_id, field=field,
                           value=value, confidence=0.9, human_verified=True,
                           verified_by="auto", source_doc=f"activity_log:{log_id}",
                           source_snippet=f"...{value}..."))
    db.commit()


def test_a_specific_requirement_produces_a_card(db):
    _note(db, 1)
    _facts(db, 10, {
        "req_sf_min": "3000",
        "req_sf_max": "5000",
        "req_submarkets": "Alexandria",
        "req_access_needs": "elevator",
        "contact_name": "Hoda Murad",
    })

    generate_opportunities(db)
    opp = db.query(IntelOpportunity).filter_by(dedup_key="company:10:stated_requirement").one()
    assert opp.status == "open"
    assert "Hoda Murad" in opp.title
    # The rationale repeats what they actually said, not a field count.
    assert "3000" in opp.rationale and "Alexandria" in opp.rationale


def test_contact_details_alone_are_not_a_requirement(db):
    _note(db, 1)
    _facts(db, 11, {"contact_name": "Someone", "contact_email": "a@b.com"})

    generate_opportunities(db)
    assert db.query(IntelOpportunity).count() == 0


def test_two_soft_facts_are_not_enough(db):
    """"Arlington" + "office" describes a note, not a requirement — without a
    specific ask this floods the list with everyone ever mentioned."""
    _note(db, 1)
    _facts(db, 12, {"req_submarkets": "Arlington", "req_space_type": "office"})

    generate_opportunities(db)
    assert db.query(IntelOpportunity).count() == 0

    # Add one specific ask and it becomes a real card.
    _facts(db, 12, {"req_sf_min": "2500"})
    generate_opportunities(db)
    assert db.query(IntelOpportunity).filter_by(
        dedup_key="company:12:stated_requirement").count() == 1


def test_a_live_expiration_takes_precedence_over_the_requirement_card(db):
    """The same tenant must not appear twice under two framings — the lease date
    is strictly more actionable."""
    _note(db, 1)
    _facts(db, 13, {"req_sf_min": "3000", "req_submarkets": "Tysons"})
    exp = date.today() + timedelta(days=200)
    db.add(Observation(entity_type="company", entity_id=13, field="expiration_date",
                       value=exp.isoformat(), human_verified=True,
                       source_doc="activity_log:1"))
    db.commit()

    generate_opportunities(db)
    open_keys = {o.dedup_key for o in db.query(IntelOpportunity).filter_by(status="open").all()}
    assert open_keys == {"company:13:lease_expiring"}


def test_a_requirement_never_outranks_a_verified_expiration(db):
    _note(db, 1, days_ago=200)      # maximum staleness bonus
    _facts(db, 14, {
        "req_sf_min": "3000", "req_sf_max": "5000", "req_budget_max_psf": "$45",
        "req_timing": "Q1", "req_must_haves": "elevator", "req_submarkets": "Reston",
    })
    exp = date.today() + timedelta(days=210)   # maximum timing bonus
    db.add(Observation(entity_type="company", entity_id=15, field="expiration_date",
                       value=exp.isoformat(), human_verified=True, source_doc="lease.pdf"))
    db.commit()

    generate_opportunities(db)
    # Select by signal: the seeded lease document also raises stale_data for #15.
    scores = {o.dedup_key: o.score for o in db.query(IntelOpportunity).all()}
    assert scores["company:15:lease_expiring"] > scores["company:14:stated_requirement"]


def test_staler_requirements_rank_above_fresh_ones(db):
    _note(db, 1, days_ago=1)
    _note(db, 2, days_ago=150)
    _facts(db, 16, {"req_sf_min": "3000", "req_submarkets": "Reston"}, log_id=1)
    _facts(db, 17, {"req_sf_min": "3000", "req_submarkets": "Reston"}, log_id=2)

    generate_opportunities(db)
    scores = {o.dedup_key: o.score for o in db.query(IntelOpportunity).all()}
    # The forgotten one surfaces first.
    assert scores["company:17:stated_requirement"] > scores["company:16:stated_requirement"]


def test_regenerating_creates_no_duplicates(db):
    _note(db, 1)
    _facts(db, 18, {"req_sf_min": "3000", "req_submarkets": "Reston"})

    generate_opportunities(db)
    generate_opportunities(db)
    generate_opportunities(db)
    assert db.query(IntelOpportunity).count() == 1


def test_generate_reports_what_it_scanned(db):
    """An empty run has to be explainable — 748 facts in, blank screen out was
    indistinguishable from a broken button."""
    _note(db, 1)
    _facts(db, 19, {"req_sf_min": "3000", "req_submarkets": "Reston"})
    db.add(Observation(entity_type="company", entity_id=20, field="expiration_date",
                       value="whenever they get around to it", human_verified=True,
                       source_doc="activity_log:1"))
    db.add(Observation(entity_type="company", entity_id=21, field="expiration_date",
                       value=(date.today() - timedelta(days=30)).isoformat(),
                       human_verified=True, source_doc="activity_log:1"))
    db.commit()

    _, stats = generate_with_stats(db)
    assert stats["facts_scanned"] == 4
    assert stats["expirations_found"] == 2
    assert stats["expirations_unreadable"] == 1
    assert stats["expirations_past"] == 1
    assert stats["by_signal_type"] == {"stated_requirement": 1}
