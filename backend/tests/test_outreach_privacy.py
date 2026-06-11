"""
Privacy guardrail tests for property_outreach_service.

Two absolute rules under test:
  1. Tenant-side copy must NEVER contain the property street address.
  2. Property-side copy must NEVER contain the tenant company name.

Tests are fully offline: _chat() is patched so no network calls or API keys
are needed. No DB is touched.
"""
import pytest
from unittest.mock import patch, MagicMock

from app.services import property_outreach_service as svc

# ── Distinctive test tokens ────────────────────────────────────────────────────
ADDR        = "1234 Distinctive Test Blvd"
TENANT_NAME = "ZZZ Distinctive Tenant Co"

# ── Fake inputs ────────────────────────────────────────────────────────────────
FAKE_PROPERTY = {
    "address":     ADDR,
    "submarket":   "Arlington (Rosslyn)",
    "sf_avail":    5000,
    "vacancy_pct": 20.0,
    "in_place_rent_psf": None,
    "listed_for_sale":   False,
    "owner_name":  "Test Owner LLC",
    "landlord_representative": None,
    "days_on_market": 90,
    "years_owned": None,
    "loan_maturity_year": None,
    "dominant_score_type": None,
    "sales_contact": None,
    "asset_class": "office",
    "market_rent_psf": 46.85,
}

FAKE_TENANT = {
    "name":                  TENANT_NAME,
    "industry":              "Technology",
    "current_sf_occupied":   5000,
    "lease_expiry_months":   12,
    "current_submarket":     "Arlington (Rosslyn)",
    "primary_contact_name":  "Jane Smith",
    "headcount":             30,
}

# ── Mock LLM response (no sensitive tokens) ────────────────────────────────────
_MOCK_LLM_RESPONSE = """\
SUBJECT: Office Opportunity in Arlington (Rosslyn)
EMAIL:
Hi Jane Smith,

With your upcoming lease expiry, I wanted to reach out about an opportunity in Arlington (Rosslyn) that may be a strong fit for your team.

My name is Jack Zamer — I'm a commercial real estate agent focused exclusively on the Northern Virginia office market.

A professional services firm is seeking office space in the area. CBRE Q1 2026 data shows 20.6% vacancy in Arlington (Rosslyn).

I work exclusively in the Northern Virginia office market, focused on matching tenants to vacancies before they hit the open listings.

I'd welcome a brief call at your convenience.

Thank you,

Jack Zamer
Vice President, The Commercial Real Estate Group
571-205-6228
OPENING:
Good morning, I'm Jack Zamer from The Commercial Real Estate Group.
CORE:
We have identified an office opportunity in Arlington that matches your criteria. CBRE Q1 2026 shows 20.6% vacancy in this submarket. This property is well-positioned for your needs.
PAIN_PROBE:
What factors are most important to you as you evaluate your upcoming lease decision?
CLOSE:
I'd welcome a brief call at your convenience.\
"""


def _call_generate(direction: str, outreach_type: str = "tenant_match") -> tuple[dict, dict]:
    """
    Call generate_property_outreach with the fake inputs, patch _chat(),
    and return (result_dict, captured_prompts).
    """
    captured: dict = {}

    def _fake_chat(system: str, user: str) -> str:
        captured["system"] = system
        captured["user"]   = user
        return _MOCK_LLM_RESPONSE

    with patch.object(svc, "_chat", side_effect=_fake_chat):
        result = svc.generate_property_outreach(
            property_dict=FAKE_PROPERTY,
            outreach_type=outreach_type,
            direction=direction,
            tenant_dict=FAKE_TENANT,
        )
    return result, captured


# ── Test 1: Tenant-side copy must not contain the property street address ──────

def test_tenant_side_excludes_property_address():
    """
    Rule 1 — Tenant-side email (written TO the tenant company) must never
    reveal the property's street address anywhere in subject or body.
    """
    result, prompts = _call_generate(direction="tenant_side")

    # Check that the address was NOT sent to the LLM in the first place.
    # If it appears in the prompt, the LLM could reproduce it and we'd lose control.
    llm_input = (prompts.get("system", "") + " " + prompts.get("user", "")).lower()
    assert ADDR.lower() not in llm_input, (
        f"PRIVACY LEAK: property address '{ADDR}' was sent to the LLM "
        f"in the tenant-side outreach prompt — remove it from _build_tenant_side()"
    )

    # Check that the address does not appear in the rendered output.
    rendered = (
        (result.get("email_subject") or "") + " " +
        (result.get("email_body") or "")
    ).lower()
    assert ADDR.lower() not in rendered, (
        f"PRIVACY LEAK: property address '{ADDR}' appears in tenant-side email output"
    )


# ── Test 2: Property-side copy must not contain the tenant company name ────────

def test_property_side_excludes_tenant_name():
    """
    Rule 2 — Property-side email (written TO the owner/broker) must never
    reveal the matched tenant company's name anywhere in subject or body.
    """
    result, prompts = _call_generate(direction="property_side")

    # Check that the tenant name was NOT sent to the LLM.
    llm_input = (prompts.get("system", "") + " " + prompts.get("user", "")).lower()
    assert TENANT_NAME.lower() not in llm_input, (
        f"PRIVACY LEAK: tenant name '{TENANT_NAME}' was sent to the LLM "
        f"in the property-side outreach prompt — remove it from the builder"
    )

    # Check that the tenant name does not appear in the rendered output.
    rendered = (
        (result.get("email_subject") or "") + " " +
        (result.get("email_body") or "")
    ).lower()
    assert TENANT_NAME.lower() not in rendered, (
        f"PRIVACY LEAK: tenant name '{TENANT_NAME}' appears in property-side email output"
    )
