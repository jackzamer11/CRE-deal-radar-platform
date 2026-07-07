"""
Scoring-adjustment contract tests (2026-07 recalibration).

Locks two deliberate changes to the tenant-opportunity engine:

  1. sig_tenant_rep: major-firm rep penalty softened from −25 to −15.
     The no-rep bonus (+10) and regional/other-rep penalty (−5) are
     UNCHANGED and pinned here so this edit can't silently drift them.

  2. sig_space_utilization: the normal-utilization band — STRICTLY between
     150 and 210 SF/head — now scores a neutral 50.0 instead of a hard 0.0.
     Boundary contract: the cramped tiers own their upper edge (exactly
     150 → 35.0) and the oversized tiers own their lower edge (exactly
     210 → 25.0); only the open interval (150, 210) is neutral.

All inputs are fabricated in-memory — pure-function boundary pins only.
No DB, no network, no CoStar.
"""
import pytest

from app.services.signal_engine import (
    compute_tenant_opportunity_score,
    sig_space_utilization,
    sig_tenant_rep,
)


# ── sig_tenant_rep delta pins ──────────────────────────────────────────────────
@pytest.mark.parametrize("rep,expected_delta", [
    (None,                    10.0),   # no rep on record — bonus unchanged
    ("",                      10.0),   # blank string counts as no rep
    ("   ",                   10.0),   # whitespace-only counts as no rep
    ("JLL",                  -15.0),   # major firm — softened from −25
    ("CBRE",                 -15.0),   # major firm
    ("Cushman & Wakefield",  -15.0),   # major firm
    ("cbre — arlington office", -15.0),  # case-insensitive substring match
    ("Local Brokerage Partners", -5.0),  # regional/other — penalty unchanged
])
def test_tenant_rep_deltas(rep, expected_delta):
    assert sig_tenant_rep(rep) == expected_delta


# ── Composite reflects −15 (not −25) for a major-firm-rep tenant ───────────────
# Inputs chosen so every signal scores (no abstains) and the composite sits
# mid-range, far from the 0/100 clamps:
#   headcount_growth 30%          → 65.0  (weight .25 → 16.25)
#   hiring velocity 10/50 = 20%   → 80.0  (weight .20 → 16.00)
#   lease expiry 7 mo (peak)      → 100.0 (weight .30 → 30.00)
#   utilization 5,000/50 = 100    → 80.0  (weight .20 → 16.00)
#   geo clustering (1 nearby)     → 30.0  (weight .05 →  1.50)
#   base composite                          = 79.75
_FULL_SIGNAL_KWARGS = dict(
    headcount_growth_pct=30.0,
    open_positions=10,
    current_headcount=50,
    lease_expiry_months=7,
    current_sf=5_000,
    current_submarket="Tysons",
    nearby_company_count=1,
)
_BASE_COMPOSITE = 79.75


def test_major_firm_rep_composite_reflects_minus_15_not_minus_25():
    result = compute_tenant_opportunity_score(
        tenant_representative="JLL", **_FULL_SIGNAL_KWARGS,
    )
    assert result["breakdown"]["tenant_rep_adjustment"] == -15.0
    assert result["composite"] == _BASE_COMPOSITE - 15.0   # 64.75
    assert result["composite"] != _BASE_COMPOSITE - 25.0   # old −25 must be gone


def test_no_rep_composite_bonus_unchanged():
    result = compute_tenant_opportunity_score(
        tenant_representative=None, **_FULL_SIGNAL_KWARGS,
    )
    assert result["breakdown"]["tenant_rep_adjustment"] == 10.0
    assert result["composite"] == _BASE_COMPOSITE + 10.0   # 89.75


def test_regional_rep_composite_penalty_unchanged():
    result = compute_tenant_opportunity_score(
        tenant_representative="Local Brokerage Partners", **_FULL_SIGNAL_KWARGS,
    )
    assert result["breakdown"]["tenant_rep_adjustment"] == -5.0
    assert result["composite"] == _BASE_COMPOSITE - 5.0    # 74.75


# ── Space utilization: neutral band scores 50.0, not 0.0 ───────────────────────
@pytest.mark.parametrize("sf,headcount", [
    (15_100, 100),   # 151 SF/head — just inside the lower edge
    (18_000, 100),   # 180 SF/head — mid-band
    (20_900, 100),   # 209 SF/head — just inside the upper edge
])
def test_utilization_strictly_inside_150_210_scores_neutral_50(sf, headcount):
    score = sig_space_utilization(sf, headcount)
    assert score == 50.0
    assert score != 0.0


# ── Boundary contract at exactly 150 and exactly 210 ──────────────────────────
def test_utilization_boundary_exactly_150_belongs_to_cramped_tier():
    # 150 is the inclusive UPPER edge of the mild-cramped tier (<= 150 → 35.0);
    # the neutral band starts strictly above it.
    assert sig_space_utilization(15_000, 100) == 35.0


def test_utilization_boundary_exactly_210_belongs_to_oversized_tier():
    # 210 is the inclusive LOWER edge of the mild-oversized tier (>= 210 → 25.0);
    # the neutral band ends strictly below it.
    assert sig_space_utilization(21_000, 100) == 25.0


# ── Adjacent tiers unshifted by the neutral-band edit ──────────────────────────
@pytest.mark.parametrize("sf,headcount,expected", [
    (9_000,  100, 100.0),  # 90  — deep-cramped upper edge
    (11_000, 100, 80.0),   # 110 — cramped tier upper edge
    (13_000, 100, 58.0),   # 130 — moderate-cramped upper edge
    (24_000, 100, 45.0),   # 240 — mid-oversized lower edge
    (28_000, 100, 70.0),   # 280 — deep-oversized lower edge
])
def test_utilization_adjacent_tiers_unchanged(sf, headcount, expected):
    assert sig_space_utilization(sf, headcount) == expected


@pytest.mark.parametrize("sf,headcount", [
    (None, 100),   # SF unknown
    (5_000, None), # headcount unknown
    (5_000, 0),    # zero headcount — divide-by-zero guard
])
def test_utilization_abstains_on_missing_inputs(sf, headcount):
    # Abstain (None) must be preserved — neutral 50.0 is only for a KNOWN
    # ratio inside the normal band, never a substitute for missing data.
    assert sig_space_utilization(sf, headcount) is None


def test_composite_includes_neutral_utilization_not_zero():
    """A tenant with normal utilization (180 SF/head) must have 50.0 flow into
    the composite instead of the old hard 0.0 dragging it down."""
    result = compute_tenant_opportunity_score(
        headcount_growth_pct=30.0,
        open_positions=20,          # 20/100 = 20% velocity → 80.0
        current_headcount=100,
        lease_expiry_months=7,
        current_sf=18_000,          # 180 SF/head — neutral band
        current_submarket="Tysons",
        tenant_representative=None,
        nearby_company_count=1,
    )
    assert result["breakdown"]["space_utilization"] == 50.0
    # base = .25·65 + .20·80 + .30·100 + .20·50 + .05·30 = 73.75; +10 no-rep bonus
    assert result["composite"] == 83.75
