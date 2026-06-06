"""
Hard-delete endpoints for properties and companies.

  (1) DELETE /api/properties/{id} returns 200 and the record is gone.
  (2) DELETE /api/companies/{id} returns 200 and the record is gone.
  (3) Deleting a non-existent record returns 404 (both endpoints).
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
from app.models.company import Company
from app.models.property import Property


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


def _make_property(session):
    prop = Property(
        property_id="NVA-DEL",
        address="9 Delete Plaza",
        submarket="Tysons",
        total_sf=40000,
        year_built=2001,
        owner_name="Owner LLC",
        in_place_rent_psf=33.0,
        market_rent_psf=37.0,
        market_cap_rate=6.5,
        sf_avail=4000,
    )
    session.add(prop)
    session.commit()
    return prop


def _make_company(session):
    co = Company(
        company_id="CO-DEL",
        name="Delete Co",
        industry="Technology",
        current_submarket="Tysons",
        current_sf=4000,
        estimated_sf_needed=4000,
    )
    session.add(co)
    session.commit()
    return co


# ── (1) DELETE property → 200, record gone ─────────────────────────────────────
def test_delete_property_removes_record(client, db_session):
    _make_property(db_session)
    assert db_session.query(Property).filter_by(property_id="NVA-DEL").first() is not None

    resp = client.delete("/api/properties/NVA-DEL")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == "NVA-DEL"

    db_session.expire_all()
    assert db_session.query(Property).filter_by(property_id="NVA-DEL").first() is None


# ── (2) DELETE company → 200, record gone ──────────────────────────────────────
def test_delete_company_removes_record(client, db_session):
    _make_company(db_session)
    assert db_session.query(Company).filter_by(company_id="CO-DEL").first() is not None

    resp = client.delete("/api/companies/CO-DEL")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == "CO-DEL"

    db_session.expire_all()
    assert db_session.query(Company).filter_by(company_id="CO-DEL").first() is None


# ── (3) DELETE non-existent → 404 (graceful) ───────────────────────────────────
def test_delete_missing_property_returns_404(client):
    resp = client.delete("/api/properties/NVA-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_delete_missing_company_returns_404(client):
    resp = client.delete("/api/companies/CO-DOES-NOT-EXIST")
    assert resp.status_code == 404
