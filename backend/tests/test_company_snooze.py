"""
Company snooze contract tests.

Guards the company-side snooze feature end to end with fabricated in-memory
inputs only (in-memory SQLite, no live DB, no network):

  (a) A company with snoozed_until in the FUTURE is EXCLUDED from the outreach
      queue (output_engine tenant-match Section A).
  (b) A company with snoozed_until in the PAST or NULL is INCLUDED (the past
      snooze also auto-clears on briefing load and the company returns).
  (c) The /api/companies/ list response the outreach agent reads still contains
      priority, headcount, growth_rate (headcount_growth_pct), lease_expiry_months,
      submarket (current_submarket), score (opportunity_score), company_id — and
      now also exposes snoozed_until so the agent can skip snoozed rows.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log   # noqa: F401 — _compute_tenant_actions queries this
import app.models.outreach_draft # noqa: F401 — registered for completeness
from app.database import Base, get_db
from app.models.company import Company
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


def _make_company(session, *, company_id, snoozed_until=None, submarket="Tysons"):
    """A company that can match the test property in _compute_tenant_actions."""
    co = Company(
        company_id=company_id,
        name=f"Tenant {company_id}",
        industry="Technology",
        current_headcount=50,
        headcount_growth_pct=22.0,
        current_submarket=submarket,
        estimated_sf_needed=5000,
        lease_expiry_months=10,
        opportunity_score=80.0,
        priority="HIGH",
        expansion_signal=True,
        snoozed_until=snoozed_until,
    )
    session.add(co)
    return co


def _make_matching_property(session, *, property_id="NVA-TEST-1", submarket="Tysons"):
    # One property per submarket: Section A dedups to the best match PER property,
    # so each company needs its own property to be independently observable.
    prop = Property(
        property_id=property_id,
        address=f"{property_id} Test Plaza",
        submarket=submarket,
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
    return prop


# ── (a) future snooze → excluded ───────────────────────────────────────────────
def test_future_snooze_excludes_company_from_queue(db_session):
    from app.services.output_engine import _compute_tenant_actions

    _make_matching_property(db_session, property_id="NVA-A", submarket="Tysons")
    _make_matching_property(db_session, property_id="NVA-B", submarket="Reston")
    future = date.today() + timedelta(days=30)
    _make_company(db_session, company_id="CO-ACTIVE", snoozed_until=None, submarket="Tysons")
    _make_company(db_session, company_id="CO-SNOOZED", snoozed_until=future, submarket="Reston")
    db_session.commit()

    actions = _compute_tenant_actions(db_session)
    matched_company_ids = {a.tenant_company_id for a in actions}

    assert "CO-ACTIVE" in matched_company_ids, "Active company must appear in the queue"
    assert "CO-SNOOZED" not in matched_company_ids, (
        "A company snoozed into the future must be EXCLUDED from the outreach queue"
    )


# ── (b) past / NULL snooze → included (and past auto-clears) ────────────────────
def test_past_and_null_snooze_included_in_queue(db_session):
    from app.services.output_engine import (
        _compute_tenant_actions,
        _process_company_snooze_returns,
    )

    _make_matching_property(db_session, property_id="NVA-A", submarket="Tysons")
    _make_matching_property(db_session, property_id="NVA-B", submarket="Reston")
    past = date.today() - timedelta(days=1)
    _make_company(db_session, company_id="CO-NULL", snoozed_until=None, submarket="Tysons")
    co_past = _make_company(db_session, company_id="CO-PAST", snoozed_until=past, submarket="Reston")
    db_session.commit()

    # Briefing load auto-clears expired snoozes and marks them returned.
    returned = _process_company_snooze_returns(db_session)
    assert "CO-PAST" in returned
    db_session.refresh(co_past)
    assert co_past.snoozed_until is None, "Expired snooze must auto-clear to NULL"
    assert co_past.returned_from_snooze is True

    actions = _compute_tenant_actions(db_session)
    matched_company_ids = {a.tenant_company_id for a in actions}
    assert "CO-NULL" in matched_company_ids, "NULL snooze (active) must be INCLUDED"
    assert "CO-PAST" in matched_company_ids, "Past snooze must reappear after expiry"


def test_null_snooze_is_null_safe(db_session):
    """A company with snoozed_until = NULL must never raise and is treated active."""
    from app.services.output_engine import _is_company_snoozed

    co = _make_company(db_session, company_id="CO-NULL2", snoozed_until=None)
    db_session.commit()
    assert _is_company_snoozed(co) is False


# ── (c) /api/companies/ field contract the agent depends on ────────────────────
def test_companies_list_contract_for_agent(db_session):
    from fastapi.testclient import TestClient
    from app.main import app

    _make_company(db_session, company_id="CO-CONTRACT", snoozed_until=None)
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        resp = client.get("/api/companies/")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows, "Expected at least one company row"
        row = next(r for r in rows if r["company_id"] == "CO-CONTRACT")

        # Fields outreach_agent.py reads — must NOT be removed or renamed.
        for field in (
            "company_id",
            "priority",
            "current_headcount",      # headcount
            "headcount_growth_pct",   # growth_rate
            "lease_expiry_months",
            "current_submarket",      # submarket
            "opportunity_score",      # score
        ):
            assert field in row, f"Agent-required field '{field}' missing from /api/companies/"

        # New field the agent uses to skip snoozed companies.
        assert "snoozed_until" in row, "snoozed_until must be exposed for agent filtering"
    finally:
        app.dependency_overrides.clear()
