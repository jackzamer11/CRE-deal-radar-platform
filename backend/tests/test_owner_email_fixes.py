"""
Owner-email (Tenant Match property-side) + submarket-enum fixes.

Fix 1 — the CBRE vacancy rate appears EXACTLY ONCE in the owner email: only as
        the dedicated standalone sentence (vacancy > 15%); the body paragraph
        never carries vacancy/CBRE/market statistics, at three layers (context
        withheld from the model, system-prompt prohibition, post-LLM strip).
Fix 2 — available SF in the owner email is rounded to the nearest 100 with the
        same rounding helper as the tenant email; raw figures never shown.
Fix 3 — "Herndon" is a first-class submarket everywhere submarkets are
        enumerated, with PROVISIONAL (≈ Dulles Corridor) benchmark data.
Fix 4 — generic market filler ("...offers a compelling mix of amenities and
        accessibility") is removed from the Tenant Match tenant-side email.

Offline: _chat is mocked (no live LLM / DB / network).
"""
import pathlib
import re as _re
from unittest.mock import patch

import pytest

import app.services.property_outreach_service as svc


def _property(submarket="Tysons", sf_avail=5000):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": sf_avail,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "vacancy_pct": 27.0,
        "listed_for_sale": False,
        "asset_class": "office",
        "dominant_score_type": "tenant_match",
    }


def _tenant():
    return {
        "name": "ZZZ Test Co",
        "industry": "Technology",
        "current_submarket": "Tysons",
        "current_sf_occupied": 3500,
        "lease_expiry_months": 12,
        "primary_contact_name": "Jane Smith",
    }


def _make_mock_chat(extra_body=""):
    """Mock whose EMAIL body can carry an LLM-written market-stat / filler
    sentence, to prove the post-LLM strip removes it."""
    def _mock_chat(system, user, **kw):
        m = _re.search(r"Open the email with exactly '([^']+)'", system)
        salutation = m.group(1) if m else "Hi Jane Smith,"
        return (
            "SUBJECT: Opportunity\n"
            "EMAIL:\n"
            f"{salutation}\n\n"
            f"Quick note about a strong fit I'm tracking for you. {extra_body}\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\nJack Zamer\n571-205-6228\n"
            "OPENING:\no\nCORE:\nc\nPAIN_PROBE:\np\nCLOSE:\nx"
        )
    return _mock_chat


def _render_owner(prop, extra_body=""):
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(extra_body)):
        return svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_tenant(),
        )["email_body"]


# ── Fix 1 (1): vacancy rate appears EXACTLY once when vacancy > 15% ─────────────
def test_owner_vacancy_rate_appears_exactly_once():
    """Tysons (27.3% > 15%): even when the model embeds its own vacancy/CBRE
    sentence in the body, the rendered email carries the rate exactly once —
    as the dedicated standalone sentence."""
    body = _render_owner(
        _property(submarket="Tysons"),
        extra_body=(
            "With Tysons's vacancy rate at 27.3%, the market is soft right now. "
            "CBRE Q1 2026 pegs Tysons vacancy near 27%."
        ),
    )
    assert body.count("vacancy rate at") == 1, body
    assert body.count("27.3%") == 1, body
    assert "qualified tenants are harder to come by — this is one worth moving on" in body
    assert "CBRE" not in body, "CBRE data must never survive into the owner email body"


# ── Fix 1 (2): body paragraph contains no CBRE / vacancy keywords ───────────────
def test_owner_body_paragraph_has_no_cbre_or_vacancy_keywords():
    """Stripping the one standalone sentence, the remainder of the owner email
    must contain no vacancy or CBRE keyword at all."""
    body = _render_owner(
        _property(submarket="Tysons"),
        extra_body="Vacancy across the submarket and CBRE data both favor acting now.",
    )
    standalone = svc._owner_vacancy_sentence("Tysons")
    remainder = body.replace(standalone, "").lower()
    assert "vacanc" not in remainder, f"vacancy keyword leaked into body: {remainder}"
    assert "cbre" not in remainder, f"CBRE keyword leaked into body: {remainder}"


def test_owner_prompt_withholds_market_stats_and_forbids_them():
    """Layer checks: the model never receives the vacancy % / CBRE benchmark, and
    the system prompt explicitly forbids market statistics in the body."""
    prompt = svc._build_tenant_match(_property(), None, "owner", tenant_dict=_tenant())
    combined = prompt["system"] + prompt["user"]
    assert "CBRE Q1 2026 Tysons" not in combined, "CBRE benchmark must not reach the model"
    assert "Market benchmark" not in prompt["user"]
    assert "Occupancy/Vacancy" not in prompt["user"], "property vacancy % must be withheld"
    assert "NEVER mention the vacancy rate, CBRE data, or any market statistics" in prompt["system"]
    # Max-3-paragraph structure is explicit: intro, body pitch, close.
    assert "Maximum 3 paragraphs total — intro, body pitch, close." in prompt["system"]


def test_owner_secondary_demand_line_belongs_to_close_paragraph():
    prompt = svc._build_tenant_match(
        _property(), None, "owner", has_secondary_demand=True, tenant_dict=_tenant()
    )
    assert "interest from other qualified tenants" in prompt["system"]
    assert "INSIDE the close paragraph" in prompt["system"], (
        "secondary-demand sentence must be instructed as part of the close paragraph"
    )


def test_owner_low_vacancy_still_uses_lease_timeline():
    """Vienna (5.2% ≤ 15%): no vacancy sentence anywhere; lease-timeline urgency."""
    body = _render_owner(_property(submarket="Vienna"))
    assert "vacancy rate at" not in body
    assert "lease expiring in approximately 12 months" in body


# ── Fix 2: owner email available SF rounded to nearest 100 ──────────────────────
def test_round_sf_helper():
    assert svc._round_sf_to_hundred(2954) == 3000
    assert svc._round_sf_to_hundred(5123) == 5100
    assert svc._round_sf_to_hundred(None) == 0
    assert svc._round_sf_to_hundred(0) == 0


def test_owner_prompt_rounds_available_sf():
    """Raw 2,954 SF must reach the model only as 3,000 SF."""
    prompt = svc._build_tenant_match(
        _property(sf_avail=2954), None, "owner", tenant_dict=_tenant()
    )
    combined = prompt["system"] + prompt["user"]
    assert "2,954" not in combined and "2954" not in combined, (
        "raw available SF must never reach the owner email prompt"
    )
    assert "3,000" in combined, "rounded available SF (3,000) expected in the prompt"


# ── Fix 3: Herndon is enumerated everywhere ─────────────────────────────────────
def test_herndon_in_submarket_enums():
    from app.api.routes.properties import VALID_SUBMARKETS, COSTAR_SUBMARKET_MAP
    from app.api.routes.companies import COSTAR_SUBMARKET_MAP as COMPANY_COSTAR_MAP
    from app.services.deal_creation_engine import ADJACENT_SUBMARKETS
    from app.config import SUBMARKET_BENCHMARKS, PROVISIONAL_SUBMARKETS, settings

    assert "Herndon" in VALID_SUBMARKETS
    assert COSTAR_SUBMARKET_MAP["herndon"] == "Herndon"
    assert COMPANY_COSTAR_MAP["herndon"] == "Herndon"
    assert "Herndon" in ADJACENT_SUBMARKETS
    assert "Herndon" in svc.CBRE_2026_BENCHMARKS
    assert "Herndon" in SUBMARKET_BENCHMARKS
    assert "Herndon" in settings.submarket_market_rent
    assert "Herndon" in settings.submarket_avg_psf
    assert "Herndon" in settings.submarket_cap_rate
    assert "Herndon" in settings.submarket_avg_dom


def test_herndon_benchmark_is_provisional():
    from app.config import SUBMARKET_BENCHMARKS, PROVISIONAL_SUBMARKETS
    assert "Herndon" in PROVISIONAL_SUBMARKETS
    src = SUBMARKET_BENCHMARKS["Herndon"]["source"]
    assert "PROVISIONAL" in src and "Dulles Corridor" in src


def test_herndon_in_frontend_dropdowns():
    root = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
    constants = (root / "constants.ts").read_text(encoding="utf-8")
    add_company = (root / "components" / "AddCompanyModal.tsx").read_text(encoding="utf-8")
    assert "'Herndon'" in constants, "Herndon missing from frontend SUBMARKETS constant"
    assert "'Herndon'" in add_company, "Herndon missing from AddCompanyModal submarket list"


# ── Fix 4: tenant-side generic market filler is removed ─────────────────────────
def test_tenant_side_filler_line_removed():
    """The 'compelling mix of amenities and accessibility' filler must be stripped
    from the Tenant Match tenant-side email with no replacement."""
    def _mock_chat(system, user, **kw):
        return (
            "SUBJECT: Tysons opportunity\n"
            "EMAIL:\n"
            "Hi Jane Smith,\n\n"
            "With your lease coming up, I wanted to flag a fit. "
            "The Tysons market offers a compelling mix of amenities and accessibility. "
            "It suits what you're after.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\nJack Zamer\n571-205-6228\n"
            "OPENING:\no\nCORE:\nc\nPAIN_PROBE:\np\nCLOSE:\nx"
        )

    with patch.object(svc, "_chat", side_effect=_mock_chat):
        body = svc.generate_property_outreach(
            property_dict=_property(),
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_tenant(),
        )["email_body"]
    assert "compelling mix" not in body, "generic market filler must be stripped"
    assert "amenities and accessibility" not in body
    # Neighbouring sentences survive the strip.
    assert "wanted to flag a fit" in body
    assert "It suits what you're after." in body


def test_tenant_side_prompt_forbids_filler():
    prompt = svc._build_tenant_side(_property(), _tenant())
    assert "compelling mix of amenities and accessibility" in prompt["system"], (
        "tenant-side system prompt must name the forbidden filler pattern"
    )
