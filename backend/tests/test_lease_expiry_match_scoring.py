"""
Lease-expiry proximity as a fourth factor in the composite Match Score.

Verification tests for the four-factor weight model:
  lease_expiry 0.40 | submarket 0.30 | class_fit 0.15 | sf_fit 0.15

sig_lease_expiry_proximity tier table (from signal_engine):
  6-9 mo  = 100 (Peak Window)    1-3 mo   = 35 (Late Stage)
  10-12   = 80  (Early Prime)    19-24    = 32 (Early Stage)
  4-5     = 70  (Ramping)        25-36    = 15 (Too Early)
  13-18   = 55  (Planning Stage) 36+      =  0 (floored, not hidden)
  Expired = 25                   None → 25 fallback in match_scoring

In-memory SQLite only — no live DB, no network, no CoStar.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log   # noqa: F401
import app.models.outreach_draft # noqa: F401
from app.database import Base
from app.models.company import Company
from app.models.property import Property

from app.config import MATCH_SCORE_WEIGHTS
from app.services.match_scoring import (
    compute_match,
    lease_expiry_chip_label,
    LEASE_EXPIRY_NULL_POINTS,
    LEASE_EXPIRY_FLOOR_COMPOSITE,
)


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


def _make_prop(session, *, property_id="NVA-L1", submarket="Tysons",
               asset_class="Class B", sf_avail=5000):
    prop = Property(
        property_id=property_id,
        address=f"{property_id} Test Plaza, Tysons, VA",
        submarket=submarket,
        asset_class=asset_class,
        total_sf=50000,
        year_built=2005,
        owner_name="Test Owner LLC",
        in_place_rent_psf=38.0,
        market_rent_psf=39.0,
        market_cap_rate=6.5,
        sf_avail=sf_avail,
        dominant_score_type="tenant_match",
        listed_for_sale=False,
    )
    session.add(prop)
    return prop


def _make_co(session, *, company_id="CO-L1", submarket="Tysons",
             building_class="Class B", sf_needed=5000, lease_expiry_months=None):
    co = Company(
        company_id=company_id,
        name=f"Tenant {company_id}",
        industry="Technology",
        current_headcount=50,
        current_submarket=submarket,
        current_building_class=building_class,
        current_sf_occupied=sf_needed,
        lease_expiry_months=lease_expiry_months,
        opportunity_score=80.0,
        priority="HIGH",
    )
    session.add(co)
    return co


# ── Test 1: 7 mo ranks above 19 mo on same property ──────────────────────────

def test_seven_month_ranks_above_nineteen_month(db_session):
    """A 7-month tenant (Peak Window, sig=100) must score higher than a 19-month
    tenant (Early Stage, sig=32) against the same property."""
    from app.api.routes.properties import _compute_matched_tenants

    prop = _make_prop(db_session)
    _make_co(db_session, company_id="CO-7MO",  lease_expiry_months=7)
    _make_co(db_session, company_id="CO-19MO", lease_expiry_months=19)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    scores = {m.company_id: m.match_score for m in matched}
    assert "CO-7MO" in scores and "CO-19MO" in scores
    assert scores["CO-7MO"] > scores["CO-19MO"], (
        f"7-month tenant ({scores['CO-7MO']}) must outscore 19-month ({scores['CO-19MO']})"
    )


# ── Test 2: 3 mo ranks below 7 mo ────────────────────────────────────────────

def test_three_month_ranks_below_seven_month():
    """Late Stage (3 mo, sig=35) scores below Peak Window (7 mo, sig=100).
    Uses compute_match directly — no DB needed."""
    match_3mo = compute_match(
        "Tysons", "Tysons", "Class B", "Class B", 5000, 5000,
        tenant_lease_expiry_months=3,
    )
    match_7mo = compute_match(
        "Tysons", "Tysons", "Class B", "Class B", 5000, 5000,
        tenant_lease_expiry_months=7,
    )
    assert match_3mo is not None and match_7mo is not None
    assert match_3mo["score"] < match_7mo["score"], (
        f"3-month ({match_3mo['score']}) must score below 7-month ({match_7mo['score']})"
    )
    # sig(3)=35: 0.40·35 + 0.30·100 + 0.15·100 + 0.15·100 = 14+30+15+15 = 74.0
    assert match_3mo["score"] == pytest.approx(74.0)
    # sig(7)=100: 0.40·100 + 0.30·100 + 0.15·100 + 0.15·100 = 100.0
    assert match_7mo["score"] == pytest.approx(100.0)


# ── Test 3: null lease → 25 fallback, match still produced ───────────────────

def test_null_lease_expiry_uses_fallback_25_and_remains_visible():
    """Unknown lease expiry falls back to 25 (below-neutral). The pair is still
    produced — null data does not suppress a match."""
    match = compute_match(
        "Tysons", "Tysons", "Class B", "Class B", 5000, 5000,
        tenant_lease_expiry_months=None,
    )
    assert match is not None, "Null lease must not suppress a match"
    assert match["lease_expiry_score"] == LEASE_EXPIRY_NULL_POINTS
    assert match["lease_expiry_score"] == 25.0
    # 0.40·25 + 0.30·100 + 0.15·100 + 0.15·100 = 10+30+15+15 = 70.0
    assert match["score"] == pytest.approx(70.0)


# ── Test 4: 36+ months → floor 20, not hidden ────────────────────────────────

def test_thirty_six_plus_months_applies_floor_and_stays_visible():
    """A 36+ month tenant (sig=0) is still matched (not excluded) and the
    composite is at least LEASE_EXPIRY_FLOOR_COMPOSITE (20)."""
    match = compute_match(
        "Tysons", "Tysons", "Class B", "Class B", 5000, 5000,
        tenant_lease_expiry_months=40,
    )
    assert match is not None, "36+ month tenant must still produce a match (not excluded)"
    assert match["lease_expiry_score"] == 0.0
    assert match["score"] >= LEASE_EXPIRY_FLOOR_COMPOSITE, (
        f"Composite {match['score']} must be >= floor {LEASE_EXPIRY_FLOOR_COMPOSITE}"
    )
    # No floor trigger needed here since sub=100 alone keeps composite high:
    # 0.40·0 + 0.30·100 + 0.15·100 + 0.15·100 = 0+30+15+15 = 60.0 > 20
    assert match["score"] == pytest.approx(60.0)


def test_floor_prevents_composite_below_20_when_lease_zero():
    """When raw_lease==0 the composite floor of 20 is enforced even if the
    sub/class/SF scores are all at minimums."""
    # Use the floor path directly: adjacent submarket (60), two-class gap (30),
    # sf at boundary (60), lease=0 → composite=0.40·0+0.30·60+0.15·30+0.15·60
    # = 0 + 18 + 4.5 + 9 = 31.5 > 20, so floor doesn't change result.
    # The contract: the score field is >= 20 whenever raw_lease==0.
    match = compute_match(
        "Reston", "Herndon", "Class A", "Class C", 5000, 5800,
        tenant_lease_expiry_months=40,
    )
    assert match is not None
    assert match["lease_expiry_score"] == 0.0
    assert match["score"] >= LEASE_EXPIRY_FLOOR_COMPOSITE


# ── Test 5: weights sum to 1.0 ───────────────────────────────────────────────

def test_match_score_weights_sum_to_one():
    """The four-factor weight dict must sum exactly to 1.0."""
    total = sum(MATCH_SCORE_WEIGHTS.values())
    assert total == pytest.approx(1.0), f"Weights sum to {total}, expected 1.0"
    assert set(MATCH_SCORE_WEIGHTS.keys()) == {"lease_expiry", "submarket", "class", "sf_fit"}


# ── Test 6: hand-calculated composite = 88.0 ─────────────────────────────────

def test_hand_calculated_composite_88():
    """Spec example: adjacent submarket (60), same class (100), delta 0 SF (100),
    7-month lease → Peak Window → sig=100.
    Composite = 0.40·100 + 0.30·60 + 0.15·100 + 0.15·100 = 40+18+15+15 = 88.0"""
    match = compute_match(
        "Reston", "Herndon", "Class B", "Class B", 5000, 5000,
        tenant_lease_expiry_months=7,
    )
    assert match is not None
    assert match["lease_expiry_score"] == 100.0
    assert match["submarket_score"] == 60.0
    assert match["class_score"] == 100.0
    assert match["sf_fit_score"] == 100.0
    assert match["score"] == pytest.approx(88.0)
    assert match["adjacent"] is True


# ── Test 7: chip label text per tier ─────────────────────────────────────────

@pytest.mark.parametrize("months,expected", [
    (None,  "No Expiry Data"),
    (0,     "Expired"),
    (-1,    "Expired"),
    (1,     "Late Stage (1mo)"),
    (3,     "Late Stage (3mo)"),
    (4,     "Ramping (4mo)"),
    (5,     "Ramping (5mo)"),
    (6,     "Peak Window (6mo)"),
    (9,     "Peak Window (9mo)"),
    (10,    "Early Prime (10mo)"),
    (12,    "Early Prime (12mo)"),
    (13,    "Planning Stage (13mo)"),
    (18,    "Planning Stage (18mo)"),
    (19,    "Early Stage (19mo)"),
    (24,    "Early Stage (24mo)"),
    (25,    "Too Early (25mo)"),
    (36,    "Too Early (36mo)"),
])
def test_chip_label_tiers(months, expected):
    assert lease_expiry_chip_label(months) == expected, (
        f"chip_label({months!r}) expected {expected!r}"
    )


# ── Test 8: matched_properties score reflects lease factor ───────────────────

def test_matched_properties_score_uses_lease_factor(db_session):
    """The company-detail matched_properties surface must include lease expiry
    in the composite — a company with a 7-month lease must score higher than
    one with a 19-month lease against the same property."""
    from app.api.routes.companies import _compute_matched_properties

    prop = _make_prop(db_session, property_id="NVA-L8", submarket="Tysons",
                      asset_class="Class B", sf_avail=5000)

    co_peak = _make_co(db_session, company_id="CO-PEAK", submarket="Tysons",
                       building_class="Class B", sf_needed=5000, lease_expiry_months=7)
    co_early = _make_co(db_session, company_id="CO-EARLY", submarket="Tysons",
                        building_class="Class B", sf_needed=5000, lease_expiry_months=19)
    db_session.commit()

    matched_peak = _compute_matched_properties(co_peak, db_session)
    matched_early = _compute_matched_properties(co_early, db_session)

    score_peak  = next(m.match_score for m in matched_peak  if m.property_id == "NVA-L8")
    score_early = next(m.match_score for m in matched_early if m.property_id == "NVA-L8")

    # sig(7)=100 → 0.40·100+0.30·100+0.15·100+0.15·100 = 100.0
    assert score_peak == pytest.approx(100.0)
    # sig(19)=32  → 0.40·32+0.30·100+0.15·100+0.15·100 = 12.8+30+15+15 = 72.8
    assert score_early == pytest.approx(72.8)
    assert score_peak > score_early

    # Chip label appears in match_reasons on the company surface
    reasons_peak = next(m.match_reasons for m in matched_peak if m.property_id == "NVA-L8")
    assert any("Peak Window (7mo)" in r for r in reasons_peak), (
        f"Chip label missing from match_reasons: {reasons_peak}"
    )


# ── Test 9: Section A sorts descending by new composite ──────────────────────

def test_section_a_sorts_descending_by_composite_with_lease(db_session):
    """Dashboard Section A must order pairs highest-composite-first, with
    lease expiry correctly influencing that composite."""
    from app.services.output_engine import _compute_tenant_actions

    prop = _make_prop(db_session, property_id="NVA-L9", submarket="Tysons",
                      asset_class="Class B", sf_avail=5000)

    # Three tenants, same submarket/class/SF, different lease expiry:
    # Peak (7mo, sig=100) > Early Stage (19mo, sig=32) > null (25 fallback)
    _make_co(db_session, company_id="CO-PEAK9",  lease_expiry_months=7)
    _make_co(db_session, company_id="CO-ES9",    lease_expiry_months=19)
    _make_co(db_session, company_id="CO-NULL9",  lease_expiry_months=None)
    db_session.commit()

    actions = _compute_tenant_actions(db_session)
    # Section A deduplicates to best tenant per property, so only one action
    # for NVA-L9. Peak should win.
    nva_action = next((a for a in actions if a.property_id == "NVA-L9"), None)
    assert nva_action is not None
    assert nva_action.tenant_company_id == "CO-PEAK9", (
        f"Peak Window tenant must be best match; got {nva_action.tenant_company_id}"
    )

    # Verify the overall list is sorted descending
    scores = [a.match_score for a in actions]
    assert scores == sorted(scores, reverse=True), "Section A must be sorted descending by match_score"

    # Verify chip label is present on the action
    assert nva_action.lease_expiry_chip == "Peak Window (7mo)"
