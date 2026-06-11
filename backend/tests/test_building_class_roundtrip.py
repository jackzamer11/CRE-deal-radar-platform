"""
Current Building Class — create/edit round-trip contract.

Locks Fix 2 of the post-PR-13 batch:
  - POST /api/companies/ persists current_building_class
  - PATCH /api/companies/{id}/building-class updates / clears it (backfill
    path for existing tenants), rejects invalid values
  - GET /api/companies/{id} round-trips the value
  - the stored value feeds the class-fit factor of the composite Match Score
  - the /api/companies/ agent-contract fields are untouched

In-memory SQLite only — no live DB, no network, no CoStar.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log   # noqa: F401
import app.models.outreach_draft # noqa: F401
from app.database import Base, get_db
from app.main import app
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
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_company(client, **overrides):
    payload = {
        "name": "Class Test Co",
        "industry": "Technology",
        "current_headcount": 28,
        "current_submarket": "Reston",
        "current_sf": 4800,
        "lease_expiry_months": 10,
        "current_building_class": "Class B",
    }
    payload.update(overrides)
    resp = client.post("/api/companies/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_persists_building_class(client):
    created = _create_company(client)
    assert created["current_building_class"] == "Class B"

    # Round-trips on a fresh GET
    got = client.get(f"/api/companies/{created['company_id']}").json()
    assert got["current_building_class"] == "Class B"


def test_patch_updates_and_clears_building_class(client):
    created = _create_company(client)
    cid = created["company_id"]

    # Update
    resp = client.patch(f"/api/companies/{cid}/building-class",
                        json={"current_building_class": "Class A"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_building_class"] == "Class A"
    assert client.get(f"/api/companies/{cid}").json()["current_building_class"] == "Class A"

    # Clear back to unknown (null and "" both clear)
    resp = client.patch(f"/api/companies/{cid}/building-class",
                        json={"current_building_class": None})
    assert resp.status_code == 200
    assert resp.json()["current_building_class"] is None


def test_patch_rejects_invalid_class(client):
    created = _create_company(client)
    resp = client.patch(f"/api/companies/{created['company_id']}/building-class",
                        json={"current_building_class": "Trophy"})
    assert resp.status_code == 422


def test_patch_404_for_unknown_company(client):
    resp = client.patch("/api/companies/CO-NOPE/building-class",
                        json={"current_building_class": "Class A"})
    assert resp.status_code == 404


def test_saved_class_feeds_class_fit_factor(client, db_session):
    """A Class B tenant saved via the API must score 70 (one-class upgrade)
    against a Class A property through the live pairing surface."""
    from app.api.routes.properties import _compute_matched_tenants

    created = _create_company(client)  # Class B tenant, Reston, needs ~4,900 SF
    sf_needed = created["estimated_sf_needed"]

    prop = Property(
        property_id="NVA-CLS",
        address="100 Class Fit Way, Reston, VA",
        submarket="Reston",
        asset_class="Class A",
        total_sf=50000,
        year_built=2010,
        owner_name="Owner LLC",
        in_place_rent_psf=38.0,
        market_rent_psf=39.0,
        market_cap_rate=6.5,
        sf_avail=sf_needed,  # delta 0 — isolates the class factor
    )
    db_session.add(prop)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    me = next(m for m in matched if m.company_id == created["company_id"])
    # 0.40·100 (exact submarket) + 0.30·70 (B→A upgrade) + 0.30·100 (delta 0) = 91.0
    assert me.match_score == pytest.approx(91.0)


def test_agent_contract_fields_unchanged(client):
    """/api/companies/ still exposes every field outreach_agent.py consumes."""
    _create_company(client)
    rows = client.get("/api/companies/").json()
    assert rows, "List endpoint must return the created company"
    row = rows[0]
    for field in ("priority", "current_headcount", "headcount_growth_pct",
                  "lease_expiry_months", "current_submarket",
                  "opportunity_score", "company_id"):
        assert field in row, f"Agent contract field missing: {field}"
