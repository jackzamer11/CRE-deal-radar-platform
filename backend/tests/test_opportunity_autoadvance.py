"""
Opportunity auto-advance contract tests.

Wires "Save & Mark Contacted" to the deal pipeline: marking an outreach log
contacted (PATCH /outreach-log/{id} with marked_contacted=True) advances the
matching Opportunity IDENTIFIED -> CONTACTED.

All inputs are fabricated in-memory (in-memory SQLite, no live DB / network).
Tests exercise the REAL PATCH endpoint via TestClient so the save-contacted
path runs end to end (same transaction as the log update).

Guards:
  (a) marking contacted advances a matching IDENTIFIED Opportunity to CONTACTED.
  (b) a deal already at a later stage (ACTIVE) is NOT regressed.
  (c) calling the save-contacted path twice is idempotent — ends CONTACTED, no
      error, no duplicate Opportunity created.
  (d) if no Opportunity exists, one is created at CONTACTED rather than raising.
  (e) a null/malformed stage value is treated as advanceable to CONTACTED.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.opportunity import Opportunity
from app.models.outreach_log import OutreachLog
from app.models.property import Property


# ── In-memory DB fixture ───────────────────────────────────────────────────────
@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ── Builders ────────────────────────────────────────────────────────────────
def _make_property(session, *, property_id="NVA-AA-1"):
    prop = Property(
        property_id=property_id,
        address=f"{property_id} Test Plaza",
        submarket="Tysons",
        total_sf=50000,
        year_built=2005,
        owner_name="Test Owner LLC",
        in_place_rent_psf=38.0,
        market_rent_psf=39.0,
        market_cap_rate=6.5,
        sf_avail=5000,
        dominant_score_type="tenant_match",
        listed_for_sale=False,
    )
    session.add(prop)
    session.flush()  # assign prop.id
    return prop


def _make_outreach_log(session, *, property_id):
    log = OutreachLog(
        property_id=property_id,
        company_id=None,
        outreach_type="tenant_match",
        email_subject="Subject",
        email_body="Body",
        call_script_opening="Opening.",
        call_script_core="Core.",
        call_script_pain_probe="Probe?",
        call_script_close="Close.",
        score_at_generation=80.0,
        priority_at_generation="HIGH",
    )
    session.add(log)
    session.flush()  # assign log.id
    return log


def _make_opportunity(session, *, property_id, stage):
    opp = Opportunity(
        opportunity_id=f"OPP-{property_id}",
        deal_type="PRE_MARKET",
        opportunity_category="LANDLORD_REP",
        property_id=property_id,
        score=50.0,
        confidence_level="MEDIUM",
        priority="HIGH",
        thesis="Test thesis.",
        next_action="Test next action.",
        stage=stage,
    )
    session.add(opp)
    session.flush()
    return opp


def _mark_contacted(client, log_id):
    return client.patch(f"/api/outreach-log/{log_id}", json={"marked_contacted": True})


# ── (a) IDENTIFIED -> CONTACTED on mark-contacted ──────────────────────────────
def test_mark_contacted_advances_identified_to_contacted(client, db_session):
    prop = _make_property(db_session)
    log = _make_outreach_log(db_session, property_id=prop.id)
    opp = _make_opportunity(db_session, property_id=prop.id, stage="IDENTIFIED")
    db_session.commit()

    resp = _mark_contacted(client, log.id)
    assert resp.status_code == 200, resp.text

    db_session.refresh(opp)
    assert opp.stage == "CONTACTED"


# ── (b) never regress a later stage ────────────────────────────────────────────
@pytest.mark.parametrize("later_stage", ["ACTIVE", "UNDER_LOI", "CLOSED", "DEAD"])
def test_later_stage_is_not_regressed(client, db_session, later_stage):
    prop = _make_property(db_session)
    log = _make_outreach_log(db_session, property_id=prop.id)
    opp = _make_opportunity(db_session, property_id=prop.id, stage=later_stage)
    db_session.commit()

    resp = _mark_contacted(client, log.id)
    assert resp.status_code == 200, resp.text

    db_session.refresh(opp)
    assert opp.stage == later_stage, f"{later_stage} must not regress to CONTACTED"


# ── (c) idempotent: twice ends CONTACTED, no duplicate, no error ───────────────
def test_mark_contacted_is_idempotent(client, db_session):
    prop = _make_property(db_session)
    log = _make_outreach_log(db_session, property_id=prop.id)
    opp = _make_opportunity(db_session, property_id=prop.id, stage="IDENTIFIED")
    db_session.commit()

    assert _mark_contacted(client, log.id).status_code == 200
    assert _mark_contacted(client, log.id).status_code == 200

    db_session.refresh(opp)
    assert opp.stage == "CONTACTED"
    # No duplicate Opportunity for this property.
    count = db_session.query(Opportunity).filter(Opportunity.property_id == prop.id).count()
    assert count == 1, "Second click must not create a duplicate Opportunity"


# ── (d) missing Opportunity → created at CONTACTED, no 500 ──────────────────────
def test_missing_opportunity_is_created_at_contacted(client, db_session):
    prop = _make_property(db_session)
    log = _make_outreach_log(db_session, property_id=prop.id)
    db_session.commit()
    assert db_session.query(Opportunity).filter(Opportunity.property_id == prop.id).count() == 0

    resp = _mark_contacted(client, log.id)
    assert resp.status_code == 200, resp.text

    opps = db_session.query(Opportunity).filter(Opportunity.property_id == prop.id).all()
    assert len(opps) == 1, "A missing Opportunity must be created (not raise)"
    assert opps[0].stage == "CONTACTED"


# ── (e) null / malformed stage → advanceable to CONTACTED ──────────────────────
@pytest.mark.parametrize("bad_stage", [None, "", "   ", "weird_value"])
def test_null_or_malformed_stage_advances(client, db_session, bad_stage):
    prop = _make_property(db_session)
    log = _make_outreach_log(db_session, property_id=prop.id)
    opp = _make_opportunity(db_session, property_id=prop.id, stage=bad_stage)
    db_session.commit()

    resp = _mark_contacted(client, log.id)
    assert resp.status_code == 200, resp.text

    db_session.refresh(opp)
    assert opp.stage == "CONTACTED"
