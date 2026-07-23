"""Phase D — signal engine + weekly opportunities contract."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all tables on Base.metadata
import app.models.outreach_log  # noqa: F401 — needed for Property relationship mapping
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.intel_signal_service import _parse_date, generate_opportunities


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


def _complete_lease(db, entity_id, expiration: date, *, expiration_verified: bool):
    """Seed all five core fields for an entity so stale_data does not fire;
    only the expiration's verified flag varies."""
    fields = {
        "tenant_name": f"Tenant {entity_id}",
        "premises_sqft": "10000",
        "commencement_date": "2020-01-01",
        "base_rent_annual": "500000",
    }
    for field, value in fields.items():
        db.add(Observation(entity_type="company", entity_id=entity_id, field=field,
                           value=value, confidence=0.9, human_verified=True,
                           source_doc="lease.pdf", source_page=1))
    db.add(Observation(entity_type="company", entity_id=entity_id, field="expiration_date",
                       value=expiration.isoformat(), confidence=0.9,
                       human_verified=expiration_verified,
                       source_doc="lease.pdf", source_page=2))
    db.commit()


def test_parse_date_handles_natural_language_from_extractor():
    # The extractor stores the model's verbatim date; the signal engine must
    # parse the natural-language forms it commonly returns, not just ISO.
    from datetime import date as _d
    assert _parse_date("2027-01-17") == _d(2027, 1, 17)
    assert _parse_date("January 17, 2027") == _d(2027, 1, 17)
    assert _parse_date("Jan 17, 2027") == _d(2027, 1, 17)
    assert _parse_date("17 January 2027") == _d(2027, 1, 17)
    assert _parse_date("01/17/2027") == _d(2027, 1, 17)
    assert _parse_date("not a date") is None
    assert _parse_date(None) is None


def test_lease_expiring_generates_from_natural_language_date(db):
    # Regression: a verified expiration stored as "Month D, YYYY" must produce a
    # lease_expiring opportunity (caught by the end-to-end check).
    exp = date.today() + timedelta(days=120)
    db.add(Observation(entity_type="company", entity_id=77, field="expiration_date",
                       value=exp.strftime("%B %d, %Y"), confidence=0.9,
                       human_verified=True, source_doc="lease.pdf", source_page=2))
    db.commit()
    generate_opportunities(db)
    opps = db.query(IntelOpportunity).filter_by(dedup_key="company:77:lease_expiring").all()
    assert len(opps) == 1


def test_activity_note_facts_do_not_raise_stale_data(db):
    # Facts mined from a call note are conversation intel, not a lease abstract.
    # An entity known only from notes must NOT be flagged "incomplete lease record".
    db.add(Observation(entity_type="company", entity_id=88, field="req_submarkets",
                       value="Arlington", confidence=0.9, human_verified=False,
                       source_doc="activity_log:12"))
    db.add(Observation(entity_type="company", entity_id=88, field="req_space_type",
                       value="Office", confidence=0.9, human_verified=False,
                       source_doc="activity_log:12"))
    db.commit()

    generate_opportunities(db)
    stale = db.query(IntelOpportunity).filter_by(dedup_key="company:88:stale_data").all()
    assert stale == []

    # But a real lease document with missing fields still does raise it.
    db.add(Observation(entity_type="company", entity_id=99, field="tenant_name",
                       value="Acme", confidence=0.9, human_verified=True,
                       source_doc="acme_lease.pdf", source_page=1))
    db.commit()
    generate_opportunities(db)
    assert db.query(IntelOpportunity).filter_by(dedup_key="company:99:stale_data").count() == 1


def test_generate_produces_right_opportunities_in_right_order(db):
    today = date.today()
    # #1 verified, expiring in 90 days -> lease_expiring (highest).
    _complete_lease(db, 1, today + timedelta(days=90), expiration_verified=True)
    # #2 verified, expiring in 400 days -> outside horizon, no opportunity.
    _complete_lease(db, 2, today + timedelta(days=400), expiration_verified=True)
    # #3 unverified, expiring in 200 days -> expiration_unverified (lower).
    _complete_lease(db, 3, today + timedelta(days=200), expiration_verified=False)

    generate_opportunities(db)
    opps = db.query(IntelOpportunity).order_by(IntelOpportunity.score.desc()).all()

    assert len(opps) == 2
    assert opps[0].entity_id == 1
    assert opps[0].dedup_key == "company:1:lease_expiring"
    assert opps[1].entity_id == 3
    assert opps[1].dedup_key == "company:3:expiration_unverified"
    # Verified always outranks unverified regardless of proximity.
    assert opps[0].score > opps[1].score
    # No opportunity for the 400-day lease.
    assert not any(o.entity_id == 2 for o in opps)
    # Rationale is readable and cites the source.
    assert "expires in 90 days" in opps[0].rationale
    assert "lease.pdf" in opps[0].rationale


def test_regenerate_creates_no_duplicates(db):
    today = date.today()
    _complete_lease(db, 1, today + timedelta(days=90), expiration_verified=True)

    generate_opportunities(db)
    generate_opportunities(db)
    generate_opportunities(db)

    opps = db.query(IntelOpportunity).all()
    assert len(opps) == 1  # upserted in place, never duplicated


def test_stale_data_signal_for_incomplete_lease(db):
    # A processed lease with base rent genuinely absent (null + unverified).
    today = date.today()
    db.add(Observation(entity_type="company", entity_id=5, field="tenant_name",
                       value="Acme", confidence=0.9, human_verified=True,
                       source_doc="lease.pdf", source_page=1))
    db.add(Observation(entity_type="company", entity_id=5, field="expiration_date",
                       value=(today + timedelta(days=500)).isoformat(),  # outside horizon
                       confidence=0.9, human_verified=True, source_doc="lease.pdf"))
    db.add(Observation(entity_type="company", entity_id=5, field="base_rent_annual",
                       value=None, confidence=None, human_verified=False,
                       source_doc="lease.pdf"))
    db.commit()

    generate_opportunities(db)
    opps = db.query(IntelOpportunity).all()

    stale = [o for o in opps if o.dedup_key == "company:5:stale_data"]
    assert len(stale) == 1
    # premises_sqft, commencement_date, base_rent_annual are all missing.
    assert "missing or unverified" in stale[0].rationale
