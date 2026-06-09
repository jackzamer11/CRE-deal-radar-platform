"""
Contract tests for the four outreach humanization fixes.

(1) Fix 2 — owner-side (property-side) email carries NO industry/sector detail;
    the matched tenant is referred to only as "a qualified tenant".
(2) Fix 1 — the available SF injected into tenant-side copy is rounded
    (a multiple of 50/100); the raw figure is never shown.
(3) Fix 4 — both deterministic call scripts (OWNER and TENANT) carry the
    verbatim 48-hour close string, with the information-extraction questions in
    the required order.

All inputs are fabricated in-memory; _chat is mocked (no live LLM / DB / network).
"""
import re as _re
from unittest.mock import patch

import pytest

import app.services.property_outreach_service as svc


# ── Fixtures ──────────────────────────────────────────────────────────────────

INDUSTRY_KEYWORDS = [
    "industry", "sector",
    "technology", "tech", "software", "saas",
    "health care", "healthcare", "medical", "clinical", "pharma", "biotech",
    "federal", "government", "defense", "contractor",
    "legal", "law", "accounting", "consulting", "advisory",
    "naics",
]


def _property(submarket="Tysons", sf_avail=5000, vacancy_pct=12.0, listed_for_sale=False):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": sf_avail,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "market_rent_psf": 38.0,
        "listed_for_sale": listed_for_sale,
        "listed_for_lease": True,
        "vacancy_pct": vacancy_pct,
        "days_on_market": 95,
        "asset_class": "office",
        "dominant_score_type": "tenant_match",
    }


def _tenant(industry="Health Care", sf_needed=3500, lease_expiry_months=12):
    return {
        "company_id": "CO-001",
        "name": "ZZZ Distinctive Tenant Co",
        "industry": industry,
        "current_submarket": "Tysons",
        "current_sf_occupied": sf_needed,
        "lease_expiry_months": lease_expiry_months,
        "primary_contact_name": "Jane Smith",
        "opportunity_score": 80,
        "priority": "HIGH",
    }


# ── (1) Owner-side email contains no industry/sector keywords ──────────────────


@pytest.mark.parametrize("industry", ["Health Care", "Technology", "Federal Contractor", "Legal Services"])
def test_owner_side_email_has_no_industry(industry):
    """Fix 2 — the owner does not need sector detail. Neither the prompt sent to
    the model nor the rendered email may carry the tenant's industry/sector; the
    tenant must be referred to as 'a qualified tenant'."""
    prop = _property()
    tenant = _tenant(industry=industry)

    # (a) The builder must never feed the industry to the model, and must instruct
    #     the "a qualified tenant" framing.
    prompt = svc._build_tenant_match(prop, None, "owner", tenant_dict=tenant)
    combined = (prompt["system"] + " " + prompt["user"]).lower()
    assert industry.lower() not in combined, (
        f"owner-side prompt leaked the tenant industry '{industry}'"
    )
    assert "a qualified tenant" in combined, (
        "owner-side prompt must refer to the matched tenant as 'a qualified tenant'"
    )

    # (b) End-to-end (mocked LLM): the rendered email body carries no industry/sector
    #     keyword. The mock echoes the salutation/address but no sector label.
    def _mock_chat(system, user, **kw):
        m = _re.search(r"Open the email with exactly '([^']+)'", system)
        salutation = m.group(1) if m else "Dear Property Owner,"
        return (
            "SUBJECT: A qualified tenant for your space\n"
            "EMAIL:\n"
            f"{salutation}\n\n"
            "I'm working with a qualified tenant looking for ~3,500 SF in Tysons who is interested in "
            "your property at 100 Test Ave, McLean, VA, which still has open space.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228\n"
            "OPENING:\nHello.\nCORE:\nCore.\nPAIN_PROBE:\nWhat is driving your timeline?\n"
            "CLOSE:\nI'd welcome a brief call at your convenience."
        )

    with patch.object(svc, "_chat", side_effect=_mock_chat):
        result = svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=tenant,
        )
    body = result["email_body"].lower()
    leaked = [kw for kw in INDUSTRY_KEYWORDS if kw in body]
    assert not leaked, f"owner-side email leaked industry/sector keyword(s): {leaked}"
    assert "a qualified tenant" in body


# ── (2) Tenant-side email SF value is a multiple of 50 ─────────────────────────


@pytest.mark.parametrize("raw_sf,expected", [
    (5123, 5100),
    (5187, 5200),
    (3340, 3300),
    (4975, 5000),
])
def test_tenant_side_sf_is_rounded(raw_sf, expected):
    """Fix 1 — available SF injected into tenant-side copy is rounded to the
    nearest 100 (always a multiple of 50); the raw figure is never shown."""
    prop = _property(sf_avail=raw_sf)
    tenant = _tenant()

    prompt = svc._build_tenant_side(prop, tenant)
    combined = prompt["system"] + " " + prompt["user"]

    assert f"{expected:,}" in combined, (
        f"tenant-side prompt should inject rounded SF {expected:,} for raw {raw_sf}"
    )
    assert f"{raw_sf:,}" not in combined, (
        f"tenant-side prompt must never show the raw available SF {raw_sf:,}"
    )
    assert expected % 50 == 0, "rounded SF must be a multiple of 50"


def test_tenant_side_sf_rounded_in_rendered_email():
    """End-to-end: every available-SF figure in the rendered tenant-side email
    (mock echoes the SF the builder injected) is a multiple of 50."""
    prop = _property(sf_avail=5123)
    tenant = _tenant()

    def _mock_chat(system, user, **kw):
        # Echo the rounded SF the builder placed in the prompt.
        m = _re.search(r"([\d,]+) square feet available", system)
        sf_text = m.group(1) if m else "5,100"
        return (
            "SUBJECT: Tysons opportunity\n"
            "EMAIL:\n"
            "Hi Jane Smith,\n\n"
            f"With your lease coming up, I wanted to flag an office property in Tysons with {sf_text} "
            "square feet available that fits what you're after.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\nJack Zamer\n571-205-6228\n"
            "OPENING:\nHello.\nCORE:\nCore.\nPAIN_PROBE:\nWhat is driving the move?\nCLOSE:\nClose."
        )

    with patch.object(svc, "_chat", side_effect=_mock_chat):
        result = svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=tenant,
        )
    body = result["email_body"]
    assert "5,123" not in body, "raw available SF must never appear in tenant-side email"
    for figure in _re.findall(r"([\d,]+)\s+square feet", body):
        val = int(figure.replace(",", ""))
        assert val % 50 == 0, f"available SF {val} in tenant-side email is not a multiple of 50"


# ── (3) Both call scripts contain the verbatim 48-hour close ───────────────────


@pytest.mark.parametrize("target", ["OWNER", "TENANT"])
def test_call_script_has_48_hour_close(target):
    """Fix 4 — both OWNER and TENANT scripts close with the verbatim 48-hour
    commitment, and carry their three information-extraction questions in order."""
    cs = svc.build_call_script(target, _property(), _tenant())

    assert "within 48 hours" in cs["close"], f"{target} close must contain the 48-hour string"

    expected_questions = (
        svc.OWNER_CALL_QUESTIONS if target == "OWNER" else svc.TENANT_CALL_QUESTIONS
    )
    # Questions present and in the required order inside the packed pain_probe field.
    positions = [cs["pain_probe"].find(q) for q in expected_questions]
    assert all(pos >= 0 for pos in positions), f"{target} script missing an extraction question"
    assert positions == sorted(positions), f"{target} extraction questions are out of order"


def test_generate_attaches_correct_call_target():
    """Direction selects the call target: property_side -> OWNER, tenant_side -> TENANT,
    and the rendered close matches the spec for each."""
    def _mock_chat(system, user, **kw):
        return (
            "SUBJECT: Test\nEMAIL:\nDear Property Owner,\n\nBody.\n\n"
            "I'd welcome a brief call at your convenience.\n\nThank you,\n\nJack Zamer\n"
            "OPENING:\nx\nCORE:\nx\nPAIN_PROBE:\nx\nCLOSE:\nx"
        )

    with patch.object(svc, "_chat", side_effect=_mock_chat):
        owner = svc.generate_property_outreach(
            property_dict=_property(), outreach_type="tenant_match",
            direction="property_side", tenant_dict=_tenant(),
        )
        tenant = svc.generate_property_outreach(
            property_dict=_property(), outreach_type="tenant_match",
            direction="tenant_side", tenant_dict=_tenant(),
        )

    assert owner["call_target"] == "OWNER"
    assert tenant["call_target"] == "TENANT"
    assert owner["call_script_close"] == svc.OWNER_CALL_CLOSE
    assert tenant["call_script_close"] == svc.TENANT_CALL_CLOSE
    assert "within 48 hours" in owner["call_script_close"]
    assert "within 48 hours" in tenant["call_script_close"]
