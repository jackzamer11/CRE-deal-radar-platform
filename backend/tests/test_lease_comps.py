"""
Import Lease Comps contract tests.

Covers the tiered name-matching that populates lease expirations onto existing
tenant companies from a parsed CoStar Lease Activity PDF. All inputs are
fabricated in-memory — no live DB (except the in-memory SQLite TestClient
cases), no network, and NO Claude call (parse_lease_pdf is monkeypatched).

Guards:
  (a) exact-name match auto-applies the expiry.
  (b) suffix/case/spacing differences ("Cannon Design" vs "CannonDesign Inc")
      still match (auto).
  (c) a company that already has an expiry is NOT overwritten (skipped-already-set).
  (d) multiple leases for one tenant -> SOONEST expiry wins.
  (e) a lease with a null expiration is skipped without error.
  (f) /api/companies/ still returns the outreach agent's required fields.
  + end-to-end preview endpoint applies exact matches and is idempotent.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.company import Company
from app.services.lease_comps_service import (
    compute_lease_matches,
    normalize_company_name,
)
import app.api.routes.lease_comps as lease_comps_route


# ── In-memory DB fixtures ──────────────────────────────────────────────────────
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


def _company(company_id, name, *, lease_expiry_date=None, lease_expiry_months=None):
    """A plain Company object (not session-attached) for pure matching tests."""
    return Company(
        company_id=company_id,
        name=name,
        industry="Technology",
        current_headcount=50,
        current_submarket="Tysons",
        opportunity_score=0.0,
        priority="IGNORE",
        lease_expiry_date=lease_expiry_date,
        lease_expiry_months=lease_expiry_months,
    )


# ── (a) exact match auto-applies ───────────────────────────────────────────────
def test_exact_name_match_auto_applies():
    companies = [_company("CO-1", "Acme Corp")]
    parsed = [{"tenant_name": "Acme Corp", "expiration_date": "2027-05-01"}]

    result = compute_lease_matches(parsed, companies)

    assert len(result["auto_applied"]) == 1
    entry = result["auto_applied"][0]
    assert entry["company_id"] == "CO-1"
    assert entry["proposed_expiry"] == "2027-05-01"
    assert result["needs_review"] == []
    assert result["skipped_no_match"] == []


# ── (b) suffix / case / spacing differences still match (auto) ─────────────────
def test_suffix_case_spacing_differences_match():
    companies = [_company("CO-2", "Cannon Design")]
    parsed = [{"tenant_name": "CannonDesign Inc", "expiration_date": "2026-11-30"}]

    # Normalization collapses both to the same compact key.
    assert normalize_company_name("Cannon Design").replace(" ", "") == \
           normalize_company_name("CannonDesign Inc").replace(" ", "")

    result = compute_lease_matches(parsed, companies)

    assert [e["company_id"] for e in result["auto_applied"]] == ["CO-2"]
    assert result["auto_applied"][0]["proposed_expiry"] == "2026-11-30"
    assert result["skipped_no_match"] == []


# ── (c) existing expiry is never overwritten ───────────────────────────────────
def test_existing_expiry_not_overwritten():
    existing = date(2025, 1, 1)
    companies = [_company("CO-3", "Acme Corp", lease_expiry_date=existing,
                          lease_expiry_months=6)]
    parsed = [{"tenant_name": "Acme Corp", "expiration_date": "2030-01-01"}]

    result = compute_lease_matches(parsed, companies)

    assert result["auto_applied"] == []
    assert result["needs_review"] == []
    assert len(result["skipped_already_set"]) == 1
    skipped = result["skipped_already_set"][0]
    assert skipped["company_id"] == "CO-3"
    assert skipped["existing_expiry"] == "2025-01-01"
    # Pure function must not mutate the object.
    assert companies[0].lease_expiry_date == existing


# ── (d) multiple leases for one tenant -> soonest wins ─────────────────────────
def test_multiple_leases_soonest_expiry_wins():
    companies = [_company("CO-4", "Globex")]
    parsed = [
        {"tenant_name": "Globex", "expiration_date": "2028-03-01"},
        {"tenant_name": "Globex", "expiration_date": "2026-09-15"},  # soonest
        {"tenant_name": "Globex", "expiration_date": "2027-12-31"},
    ]

    result = compute_lease_matches(parsed, companies)

    assert len(result["auto_applied"]) == 1
    assert result["auto_applied"][0]["proposed_expiry"] == "2026-09-15"


# ── (e) null expiration is skipped without error ───────────────────────────────
def test_null_expiration_is_skipped():
    companies = [_company("CO-5", "Initech")]
    parsed = [{"tenant_name": "Initech", "expiration_date": None}]

    result = compute_lease_matches(parsed, companies)  # must not raise

    assert result["auto_applied"] == []
    assert result["needs_review"] == []
    assert result["skipped_no_match"] == []
    assert result["skipped_already_set"] == []


# ── (e2) fuzzy match lands in needs_review (not auto, not skipped) ─────────────
def test_fuzzy_match_goes_to_review():
    companies = [_company("CO-6", "Northrop Grumman Systems")]
    parsed = [{"tenant_name": "Northrop Grummann System", "expiration_date": "2027-01-01"}]

    result = compute_lease_matches(parsed, companies)

    assert result["auto_applied"] == []
    assert len(result["needs_review"]) == 1
    review = result["needs_review"][0]
    assert review["company_id"] == "CO-6"
    assert 0.0 < review["confidence"] < 1.0


# ── (c-route) /api/companies/ still returns the agent's required fields ────────
def test_companies_list_contract_for_agent(client, db_session):
    db_session.add(_company("CO-AGENT", "Agent Co"))
    db_session.commit()

    resp = client.get("/api/companies/")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    for field in (
        "company_id", "priority", "current_headcount", "headcount_growth_pct",
        "lease_expiry_months", "current_submarket", "opportunity_score",
    ):
        assert field in row, f"/api/companies/ must expose {field} for outreach_agent.py"


# ── end-to-end preview endpoint: applies exact matches + idempotent ───────────
def test_preview_endpoint_applies_and_is_idempotent(client, db_session, monkeypatch):
    db_session.add(_company("CO-100", "Acme Corp"))
    db_session.add(_company("CO-101", "Globex"))
    db_session.commit()

    fake_leases = [
        {"tenant_name": "Acme Corp", "sf": 5000, "expiration_date": "2027-05-01",
         "move_in_date": None},
        {"tenant_name": "Globex", "sf": 8000, "expiration_date": None,
         "move_in_date": None},  # null expiry -> skipped, no crash
    ]
    # Patch the route's reference so no real Claude/network call happens.
    monkeypatch.setattr(lease_comps_route, "parse_lease_pdf", lambda raw: fake_leases)

    files = {"file": ("leases.pdf", b"%PDF-1.4 fake-bytes", "application/pdf")}
    resp = client.post("/api/lease-comps/preview", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["company_id"] for e in body["auto_applied"]] == ["CO-100"]

    # The exact match was actually written.
    acme = db_session.query(Company).filter(Company.company_id == "CO-100").first()
    db_session.refresh(acme)
    assert acme.lease_expiry_date == date(2027, 5, 1)
    assert acme.lease_expiry_source == "costar"
    assert acme.lease_expiry_months is not None

    # Re-importing the same PDF must NOT re-apply or overwrite — idempotent.
    resp2 = client.post("/api/lease-comps/preview", files=files)
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["auto_applied"] == []
    assert any(s["company_id"] == "CO-100" for s in body2["skipped_already_set"])


# ── confirm endpoint writes reviewed matches but respects never-overwrite ─────
def test_confirm_endpoint_writes_and_protects(client, db_session):
    db_session.add(_company("CO-200", "Pied Piper"))
    db_session.add(_company("CO-201", "Hooli",
                            lease_expiry_date=date(2025, 6, 1), lease_expiry_months=3))
    db_session.commit()

    payload = {"matches": [
        {"company_id": "CO-200", "expiration_date": "2028-02-01"},
        {"company_id": "CO-201", "expiration_date": "2030-01-01"},  # protected
    ]}
    resp = client.post("/api/lease-comps/confirm", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [a["company_id"] for a in body["applied"]] == ["CO-200"]
    assert any(s["company_id"] == "CO-201" for s in body["skipped"])

    piper = db_session.query(Company).filter(Company.company_id == "CO-200").first()
    hooli = db_session.query(Company).filter(Company.company_id == "CO-201").first()
    db_session.refresh(piper)
    db_session.refresh(hooli)
    assert piper.lease_expiry_date == date(2028, 2, 1)
    assert hooli.lease_expiry_date == date(2025, 6, 1)  # untouched
