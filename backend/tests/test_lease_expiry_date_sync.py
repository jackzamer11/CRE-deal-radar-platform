"""
Regression: lease_expiry_months / lease_expiry_date desync.

lease_expiry_months is the column the scoring engine reads directly.
lease_expiry_date is the column the API response schemas (CompanyBase,
CompanyListOut) recompute lease_expiry_months FROM, live, on every read —
so a company's on-screen expiry can be correct while the stored
lease_expiry_months column sits stale or NULL (e.g. a direct data edit
that only touched the date, or any path that sets one field without the
other). When that happens, the 30%-weighted lease_expiry signal used to
silently abstain during scoring even though the UI showed a real value.

Fix: _run_signals() (companies.py) and refresh_company_signals()
(pipeline.py) now re-derive lease_expiry_months from lease_expiry_date via
the same months_until_lease_expiry() helper the display schemas use,
whenever a date is on record — self-healing the column and guaranteeing
the scorer never abstains on a signal the display is already showing.

All in-memory (SQLite StaticPool). No network, no live DB.
"""
from datetime import date

import pytest
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log  # noqa: F401
from app.database import Base
from app.models.company import Company
from app.api.routes.companies import _run_signals
from app.ingestion.pipeline import refresh_company_signals
from app.schemas.company import months_until_lease_expiry


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


def _desynced_company(company_id: str) -> Company:
    """A company with a real lease_expiry_date (peak 6-9mo window) but a
    NULL lease_expiry_months column — the exact desync under test."""
    expiry_date = date.today() + relativedelta(months=6)
    return Company(
        company_id=company_id,
        name="Desync Test Co",
        industry="Technology",
        current_headcount=40,
        # Explicitly confirmed zero open positions — keeps hiring_velocity
        # scoring (not abstaining) so this test's signals_scored_count isn't
        # incidentally coupled to open_positions null-handling semantics.
        open_positions=0,
        current_submarket="Tysons",
        tenant_representative="JLL",
        lease_expiry_date=expiry_date,
        lease_expiry_months=None,   # ** the desync **
    )


def test_months_until_lease_expiry_matches_six_month_date():
    # Sanity: the shared helper used by both the display schemas and the
    # scoring path agrees with a straightforward relativedelta(months=6).
    expiry_date = date.today() + relativedelta(months=6)
    assert months_until_lease_expiry(expiry_date) == 6


def test_run_signals_derives_months_from_date_when_column_is_null(db_session):
    """_run_signals() (companies.py) must not abstain on lease_expiry when
    lease_expiry_date is set but lease_expiry_months is NULL."""
    company = _desynced_company("CO-DESYNC-1")
    db_session.add(company)
    db_session.flush()

    _run_signals(company)

    assert company.sig_lease_expiry == 100.0, (
        "lease_expiry signal abstained (persisted as 0.0) despite a valid "
        "lease_expiry_date on record — _run_signals() is reading the stale "
        "lease_expiry_months column instead of deriving from the date"
    )
    # The column self-heals to match what the date implies.
    assert company.lease_expiry_months == 6
    assert company.signals_scored_count >= 3
    assert company.insufficient_data is False


def test_refresh_company_signals_derives_months_from_date_when_column_is_null(db_session):
    """refresh_company_signals() (pipeline.py) — the function behind the daily
    job and used by the deal-creation engine — must have the identical fix."""
    company = _desynced_company("CO-DESYNC-2")
    db_session.add(company)
    db_session.flush()

    refresh_company_signals(db_session, company)

    assert company.sig_lease_expiry == 100.0, (
        "lease_expiry signal abstained (persisted as 0.0) despite a valid "
        "lease_expiry_date on record — refresh_company_signals() is reading "
        "the stale lease_expiry_months column instead of deriving from the date"
    )
    assert company.lease_expiry_months == 6
    assert company.insufficient_data is False


def test_run_signals_still_uses_stored_months_when_no_date_on_record(db_session):
    """Companies with lease_expiry_months set directly (e.g. CoStar import,
    which never populates lease_expiry_date) must be unaffected — the date
    branch is additive, not a replacement for the existing column read."""
    company = Company(
        company_id="CO-NODATE",
        name="No Date Co",
        industry="Technology",
        current_headcount=40,
        current_submarket="Tysons",
        lease_expiry_date=None,
        lease_expiry_months=6,
    )
    db_session.add(company)
    db_session.flush()

    _run_signals(company)

    assert company.lease_expiry_months == 6
    assert company.sig_lease_expiry == 100.0


def test_run_signals_abstains_when_both_date_and_months_are_null(db_session):
    """No date, no months — must still abstain cleanly (null-safe), not raise."""
    company = Company(
        company_id="CO-NOEXPIRY",
        name="No Expiry Co",
        industry="Technology",
        current_headcount=40,
        current_submarket="Tysons",
        lease_expiry_date=None,
        lease_expiry_months=None,
    )
    db_session.add(company)
    db_session.flush()

    _run_signals(company)

    assert company.lease_expiry_months is None
    assert company.sig_lease_expiry == 0.0   # abstain persisted as 0.0, per existing convention
