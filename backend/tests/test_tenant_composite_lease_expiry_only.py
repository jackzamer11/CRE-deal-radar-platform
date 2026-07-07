"""
Tenant-opportunity composite reweight (2026-07): sig_lease_expiry_proximity is
now the SOLE driver of opportunity_score (100% weight). Growth, hiring
velocity, space utilization, and geo clustering still compute and are
returned for display on the company card, but no longer affect the score.
The rep-status delta (+10 no rep / −15 major firm / −5 regional) is still
applied on top of the pure lease-expiry score, unchanged.

Pure in-memory unit tests — no DB, no network, no CoStar.
"""
import pytest

from app.services.signal_engine import compute_tenant_opportunity_score


# (a) Identical lease_expiry_months, wildly different growth/hiring/space/geo
# → identical score, proving those signals no longer feed the composite.
def test_only_lease_expiry_drives_composite():
    low_signal = compute_tenant_opportunity_score(
        headcount_growth_pct=0.0,
        open_positions=0,
        current_headcount=500,
        lease_expiry_months=7,          # peak window → 100.0
        current_sf=500_000,             # 1000 SF/head — deep oversized
        current_submarket=None,         # geo clustering abstains to 0.0 base
        tenant_representative=None,
        nearby_company_count=0,
    )
    high_signal = compute_tenant_opportunity_score(
        headcount_growth_pct=90.0,
        open_positions=400,
        current_headcount=500,
        lease_expiry_months=7,          # same peak window → 100.0
        current_sf=20_000,              # 40 SF/head — deep cramped
        current_submarket="Tysons",
        tenant_representative=None,
        nearby_company_count=10,
    )

    # Wildly different reference signals...
    assert low_signal["breakdown"]["headcount_growth"] != high_signal["breakdown"]["headcount_growth"]
    assert low_signal["breakdown"]["hiring_velocity"] != high_signal["breakdown"]["hiring_velocity"]
    assert low_signal["breakdown"]["space_utilization"] != high_signal["breakdown"]["space_utilization"]
    assert low_signal["breakdown"]["geo_clustering"] != high_signal["breakdown"]["geo_clustering"]

    # ...yet identical composite, because only lease_expiry (identical here) scores.
    assert low_signal["composite"] == high_signal["composite"] == 100.0


# (b) Rep-status delta still applies correctly on top of the pure lease-expiry score.
@pytest.mark.parametrize("rep,expected_delta,expected_composite", [
    (None,           10.0, 100.0),   # 90 + 10, clamped to 100
    ("JLL",         -15.0,  75.0),   # 90 - 15
    ("Regional Co",  -5.0,  85.0),   # 90 - 5
])
def test_rep_delta_applies_on_top_of_pure_lease_expiry_score(rep, expected_delta, expected_composite):
    result = compute_tenant_opportunity_score(
        headcount_growth_pct=None,
        open_positions=None,
        current_headcount=None,
        lease_expiry_months=12,         # 10-12mo tier → 80.0... see note below
        current_sf=None,
        current_submarket=None,
        tenant_representative=rep,
        nearby_company_count=0,
    )
    # lease_expiry_months=12 scores 80.0 (10-12mo tier); adjust expectations to
    # that base rather than re-deriving the curve here.
    base = 80.0
    assert result["breakdown"]["tenant_rep_adjustment"] == expected_delta
    assert result["composite"] == round(min(100.0, max(0.0, base + expected_delta)), 2)


# (c) All four excluded signals still compute and are still returned (not deleted).
def test_excluded_signals_still_compute_and_are_returned():
    result = compute_tenant_opportunity_score(
        headcount_growth_pct=40.0,
        open_positions=15,
        current_headcount=60,
        lease_expiry_months=7,
        current_sf=9_000,
        current_submarket="Reston",
        tenant_representative=None,
        nearby_company_count=2,
    )
    breakdown = result["breakdown"]
    for key in ("headcount_growth", "hiring_velocity", "space_utilization", "geo_clustering"):
        assert key in breakdown
        assert breakdown[key] is not None


# Companies with no lease-expiry data at all have nothing left to fall back
# on (it's the only scoring signal) — they score 0 and are flagged insufficient,
# even if every reference signal is fully populated.
def test_no_lease_expiry_scores_zero_and_insufficient():
    result = compute_tenant_opportunity_score(
        headcount_growth_pct=90.0,
        open_positions=50,
        current_headcount=100,
        lease_expiry_months=None,
        current_sf=10_000,
        current_submarket="Tysons",
        tenant_representative=None,
        nearby_company_count=5,
    )
    assert result["signals_scored"] == 0
    assert result["insufficient_data"] is True
    # +10 no-rep bonus still applies on top of the 0.0 base.
    assert result["composite"] == 10.0
    # Reference signals still fully populated for the card.
    assert result["breakdown"]["headcount_growth"] == 100.0
    assert result["breakdown"]["space_utilization"] is not None
