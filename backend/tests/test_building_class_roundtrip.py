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
        "current_sf_occupied": 4800,
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
    sf_needed = created["current_sf_occupied"]

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


def test_patch_building_class_response_keeps_matched_properties(client, db_session):
    """Production regression: setting current_building_class to Class B made all
    matched properties disappear from the company detail panel, even when the
    property was also Class B. Root cause: PATCH /building-class returned the raw
    ORM object, so CompanyOut.matched_properties fell back to the schema default
    [] — the frontend replaces its selected-company state with the PATCH
    response, wiping the cards. The scoring itself was never the problem.

    Confirmed production pair: Bala (Arlington (Rosslyn), Class B, 7,275 SF)
    vs 2420 Wilson Blvd (Arlington (Clarendon), Class B, 7,638 SF avail).
    Adjacent submarkets, same class, SF delta 363 ≤ 800 — must be a valid match
    in the PATCH response itself."""
    prop = Property(
        property_id="NVA-2420W",
        address="2420 Wilson Blvd, Arlington, VA",
        submarket="Arlington (Clarendon)",
        asset_class="Class B",
        total_sf=40000,
        year_built=2001,
        owner_name="Wilson Owner LLC",
        in_place_rent_psf=40.0,
        market_rent_psf=42.0,
        market_cap_rate=6.5,
        sf_avail=7638,
    )
    db_session.add(prop)
    db_session.commit()

    created = _create_company(
        client,
        name="Bala",
        current_submarket="Arlington (Rosslyn)",
        current_sf_occupied=7275,
        current_building_class=None,  # start unknown, like production
    )
    cid = created["company_id"]

    # Sanity: with class unknown the pair already matches (class neutral 50)
    before = client.get(f"/api/companies/{cid}").json()
    assert any(m["property_id"] == "NVA-2420W" for m in before["matched_properties"])

    # The reported repro: set the class to Class B via the PATCH endpoint
    resp = client.patch(f"/api/companies/{cid}/building-class",
                        json={"current_building_class": "Class B"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    match = next((m for m in body["matched_properties"]
                  if m["property_id"] == "NVA-2420W"), None)
    assert match is not None, (
        "Same-class pair must survive in the PATCH response — returning it "
        "without matched_properties wipes the cards in the UI"
    )
    # Adjacent 60 · 0.40 + same-class 100 · 0.30 + SF fit (delta 363) · 0.30
    sf_fit = 100.0 - 40.0 * (363 / 800)
    expected = 0.40 * 60 + 0.30 * 100 + 0.30 * sf_fit
    assert match["match_score"] == pytest.approx(round(expected, 1))
    assert match["adjacent_submarket"] is True
    assert any("Class fit 100/100" in r for r in match["match_reasons"])

    # And the fresh GET agrees — DB really holds Class B and it scores 100
    after = client.get(f"/api/companies/{cid}").json()
    assert after["current_building_class"] == "Class B"
    assert any(m["property_id"] == "NVA-2420W" for m in after["matched_properties"])


def test_patch_sf_occupied_response_keeps_matched_properties(client, db_session):
    """Same response-shape regression for the other match-input PATCH:
    /sf-occupied must also return computed matched_properties."""
    prop = Property(
        property_id="NVA-SFP",
        address="1 SF Patch Way, Reston, VA",
        submarket="Reston",
        asset_class="Class B",
        total_sf=30000,
        year_built=2005,
        owner_name="Owner LLC",
        in_place_rent_psf=35.0,
        market_rent_psf=36.0,
        market_cap_rate=6.5,
        sf_avail=5000,
    )
    db_session.add(prop)
    db_session.commit()

    created = _create_company(client, current_submarket="Reston",
                              current_sf_occupied=20000)  # gate fails: no match
    cid = created["company_id"]

    resp = client.patch(f"/api/companies/{cid}/sf-occupied",
                        json={"current_sf_occupied": 5000})
    assert resp.status_code == 200, resp.text
    assert any(m["property_id"] == "NVA-SFP"
               for m in resp.json()["matched_properties"]), (
        "PATCH /sf-occupied response must include freshly computed matches"
    )

