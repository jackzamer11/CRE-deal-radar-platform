"""
Verification for two changes:

  (1) Medical/non-medical soft penalty: a medical property matched to a
      non-medical tenant scores exactly 20 points lower than the identical match
      without the mismatch — and the match still appears (no hard filter).

  (2) CoStar import upsert always sets estimated_sf_needed = current_sf, on both
      the insert and the update (re-import) path.
"""
import io

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
from app.api.routes.companies import COSTAR_TENANT_COLS
from app.services.match_scoring import medical_mismatch_penalty, MEDICAL_MISMATCH_PENALTY


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


@pytest.fixture()
def client(db_session):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _make_property(session, *, is_medical):
    prop = Property(
        property_id="NVA-MED",
        address="1 Medical Plaza",
        submarket="Tysons",
        total_sf=50000,
        year_built=2005,
        owner_name="Owner LLC",
        in_place_rent_psf=35.0,
        market_rent_psf=38.0,
        market_cap_rate=6.5,
        sf_avail=5000,
        dominant_score_type="tenant_match",
        listed_for_sale=False,
        is_medical=is_medical,
    )
    session.add(prop)
    return prop


def _make_tenant(session, *, is_medical):
    co = Company(
        company_id="CO-MED",
        name="Tenant Co",
        industry="Technology",
        current_submarket="Tysons",   # same submarket → +30
        current_sf_occupied=5000,      # ratio 1.0 → +40
        lease_expiry_months=10,        # <= 18 → +10
        is_medical=is_medical,
    )
    session.add(co)
    return co


def _score_section_a(session):
    from app.services.output_engine import _compute_tenant_actions
    actions = _compute_tenant_actions(session)
    assert len(actions) == 1, "Expected exactly one matched tenant action"
    return actions[0].match_score


# ── (1) medical mismatch costs exactly 20 points, match still shown ────────────
def test_medical_mismatch_penalty_helper():
    class Flag:
        def __init__(self, m): self.is_medical = m
    assert medical_mismatch_penalty(Flag(True), Flag(False)) == MEDICAL_MISMATCH_PENALTY == -20.0
    assert medical_mismatch_penalty(Flag(False), Flag(True)) == -20.0
    assert medical_mismatch_penalty(Flag(True), Flag(True)) == 0.0
    assert medical_mismatch_penalty(Flag(False), Flag(False)) == 0.0


def test_medical_property_nonmedical_tenant_scores_20_lower(db_session):
    # Baseline: neither side medical → no penalty.
    _make_property(db_session, is_medical=False)
    _make_tenant(db_session, is_medical=False)
    db_session.commit()
    baseline = _score_section_a(db_session)

    # Flip the property to medical (tenant stays non-medical) → mismatch.
    prop = db_session.query(Property).filter_by(property_id="NVA-MED").first()
    prop.is_medical = True
    db_session.commit()
    penalized = _score_section_a(db_session)

    assert penalized == baseline - 20, (
        f"Medical/non-medical mismatch must cost 20 points "
        f"(baseline {baseline}, penalized {penalized})"
    )
    # Soft penalty, not a hard filter — the match is still present.
    assert penalized > 0


# ── (2) CoStar upsert sets current_sf_occupied from "SF Occupied" ───────────────
def _costar_row(tenant_name, sf_occupied):
    base = {col: "" for col in COSTAR_TENANT_COLS}
    base.update({
        "Tenant Name": tenant_name,
        "Address":     f"{tenant_name} Plaza, Tysons, VA",
        "State":       "VA",
        "Submarket":   "Tysons",
        "SF Occupied": str(sf_occupied),
        "Industry":    "Technology",
        "Employees":   "25",
    })
    return base


def _upload(client, rows):
    import pandas as pd
    df = pd.DataFrame(rows, columns=COSTAR_TENANT_COLS)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    resp = client.post(
        "/api/companies/costar-import",
        files={"file": ("tenants.csv", buf.getvalue(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_costar_upsert_sets_current_sf_occupied(client, db_session):
    # Insert path — CoStar "SF Occupied" lands in the single current_sf_occupied field.
    res1 = _upload(client, [_costar_row("UpsertCo", 6000)])
    assert res1["inserted"] == 1
    co = db_session.query(Company).filter_by(name="UpsertCo").first()
    assert co is not None
    assert co.current_sf_occupied == 6000

    # Update (re-import same tenant+address) path with a different SF
    res2 = _upload(client, [_costar_row("UpsertCo", 8200)])
    assert res2["updated"] == 1
    db_session.refresh(co)
    assert co.current_sf_occupied == 8200
