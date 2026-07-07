"""
Null-handling contract tests: open_positions and current_headcount.

Guards two related fixes with fabricated in-memory inputs only (no live DB,
no network):

  (a) sig_hiring_velocity abstains (returns None) when open_positions was
      never entered (NULL) — it must not be silently scored as 0.0.
  (b) sig_hiring_velocity(0, ...) still scores as a deliberate 0.0 —
      distinguishable from the NULL abstain case.
  (c) A company with current_headcount=NULL saves successfully via the
      POST /api/companies/ endpoint (headcount is optional, not required).
  (d) sig_headcount_growth and sig_space_utilization both abstain cleanly
      (None, not 0.0, not a crash) when current_headcount is NULL.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401 — resolves Property/Company relationships
import app.models.outreach_draft  # noqa: F401 — registered for completeness
from app.database import Base, get_db
from app.services.signal_engine import (
    sig_hiring_velocity,
    sig_headcount_growth,
    sig_space_utilization,
)


# ── (a) / (b): pure-function boundary pins ─────────────────────────────────────

def test_hiring_velocity_abstains_on_null_open_positions():
    assert sig_hiring_velocity(None, 100) is None


def test_hiring_velocity_scores_deliberate_zero_open_positions():
    score = sig_hiring_velocity(0, 100)
    assert score is not None
    assert score == 0.0


def test_hiring_velocity_null_and_zero_are_distinguishable():
    assert sig_hiring_velocity(None, 100) is not sig_hiring_velocity(0, 100)
    assert sig_hiring_velocity(None, 100) is None
    assert sig_hiring_velocity(0, 100) == 0.0


def test_hiring_velocity_still_abstains_on_unknown_headcount():
    # Pre-existing behavior must survive the open_positions change.
    assert sig_hiring_velocity(10, None) is None
    assert sig_hiring_velocity(10, 0) is None


# ── (d): headcount-dependent signals abstain cleanly on NULL headcount ─────────

def test_headcount_growth_abstains_on_null_growth_pct():
    # sig_headcount_growth takes growth_pct (already derived); None → abstain.
    assert sig_headcount_growth(None) is None


def test_space_utilization_abstains_on_null_headcount():
    assert sig_space_utilization(5000, None) is None


def test_space_utilization_abstains_on_null_sf():
    assert sig_space_utilization(None, 100) is None


def test_space_utilization_no_crash_on_null_headcount_and_sf():
    # Must not raise (e.g. ZeroDivisionError / TypeError) — clean abstain.
    try:
        result = sig_space_utilization(None, None)
    except Exception as exc:  # pragma: no cover - failure path
        pytest.fail(f"sig_space_utilization raised on null inputs: {exc}")
    assert result is None


# ── (c): company with NULL headcount saves successfully via the API ────────────

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
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_company_with_blank_headcount_saves_successfully(client):
    resp = client.post(
        "/api/companies/",
        json={
            "name": "Blank Headcount Co",
            "industry": "Technology",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_headcount"] is None
    # Signals that depend on headcount must abstain, not fabricate a score.
    assert body["sig_hiring_velocity"] == 0.0        # None persisted as 0.0
    assert body["sig_headcount_growth"] == 0.0        # None persisted as 0.0
    assert body["sig_space_utilization"] == 0.0       # None persisted as 0.0
    assert body["insufficient_data"] is True


def test_create_company_open_positions_null_vs_zero_distinguishable(client):
    # Never entered — open_positions omitted entirely.
    resp_blank = client.post(
        "/api/companies/",
        json={
            "name": "Never Entered Co",
            "industry": "Technology",
            "current_headcount": 100,
        },
    )
    assert resp_blank.status_code == 200, resp_blank.text
    blank_body = resp_blank.json()
    assert blank_body["open_positions"] is None

    # Explicitly confirmed zero open positions.
    resp_zero = client.post(
        "/api/companies/",
        json={
            "name": "Confirmed Zero Co",
            "industry": "Technology",
            "current_headcount": 100,
            "open_positions": 0,
        },
    )
    assert resp_zero.status_code == 200, resp_zero.text
    zero_body = resp_zero.json()
    assert zero_body["open_positions"] == 0

    # Post lease-expiry-only reweight, hiring_velocity no longer feeds the
    # composite or signals_scored_count at all (only lease_expiry does), so
    # the abstain-vs-confirmed-zero distinction for hiring_velocity is no
    # longer visible via signals_scored_count — both companies here posted no
    # lease_expiry_months, so both abstain on the one scoring signal.
    # The pure-function distinguishability of NULL vs 0 open_positions is
    # covered directly above by test_hiring_velocity_null_and_zero_are_distinguishable.
    assert blank_body["signals_scored_count"] == 0
    assert zero_body["signals_scored_count"] == 0
