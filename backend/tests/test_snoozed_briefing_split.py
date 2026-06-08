"""
Snoozed properties power the "Snoozed" toggle views, and are excluded from the
default active queue.

  - A snoozed property appears ONLY in the snoozed=True computation, never in the
    default (active) computation — for both Section A (tenant actions) and
    Section B (acquisition targets).
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.company import Company
from app.models.property import Property
from app.services.output_engine import _compute_tenant_actions, _compute_acquisition_targets


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _property(session, *, snoozed, dominant, signal_score=0.0, listed=False):
    prop = Property(
        property_id="NVA-SNZ",
        address="5 Snooze Plaza",
        submarket="Tysons",
        total_sf=50000,
        year_built=2003,
        owner_name="Owner LLC",
        in_place_rent_psf=35.0,
        market_rent_psf=38.0,
        market_cap_rate=6.5,
        sf_avail=5000,
        dominant_score_type=dominant,
        signal_score=signal_score,
        listed_for_sale=listed,
        snoozed_until=(date.today() + timedelta(days=30)) if snoozed else None,
    )
    session.add(prop)
    return prop


def test_snoozed_tenant_action_only_in_snoozed_view(db_session):
    _property(db_session, snoozed=True, dominant="tenant_match")
    db_session.add(Company(
        company_id="CO-SNZ", name="Snz Co", industry="Technology",
        current_submarket="Tysons", current_sf_occupied=5000,
        lease_expiry_months=10,
    ))
    db_session.commit()

    assert _compute_tenant_actions(db_session, snoozed=False) == []
    snoozed = _compute_tenant_actions(db_session, snoozed=True)
    assert len(snoozed) == 1
    assert snoozed[0].property_id == "NVA-SNZ"


def test_snoozed_acquisition_only_in_snoozed_view(db_session):
    _property(db_session, snoozed=True, dominant="acquisition", signal_score=75.0)
    db_session.commit()

    assert _compute_acquisition_targets(db_session, snoozed=False) == []
    snoozed = _compute_acquisition_targets(db_session, snoozed=True)
    assert len(snoozed) == 1
    assert snoozed[0].property_id == "NVA-SNZ"
