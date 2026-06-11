"""
Building class format normalization fix — lock CoStar single-letter vs platform "Class X" handling.

Root cause: CoStar exports building class as single letters ("A", "B", "C").
The platform's internal format is "Class A", "Class B", "Class C" (from tenant dropdown).
If properties are stored with single-letter values while tenants have "Class X",
the scoring comparison fails: "B" != "Class B" → both fall back to neutral 50 → no exclusions ever work.

Fixes:
  1. Normalization helper in match_scoring.py: _normalize_class() handles both formats
  2. Backfill migration: convert any single-letter values in the DB to "Class X" format
  3. Defensive _class_rank: always normalizes before ranking
  4. Test: "B" property + "Class B" tenant = 100 (same class); "A" vs "C" = excluded; etc.

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
from app.services.match_scoring import (
    _normalize_class,
    class_score,
    compute_match,
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


# ── Normalization helper tests ────────────────────────────────────────────────

def test_normalize_class_platform_format():
    """Accept platform format: 'Class A', 'Class B', 'Class C'."""
    assert _normalize_class("Class A") == "Class A"
    assert _normalize_class("Class B") == "Class B"
    assert _normalize_class("Class C") == "Class C"


def test_normalize_class_single_letter():
    """Accept CoStar single-letter format: 'A', 'B', 'C'."""
    assert _normalize_class("A") == "Class A"
    assert _normalize_class("B") == "Class B"
    assert _normalize_class("C") == "Class C"


def test_normalize_class_case_insensitive():
    """Accept case variants: 'a', 'class b', 'CLASS A', 'cLaSs B'."""
    assert _normalize_class("a") == "Class A"
    assert _normalize_class("b") == "Class B"
    assert _normalize_class("class a") == "Class A"
    assert _normalize_class("CLASS B") == "Class B"
    assert _normalize_class("cLaSs C") == "Class C"


def test_normalize_class_whitespace_tolerant():
    """Accept leading/trailing whitespace."""
    assert _normalize_class("  Class A  ") == "Class A"
    assert _normalize_class("  B  ") == "Class B"


def test_normalize_class_invalid_returns_none():
    """Invalid input (null, wrong letter, unparseable) returns None."""
    assert _normalize_class(None) is None
    assert _normalize_class("") is None
    assert _normalize_class("D") is None
    assert _normalize_class("Trophy") is None
    assert _normalize_class("Class D") is None


# ── Class fit scoring with both formats ────────────────────────────────────────

def test_class_score_single_letter_property_vs_platform_tenant():
    """CoStar property ("B") + platform tenant ("Class B") = 100 (same class).
    This is the core regression: before the fix this would return 50 (neutral)."""
    assert class_score("Class B", "B") == 100.0
    assert class_score("Class A", "A") == 100.0
    assert class_score("Class C", "C") == 100.0


def test_class_score_mixed_formats_upgrade():
    """Tenant upgrade: single-letter property ("A") vs platform tenant ("Class B") = 70."""
    assert class_score("Class B", "A") == 70.0


def test_class_score_mixed_formats_downgrade():
    """Tenant downgrade: single-letter property ("B") vs platform tenant ("Class A") = 55."""
    assert class_score("Class A", "B") == 55.0


def test_class_score_mixed_formats_two_classes_apart():
    """Two classes apart: single-letter property ("C") vs platform tenant ("Class A") = None (excluded)."""
    assert class_score("Class A", "C") is None
    assert class_score("Class C", "A") is None


def test_compute_match_single_letter_property_format():
    """Full composite match with single-letter property class (CoStar format).
    Same submarket, same class, delta 0 → full 100."""
    match = compute_match("Tysons", "Tysons", "Class B", "B", 5000, 5000)
    assert match is not None
    assert match["class_score"] == 100.0
    assert match["score"] == 100.0


def test_compute_match_adjacent_with_single_letter():
    """Adjacent submarket + mixed class formats must work."""
    match = compute_match("Reston", "Herndon", "Class B", "B", 5000, 5000)
    assert match is not None
    assert match["class_score"] == 100.0
    # 0.40·60 (adjacent) + 0.30·100 (same class) + 0.30·100 (delta 0) = 84.0
    assert match["score"] == pytest.approx(84.0)
    assert match["adjacent"] is True


def test_compute_match_class_a_vs_c_single_letter_excluded():
    """Class A tenant must not match Class C property, even with single-letter format."""
    match = compute_match("Tysons", "Tysons", "Class A", "C", 5000, 5000)
    assert match is None, "Two-class gap (A↔C) must be excluded regardless of format"


# ── End-to-end pairing with mixed formats ─────────────────────────────────────

def test_matched_properties_single_letter_class_property(db_session):
    """Property with single-letter class ('B') must match tenant with platform format ('Class B')."""
    from app.api.routes.companies import _compute_matched_properties

    # Create a property with single-letter class (like a CoStar import)
    prop = Property(
        property_id="NVA-SL",
        address="123 Single Letter Way, Tysons, VA",
        submarket="Tysons",
        asset_class="B",  # single letter — the bug case
        total_sf=50000,
        year_built=2010,
        owner_name="Owner LLC",
        in_place_rent_psf=38.0,
        market_rent_psf=39.0,
        market_cap_rate=6.5,
        sf_avail=5000,
    )
    db_session.add(prop)

    # Create a tenant with platform format ('Class B')
    co = Company(
        company_id="CO-SL",
        name="Single Letter Tenant",
        industry="Technology",
        current_headcount=50,
        current_submarket="Tysons",
        current_building_class="Class B",  # platform format
        current_sf_occupied=5000,
        lease_expiry_months=10,
        opportunity_score=80.0,
        priority="HIGH",
    )
    db_session.add(co)
    db_session.commit()

    matched = _compute_matched_properties(co, db_session)
    me = next((m for m in matched if m.property_id == "NVA-SL"), None)
    assert me is not None, "Single-letter property must match platform-format tenant"
    # 0.40·100 (exact) + 0.30·100 (same class) + 0.30·100 (delta 0) = 100.0
    assert me.match_score == pytest.approx(100.0)
    assert any("Class fit 100/100" in r for r in me.match_reasons)


def test_matched_properties_class_a_never_matches_c():
    """Regression: Class A tenant must never match Class C property, even if single-letter."""
    from app.services.match_scoring import compute_match
    # Single-letter C property
    match = compute_match("Tysons", "Tysons", "Class A", "C", 5000, 5000)
    assert match is None


def test_normalization_preserves_null_and_invalid():
    """Null and invalid values must stay null — never crash or default to neutral."""
    # Null
    assert _normalize_class(None) is None
    # Invalid letter
    assert _normalize_class("X") is None
    # Non-string
    assert _normalize_class(123) is None
