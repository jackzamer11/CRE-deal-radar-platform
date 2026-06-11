"""
Composite Match Score contract tests.

Locks the tenant ↔ property matching contract introduced with the composite
Match Score (submarket adjacency 0.40 / building-class fit 0.30 / SF fit 0.30):

  - exact submarket scores 100; adjacent scores 60; adjacency works in BOTH
    lookup directions; non-adjacent pairs are excluded
  - unknown submarket falls back to exact-match-only (never crashes, never
    matches everything)
  - class upgrade = 70, downgrade = 55, two-class gap = 30 (visible, not excluded), null class = 50
  - the 800 SF delta hard gate excludes pairs BEFORE scoring
  - composite math verified against a hand-calculated example
  - pairing generators sort descending by composite and never 500 on null
    submarket / null class / empty company lists

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

from app.config import (
    SUBMARKET_ADJACENCY,
    MATCH_SCORE_WEIGHTS,
    MAX_SF_DELTA,
)
from app.services.match_scoring import (
    are_adjacent,
    submarket_score,
    class_score,
    sf_fit_score,
    sf_delta_passes_gate,
    compute_match,
)


# ── In-memory DB fixture ──────────────────────────────────────────────────────
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


def _make_property(session, *, property_id="NVA-T1", submarket="Tysons",
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


def _make_company(session, *, company_id="CO-T1", submarket="Tysons",
                  building_class=None, sf_needed=5000):
    co = Company(
        company_id=company_id,
        name=f"Tenant {company_id}",
        industry="Technology",
        current_headcount=50,
        current_submarket=submarket,
        current_building_class=building_class,
        current_sf_occupied=sf_needed,
        lease_expiry_months=10,
        opportunity_score=80.0,
        priority="HIGH",
    )
    session.add(co)
    return co


# ── Adjacency map structure ───────────────────────────────────────────────────

def test_adjacency_map_is_fully_symmetric():
    for sub, neighbors in SUBMARKET_ADJACENCY.items():
        for n in neighbors:
            assert sub in SUBMARKET_ADJACENCY.get(n, set()), (
                f"Adjacency must be symmetric: {sub} → {n} exists but {n} → {sub} missing"
            )


def test_spec_pairs_present_both_directions():
    spec_pairs = [
        ("Reston", "Herndon"), ("Reston", "Tysons"), ("Reston", "Vienna"), ("Reston", "McLean"),
        ("Tysons", "McLean"), ("Tysons", "Vienna"), ("Tysons", "Falls Church"), ("Tysons", "Herndon"),
        ("McLean", "Vienna"),
        ("Vienna", "Merrifield"), ("Vienna", "Fairfax City"),
        ("Falls Church", "Merrifield"),
        ("Merrifield", "Fairfax City"), ("Merrifield", "Annandale"),
        ("Annandale", "Springfield"),
        ("Fairfax City", "Centreville"),
        ("Arlington (Clarendon)", "Arlington (Rosslyn)"),
        ("Arlington (Clarendon)", "Arlington (Ballston)"),
        ("Arlington (Clarendon)", "Arlington (Columbia Pike)"),
        ("Arlington (Rosslyn)", "Arlington (Ballston)"),
        ("Arlington (Rosslyn)", "Arlington (Columbia Pike)"),
        ("Arlington (Ballston)", "Arlington (Columbia Pike)"),
        ("Arlington (Clarendon)", "Crystal City"),
        ("Arlington (Rosslyn)", "Crystal City"),
        ("Arlington (Ballston)", "Crystal City"),
        ("Arlington (Columbia Pike)", "Crystal City"),
        ("Crystal City", "Alexandria (Old Town)"),
    ]
    for a, b in spec_pairs:
        assert are_adjacent(a, b), f"{a} ↔ {b} must be adjacent"
        assert are_adjacent(b, a), f"{b} ↔ {a} must be adjacent (reverse direction)"


# ── Submarket factor ──────────────────────────────────────────────────────────

def test_exact_submarket_scores_100():
    assert submarket_score("Tysons", "Tysons") == 100.0


def test_adjacent_submarket_scores_60_both_directions():
    assert submarket_score("Reston", "Herndon") == 60.0
    assert submarket_score("Herndon", "Reston") == 60.0


def test_non_adjacent_pair_excluded():
    # Reston and Alexandria (Old Town) are both known but not adjacent
    assert submarket_score("Reston", "Alexandria (Old Town)") is None


def test_unknown_submarket_falls_back_to_exact_match_only():
    # Unknown on both sides — exact string match still allowed
    assert submarket_score("Leesburg", "Leesburg") == 100.0
    # Unknown vs known, non-exact — excluded, never a wildcard match
    assert submarket_score("Leesburg", "Tysons") is None
    assert submarket_score("Tysons", "Leesburg") is None


def test_null_submarket_never_crashes_and_excludes():
    assert submarket_score(None, "Tysons") is None
    assert submarket_score("Tysons", None) is None
    assert submarket_score(None, None) is None


# ── Building-class factor ─────────────────────────────────────────────────────

def test_same_class_scores_100():
    assert class_score("Class B", "Class B") == 100.0
    assert class_score("A", "Class A") == 100.0   # format-tolerant


def test_class_upgrade_scores_70():
    # B-tenant moving up to an A-property
    assert class_score("Class B", "Class A") == 70.0
    assert class_score("Class C", "Class B") == 70.0


def test_class_downgrade_scores_55():
    assert class_score("Class A", "Class B") == 55.0
    assert class_score("Class B", "Class C") == 55.0


def test_two_class_gap_visible_both_directions():
    """Two-class gaps score 30 (low but visible), never excluded."""
    assert class_score("Class A", "Class C") == 30.0
    assert class_score("Class C", "Class A") == 30.0


def test_null_or_unparseable_class_is_neutral_50_never_crash():
    assert class_score(None, "Class A") == 50.0
    assert class_score("Class B", None) == 50.0
    assert class_score(None, None) == 50.0
    assert class_score("Trophy", "Class A") == 50.0  # unparseable → neutral


def test_same_class_pair_produces_valid_match():
    """Same-class pairs must score the class factor at 100 and produce a
    non-None composite match. This regression test locks the fix for: when a
    tenant's current_building_class matches a property's building class
    exactly (e.g. both Class B), the pair must not be excluded."""
    # Same class, exact submarket — full composite
    match = compute_match("Tysons", "Tysons", "Class B", "Class B", 5000, 5000)
    assert match is not None, "Same-class pair must not be excluded"
    assert match["class_score"] == 100.0, "Same class must score 100"
    assert match["score"] == 100.0, "Perfect match (same submarket + class + SF) = 100"

    # Same class, adjacent submarket
    match = compute_match("Reston", "Herndon", "Class A", "Class A", 5000, 5000)
    assert match is not None
    assert match["class_score"] == 100.0
    # 0.40·60 (adjacent) + 0.30·100 (same class) + 0.30·100 (delta 0) = 84.0
    assert match["score"] == pytest.approx(84.0)
    assert match["adjacent"] is True


def test_same_class_pair_persists_through_tenant_match_surface(db_session):
    """A Class B tenant matched against a Class B property must appear in the
    matched_tenants surface with the correct class score and composite, not be
    excluded."""
    from app.api.routes.properties import _compute_matched_tenants

    # Create a Class B property, Tysons, 5000 SF available
    prop = _make_property(db_session, property_id="NVA-B", submarket="Tysons",
                         asset_class="Class B", sf_avail=5000)
    # Create a Class B tenant, Tysons, 5000 SF needed
    tenant = _make_company(db_session, company_id="CO-B", submarket="Tysons",
                          building_class="Class B", sf_needed=5000)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    me = next((m for m in matched if m.company_id == "CO-B"), None)
    assert me is not None, "Same-class tenant must appear in matched_tenants"
    assert me.match_score == 100.0, "Perfect match (same submarket + class + SF) must be 100"


# ── SF-fit gate + gradient ────────────────────────────────────────────────────

def test_800_sf_gate_excludes_before_scoring():
    assert sf_delta_passes_gate(5000, 5000 + MAX_SF_DELTA + 1) is False
    assert sf_delta_passes_gate(5000, 5000 + MAX_SF_DELTA) is True
    # compute_match returns None on a gate failure regardless of perfect
    # submarket/class fit — the gate runs first.
    assert compute_match("Tysons", "Tysons", "Class A", "Class A", 5000, 5801) is None


def test_sf_gate_null_safe():
    assert sf_delta_passes_gate(None, 5000) is False
    assert sf_delta_passes_gate(5000, None) is False
    assert sf_delta_passes_gate(0, 0) is False


def test_sf_fit_gradient():
    assert sf_fit_score(5000, 5000) == 100.0           # delta 0
    assert sf_fit_score(5000, 5800) == 60.0            # delta 800 (cutoff)
    assert sf_fit_score(5000, 5400) == 80.0            # delta 400 (midpoint)


# ── Composite math (hand-calculated) ──────────────────────────────────────────

def test_composite_hand_calculated_example():
    # Exact submarket (100), B-tenant → A-property (70),
    # 4,800 SF needed vs 5,000 SF available → delta 200 → 100 − 40·(200/800) = 90
    # Composite = 0.40·100 + 0.30·70 + 0.30·90 = 40 + 21 + 27 = 88.0
    match = compute_match("Tysons", "Tysons", "Class B", "Class A", 4800, 5000)
    assert match is not None
    assert match["submarket_score"] == 100.0
    assert match["class_score"] == 70.0
    assert match["sf_fit_score"] == 90.0
    assert match["score"] == pytest.approx(88.0)
    assert match["adjacent"] is False


def test_composite_adjacent_example_flags_badge():
    # Adjacent submarket (60), same class (100), delta 0 (100)
    # Composite = 0.40·60 + 0.30·100 + 0.30·100 = 24 + 30 + 30 = 84.0
    match = compute_match("Herndon", "Reston", "Class B", "Class B", 5000, 5000)
    assert match is not None
    assert match["score"] == pytest.approx(84.0)
    assert match["adjacent"] is True


def test_weights_sum_to_one():
    assert sum(MATCH_SCORE_WEIGHTS.values()) == pytest.approx(1.0)


# ── Pairing generators (in-memory DB) ─────────────────────────────────────────

def test_matched_tenants_apply_gate_and_sort_descending(db_session):
    from app.api.routes.properties import _compute_matched_tenants

    prop = _make_property(db_session, sf_avail=5000, asset_class="Class A")
    # Survives: exact submarket, class B → A upgrade, delta 200
    _make_company(db_session, company_id="CO-GOOD", submarket="Tysons",
                  building_class="Class B", sf_needed=4800)
    # Survives with lower score: adjacent submarket
    _make_company(db_session, company_id="CO-ADJ", submarket="Reston",
                  building_class="Class A", sf_needed=5000)
    # Excluded: SF delta 900 > 800 despite perfect submarket/class
    _make_company(db_session, company_id="CO-FAR-SF", submarket="Tysons",
                  building_class="Class A", sf_needed=5900)
    # Excluded: non-adjacent submarket
    _make_company(db_session, company_id="CO-FAR-GEO", submarket="Alexandria (Old Town)",
                  building_class="Class A", sf_needed=5000)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    ids = [m.company_id for m in matched]
    assert "CO-FAR-SF" not in ids, "800 SF gate must exclude before scoring"
    assert "CO-FAR-GEO" not in ids, "Non-adjacent submarket pair must be excluded"
    assert set(ids) == {"CO-GOOD", "CO-ADJ"}
    scores = [m.match_score for m in matched]
    assert scores == sorted(scores, reverse=True), "Pairings must sort descending by composite"
    adj = {m.company_id: m.adjacent_submarket for m in matched}
    assert adj["CO-ADJ"] is True
    assert adj["CO-GOOD"] is False


def test_matched_tenants_null_submarket_and_class_never_500(db_session):
    from app.api.routes.properties import _compute_matched_tenants

    prop = _make_property(db_session, sf_avail=5000)
    # Null submarket — excluded gracefully, no crash
    _make_company(db_session, company_id="CO-NOSUB", submarket=None,
                  building_class="Class B", sf_needed=5000)
    # Null class — neutral 50, still included
    _make_company(db_session, company_id="CO-NOCLASS", submarket="Tysons",
                  building_class=None, sf_needed=5000)
    db_session.commit()

    matched = _compute_matched_tenants(prop, db_session)
    ids = {m.company_id for m in matched}
    assert "CO-NOSUB" not in ids
    assert "CO-NOCLASS" in ids
    # Null class company: 0.4·100 + 0.3·50 + 0.3·100 = 85.0
    no_class = next(m for m in matched if m.company_id == "CO-NOCLASS")
    assert no_class.match_score == pytest.approx(85.0)


def test_matched_properties_empty_company_data_and_reverse_direction(db_session):
    from app.api.routes.companies import _compute_matched_properties

    # Company with no SF need → empty list, no crash
    co_empty = _make_company(db_session, company_id="CO-EMPTY", sf_needed=None)
    db_session.commit()
    assert _compute_matched_properties(co_empty, db_session) == []

    # Company → property direction also honours adjacency + gate
    _make_property(db_session, property_id="NVA-ADJ", submarket="Herndon", sf_avail=5000)
    _make_property(db_session, property_id="NVA-FAR", submarket="Crystal City", sf_avail=5000)
    co = _make_company(db_session, company_id="CO-RESTON", submarket="Reston", sf_needed=5000)
    db_session.commit()

    matched = _compute_matched_properties(co, db_session)
    ids = {m.property_id for m in matched}
    assert "NVA-ADJ" in ids, "Reston → Herndon adjacency must work in this direction too"
    assert "NVA-FAR" not in ids
    assert next(m for m in matched if m.property_id == "NVA-ADJ").adjacent_submarket is True


def test_briefing_actions_handle_empty_company_list(db_session):
    from app.services.output_engine import _compute_tenant_actions

    _make_property(db_session, sf_avail=5000)
    db_session.commit()
    # No companies at all — must return [] without raising
    assert _compute_tenant_actions(db_session) == []


def test_briefing_actions_set_adjacent_flag_and_sort(db_session):
    from app.services.output_engine import _compute_tenant_actions

    _make_property(db_session, property_id="NVA-A", submarket="Tysons", sf_avail=5000)
    _make_property(db_session, property_id="NVA-B", submarket="Herndon", sf_avail=5000)
    _make_company(db_session, company_id="CO-T", submarket="Tysons", sf_needed=5000)
    db_session.commit()

    actions = _compute_tenant_actions(db_session)
    by_prop = {a.property_id: a for a in actions}
    assert by_prop["NVA-A"].adjacent_submarket is False
    assert by_prop["NVA-B"].adjacent_submarket is True
    scores = [a.match_score for a in actions]
    assert scores == sorted(scores, reverse=True)


# ── Non-adjacent leak regression (production pairs from PR #13 verification) ──
# An Arlington (Clarendon) tenant was observed matched to a Fairfax City
# property (score 50) and two Alexandria (Old Town) properties (score 40).
# Neither submarket pair is identical nor adjacent in SUBMARKET_ADJACENCY, so
# all of them must be excluded entirely: no card, no score, no outreach.

def test_leaked_pair_clarendon_fairfax_city_excluded():
    assert submarket_score("Arlington (Clarendon)", "Fairfax City") is None
    assert compute_match("Arlington (Clarendon)", "Fairfax City",
                         None, "Class B", 5000, 5000) is None


def test_leaked_pair_clarendon_old_town_excluded():
    assert submarket_score("Arlington (Clarendon)", "Alexandria (Old Town)") is None
    assert compute_match("Arlington (Clarendon)", "Alexandria (Old Town)",
                         None, "Class B", 5000, 5000) is None


def test_null_class_cannot_prop_up_non_adjacent_pair():
    """The submarket gate runs before any blending — the neutral-50 class
    factor must never carry a non-adjacent pair into the composite."""
    match = compute_match("Arlington (Clarendon)", "Fairfax City",
                          None, None, 5000, 5000)
    assert match is None


def test_reston_tenant_tysons_property_adjacent_factor_60():
    match = compute_match("Reston", "Tysons", "Class B", "Class B", 5000, 5000)
    assert match is not None
    assert match["adjacent"] is True
    assert match["submarket_score"] == 60.0


def test_same_submarket_factor_100():
    match = compute_match("Tysons", "Tysons", "Class B", "Class B", 5000, 5000)
    assert match is not None
    assert match["adjacent"] is False
    assert match["submarket_score"] == 100.0


def test_null_class_on_valid_adjacent_pair_still_matches_at_50():
    """Gate fix must not over-exclude: a valid adjacent pair with null class
    on either side still matches with the class factor at neutral 50."""
    match = compute_match("Reston", "Tysons", None, "Class B", 5000, 5000)
    assert match is not None
    assert match["class_score"] == 50.0
    # 0.40·60 + 0.30·50 + 0.30·100 = 24 + 15 + 30 = 69.0
    assert match["score"] == pytest.approx(69.0)


def test_leaked_pairs_absent_from_all_three_surfaces(db_session):
    """The three pairing surfaces (property-detail Matched Tenants,
    company-detail Matched Properties, Dashboard Section A) all consume the
    shared module — none may display a non-adjacent pair."""
    from app.api.routes.properties import _compute_matched_tenants
    from app.api.routes.companies import _compute_matched_properties
    from app.services.output_engine import _compute_tenant_actions

    prop_ffx = _make_property(db_session, property_id="NVA-FFX", submarket="Fairfax City", sf_avail=5000)
    prop_ot1 = _make_property(db_session, property_id="NVA-OT1", submarket="Alexandria (Old Town)", sf_avail=5000)
    prop_ot2 = _make_property(db_session, property_id="NVA-OT2", submarket="Alexandria (Old Town)", sf_avail=4800)
    tenant = _make_company(db_session, company_id="CO-CLRN",
                           submarket="Arlington (Clarendon)",
                           building_class=None, sf_needed=5000)
    db_session.commit()

    # Surface 1: property-detail Matched Tenants
    for prop in (prop_ffx, prop_ot1, prop_ot2):
        assert all(m.company_id != "CO-CLRN" for m in _compute_matched_tenants(prop, db_session)), (
            f"Non-adjacent tenant leaked onto {prop.property_id} Matched Tenants card"
        )

    # Surface 2: company-detail Matched Properties
    matched_props = {m.property_id for m in _compute_matched_properties(tenant, db_session)}
    assert matched_props.isdisjoint({"NVA-FFX", "NVA-OT1", "NVA-OT2"}), (
        "Non-adjacent properties leaked onto the company's Matched Properties card"
    )

    # Surface 3: Dashboard Section A
    pairs = {(a.property_id, a.tenant_company_id) for a in _compute_tenant_actions(db_session)}
    for pid in ("NVA-FFX", "NVA-OT1", "NVA-OT2"):
        assert (pid, "CO-CLRN") not in pairs, (
            f"Non-adjacent pair {pid} ↔ CO-CLRN leaked into Dashboard Section A"
        )


# ── Tenant-side email privacy on adjacent matches ─────────────────────────────

def test_tenant_side_email_names_property_submarket_never_address():
    """For an adjacent match the tenant-side email prompt must anchor on the
    property's ACTUAL submarket — and the street address must never reach
    the model in any tenant-side instruction."""
    from app.services.property_outreach_service import _build_tenant_side

    prop_dict = {
        "address":   "1234 Secret St, Herndon, VA 20170",
        "submarket": "Herndon",          # property's actual submarket
        "asset_class": "Class B",
        "sf_avail":  5000,
        "in_place_rent_psf": 32.0,
    }
    tenant_dict = {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_sf_occupied": 5000,
        "lease_expiry_months": 10,
        "current_submarket": "Reston",   # adjacent tenant submarket
        "primary_contact_name": "Pat Lee",
    }
    prompt = _build_tenant_side(prop_dict, tenant_dict)
    combined = prompt["system"] + "\n" + prompt["user"]
    assert "Herndon" in combined, "Email must name the property's actual submarket"
    assert "1234 Secret St" not in combined, "Street address must never reach tenant-side copy"
    assert "NEVER reveal street address" in combined
