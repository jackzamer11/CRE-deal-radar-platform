"""
Tests for the current_sf_occupied SF model + the MAX_SF_DELTA match tolerance.

Required coverage:
  (1) a company with null current_sf_occupied is blocked from outreach
      (decision: the match CARD is kept, but outreach generation is blocked).
  (2) a 799 SF delta passes (within tolerance).
  (3) an 801 SF delta is suppressed (no card, no outreach).
  (4) an exact SF match passes.
  (5) contacted pairs are excluded from the delta filter (never suppressed).
  (6) no headcount-based SF calculation exists anywhere in the codebase.

Offline: no live LLM / network. An in-memory SQLite DB is used where a DB is
needed; FastAPI's get_db is overridden for the one endpoint test.
"""
import os
import re
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.models.company import Company
from app.models.property import Property
from app.models.outreach_log import OutreachLog
from app.services.match_scoring import sf_match_suppressed, MAX_SF_DELTA


# ── Fixtures ────────────────────────────────────────────────────────────────────
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


def _property(db, *, sf_avail=5000, total_sf=50000, vacant_sf=None, property_id="NVA-1",
              submarket="Tysons"):
    p = Property(
        property_id=property_id, address="1 Plaza", submarket=submarket,
        total_sf=total_sf, year_built=2005, sf_avail=sf_avail, vacant_sf=vacant_sf,
        owner_name="Owner LLC",
        in_place_rent_psf=35.0, market_rent_psf=38.0, market_cap_rate=6.5,
        listed_for_sale=False,
    )
    db.add(p)
    db.commit()
    return p


def _company(db, *, sf_occupied, company_id="CO-1"):
    c = Company(
        company_id=company_id, name="Tenant Co", industry="Technology",
        current_submarket="Tysons", current_sf_occupied=sf_occupied,
        lease_expiry_months=10, expansion_signal=True,
    )
    db.add(c)
    db.commit()
    return c


# ── (1) null current_sf_occupied → outreach blocked ─────────────────────────────
def test_null_sf_blocks_outreach(client, db_session):
    """Decision: the match card is kept, but outreach is blocked until SF is set.
    The company-side draft-outreach endpoint refuses with a 'SF: Unknown' 422."""
    _company(db_session, sf_occupied=None, company_id="CO-NOSF")

    resp = client.post("/api/companies/CO-NOSF/draft-outreach")
    assert resp.status_code == 422, resp.text
    assert "SF: Unknown" in resp.json()["detail"]


def test_null_sf_keeps_match_card(db_session):
    """Decision check: a null-SF tenant is NOT removed from the match surface."""
    from app.api.routes.properties import _compute_matched_tenants
    prop = _property(db_session, sf_avail=5000)
    _company(db_session, sf_occupied=None, company_id="CO-NOSF")
    matched = _compute_matched_tenants(prop, db_session)
    assert any(m.company_id == "CO-NOSF" for m in matched), "null-SF card must remain"


# ── (2)(3)(4) delta tolerance unit behaviour ────────────────────────────────────
def test_constant_value():
    assert MAX_SF_DELTA == 800


def test_799_delta_passes():
    # |5000 - 5799| = 799 ≤ 800 → not suppressed
    assert sf_match_suppressed(5000, 5799) is False
    assert sf_match_suppressed(5799, 5000) is False


def test_801_delta_suppressed():
    # |5000 - 5801| = 801 > 800 → suppressed
    assert sf_match_suppressed(5000, 5801) is True
    assert sf_match_suppressed(5801, 5000) is True


def test_exact_match_passes():
    assert sf_match_suppressed(5000, 5000) is False


def test_800_delta_boundary_passes():
    # Exactly at the ceiling is allowed (strictly greater-than suppresses).
    assert sf_match_suppressed(5000, 5800) is False


# ── (3 integration) 801 delta is suppressed from the match surface ──────────────
def test_801_delta_suppressed_from_matches(db_session):
    from app.api.routes.properties import _compute_matched_tenants
    prop = _property(db_session, sf_avail=5000)
    _company(db_session, sf_occupied=5801, company_id="CO-FAR")  # 801 over
    matched = _compute_matched_tenants(prop, db_session)
    assert not any(m.company_id == "CO-FAR" for m in matched), (
        "a pairing > MAX_SF_DELTA apart must be suppressed entirely"
    )


def test_799_delta_appears_in_matches(db_session):
    from app.api.routes.properties import _compute_matched_tenants
    prop = _property(db_session, sf_avail=5000)
    _company(db_session, sf_occupied=5799, company_id="CO-NEAR")  # 799 over
    matched = _compute_matched_tenants(prop, db_session)
    assert any(m.company_id == "CO-NEAR" for m in matched), (
        "a pairing within MAX_SF_DELTA must still match"
    )


# ── (Fix 1) delta filter uses AVAILABLE SF, not Total SF ────────────────────────
def test_delta_uses_available_sf_not_total_sf(db_session):
    """Total SF within 800 of occupied must NOT save a pairing whose AVAILABLE SF
    gap exceeds 800 — the filter compares against available SF only."""
    from app.api.routes.properties import _compute_matched_tenants
    # occupied 5000; total_sf 5200 (Δ200, within tolerance) but sf_avail 6000 (Δ1000).
    prop = _property(db_session, sf_avail=6000, total_sf=5200)
    _company(db_session, sf_occupied=5000, company_id="CO-AVAILFAR")
    matched = _compute_matched_tenants(prop, db_session)
    assert not any(m.company_id == "CO-AVAILFAR" for m in matched), (
        "delta must be measured against available SF (6000), not total SF (5200)"
    )


def test_null_available_sf_suppresses(db_session):
    """Fix 1: when available SF is null the pairing is suppressed entirely."""
    from app.api.routes.properties import _compute_matched_tenants
    assert sf_match_suppressed(5000, None) is True
    assert sf_match_suppressed(5000, 0) is True
    prop = _property(db_session, sf_avail=None)
    _company(db_session, sf_occupied=5000, company_id="CO-NOAVAIL")
    matched = _compute_matched_tenants(prop, db_session)
    assert matched == [], "null available SF must suppress all matches for the property"


def test_delta_ignores_vacant_sf(db_session):
    """The delta filter must read AVAILABLE SF, never vacant_sf: a vacant_sf within
    800 of occupied must NOT save a pairing whose available SF gap exceeds 800."""
    from app.api.routes.properties import _compute_matched_tenants
    # occupied 5000; vacant_sf 5000 (Δ0) but sf_avail 6000 (Δ1000) → suppressed.
    prop = _property(db_session, sf_avail=6000, vacant_sf=5000.0)
    _company(db_session, sf_occupied=5000, company_id="CO-VACFAR")
    matched = _compute_matched_tenants(prop, db_session)
    assert not any(m.company_id == "CO-VACFAR" for m in matched), (
        "delta must use available SF (6000), not vacant_sf (5000)"
    )


# ── Reported regression: Human Resources Research (40,000) vs N Pitt St (2,954) ──
def test_reported_hrr_vs_pitt_st_suppressed(db_session):
    """Regression for the reported bug: a 40,000 SF tenant must be suppressed against
    a 2,954 SF available property (Δ37,046 ≫ 800) on BOTH match surfaces."""
    from app.api.routes.properties import _compute_matched_tenants
    from app.api.routes.companies import _compute_matched_properties

    # Total/vacant deliberately set near the occupied figure to prove they are
    # NOT what the delta reads — only sf_avail (2,954) is.
    prop = _property(
        db_session, sf_avail=2954, total_sf=40000, vacant_sf=39500.0,
        property_id="NVA-PITT", submarket="Alexandria (Old Town)",
    )
    co = Company(
        company_id="CO-HRR", name="Human Resources Research", industry="Federal",
        current_submarket="Alexandria (Old Town)", current_sf_occupied=40000,
        lease_expiry_months=12, expansion_signal=True,
    )
    db_session.add(co)
    db_session.commit()

    assert not any(
        m.company_id == "CO-HRR" for m in _compute_matched_tenants(prop, db_session)
    ), "HRR (40,000) must be suppressed against 2,954 SF available (property surface)"
    assert not any(
        p.property_id == "NVA-PITT" for p in _compute_matched_properties(co, db_session)
    ), "N Pitt St (2,954 avail) must be suppressed for HRR (40,000) (company surface)"


def test_company_card_endpoint_suppresses_contacted_mismatch(client, db_session):
    """End-to-end via the Company-card endpoint (GET /api/companies/{id}): a gross SF
    mismatch must NOT appear even when the pair has already been contacted.

    This reproduces the reported bug exactly — the contacted exemption used to bypass
    the SF delta and surface 1240-1250 N Pitt St (2,954 avail) for Human Resources
    Research (40,000 occupied). The SF delta is now a hard filter on this display.
    """
    prop = _property(
        db_session, sf_avail=2954, total_sf=42000, vacant_sf=2954.0,
        property_id="NVA-PITT", submarket="Alexandria (Old Town)",
    )
    co = Company(
        company_id="CO-HRR", name="Human Resources Research", industry="Federal",
        current_submarket="Alexandria (Old Town)", current_sf_occupied=40000,
        current_rent_psf=40.0, lease_expiry_months=12, expansion_signal=True,
    )
    db_session.add(co)
    db_session.commit()
    # Mark the exact pair contacted — this is what previously bypassed suppression.
    db_session.add(OutreachLog(
        property_id=prop.id, company_id=co.id, outreach_type="tenant_match",
        marked_contacted=True,
    ))
    db_session.commit()

    resp = client.get("/api/companies/CO-HRR")
    assert resp.status_code == 200, resp.text
    matched = resp.json().get("matched_properties", [])
    assert not any(p["property_id"] == "NVA-PITT" for p in matched), (
        "1240-1250 N Pitt St (2,954 SF avail) must NOT appear on HRR's Company card "
        f"(Δ37,046 ≫ 800), even when contacted; got: {matched}"
    )


# ── (5) contacted pairs are excluded from the delta filter ──────────────────────
def test_contacted_pair_excluded_from_delta_filter(db_session):
    """A pair whose SF gap exceeds MAX_SF_DELTA is normally suppressed — but if the
    pair is already marked contacted, suppression must NOT apply (contacted history
    is never disturbed)."""
    from app.api.routes.properties import _compute_matched_tenants
    prop = _property(db_session, sf_avail=5000)
    co = _company(db_session, sf_occupied=9000, company_id="CO-CONTACTED")  # 4000 over

    # Sanity: without contact, this pair is suppressed.
    assert not any(
        m.company_id == "CO-CONTACTED" for m in _compute_matched_tenants(prop, db_session)
    )

    # Mark the exact pair contacted via an OutreachLog row.
    db_session.add(OutreachLog(
        property_id=prop.id, company_id=co.id, outreach_type="tenant_match",
        marked_contacted=True,
    ))
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    assert any(m.company_id == "CO-CONTACTED" for m in matched), (
        "a contacted pair must be excluded from the delta filter (still shown)"
    )


# ── (6) no headcount-based SF calculation exists anywhere ───────────────────────
def test_no_headcount_based_sf_calculation():
    """Scan the application source for any headcount → square-footage derivation.

    Allowed: SF / headcount ratios (utilization) and open_positions / headcount
    velocity. Forbidden: multiplying headcount (or any SF-per-head constant) to
    PRODUCE a square-footage figure, and the removed project_sf() projection.
    """
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    forbidden = [
        re.compile(r"\*\s*175\b"),
        re.compile(r"\b175\s*\*"),
        re.compile(r"SF_PER_PERSON", re.IGNORECASE),
        re.compile(r"MODERN_SF_PER_HEAD"),
        re.compile(r"def\s+project_sf"),
    ]
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in forbidden:
            if pat.search(text):
                offenders.append(f"{path.name}: {pat.pattern}")
    assert not offenders, f"headcount-based SF derivation found: {offenders}"
