"""
Listing-aware framing for the ACQUISITION outreach prompt (_build_acquisition).

Contract:
  - is_listed (listed_for_sale=True)  → prompt must acknowledge the property is
    actively marketed ("on the market") and must NOT pitch an off-market sale.
  - is_listed False (listed_for_sale=False) → prompt keeps the existing
    "off-market" framing.

The prompt strings are inspected directly (no live LLM / DB / network).
"""
import pytest


def _import_svc():
    import app.services.property_outreach_service as svc
    return svc


def _property(listed_for_sale, submarket="Fairfax City"):
    return {
        "property_id": "TST-ACQ-001",
        "address": "9000 Acquisition Way, Fairfax, VA",
        "submarket": submarket,
        "total_sf": 30000,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 25.0,
        "market_cap_rate": 6.5,
        "listed_for_sale": listed_for_sale,
        "vacancy_pct": 15.0,
        "days_on_market": 60,
        "dominant_score_type": "hold_period",
    }


def test_acquisition_prompt_on_market_when_listed():
    svc = _import_svc()
    prompt = svc._build_acquisition(_property(listed_for_sale=True), target_type="owner")
    combined = prompt["system"] + prompt["user"]
    assert "on the market" in combined, (
        "Listed property: acquisition prompt must acknowledge the property is on the market"
    )
    # When listed we must not pitch a potential off-market sale.
    assert "off-market sale" not in combined, (
        "Listed property: acquisition prompt must not pitch an off-market sale"
    )


def test_acquisition_prompt_off_market_when_not_listed():
    svc = _import_svc()
    prompt = svc._build_acquisition(_property(listed_for_sale=False), target_type="owner")
    combined = prompt["system"] + prompt["user"]
    assert "off-market" in combined, (
        "Unlisted property: acquisition prompt must keep the off-market framing"
    )
