"""
Contract tests for four outreach template fixes.

(a) No duplicate salutation in property-side tenant-match email.
(b) Month pluralisation — "1 month" not "1 months" — across all builders.
(c) FSV email does NOT contain the duplicate _PHASE1_FSV_CLOSING pitch.
(d) Centreville CoStar submarket mapping resolves to "Centreville".
"""
from unittest.mock import patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_chat(system, user, *, max_tokens=600):
    """Minimal mock _chat that returns a structurally valid outreach response."""
    return (
        "Subject: Test Subject\n\n"
        "Dear Property Owner,\n\n"
        "This is a test email body.\n\n"
        "I work exclusively in the Northern Virginia office market, focused on "
        "matching tenants to vacancies before they hit the open listings.\n\n"
        "Thank you,\n"
        "Test Agent\n\n"
        "Opening: Hello.\n"
        "Core message: Core.\n"
        "Pain probe: Pain?\n"
        "Close: I'd welcome a brief call at your convenience."
    )


def _minimal_property(submarket="Tysons"):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": 5000,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "market_rent_psf": 38.0,
        "market_cap_rate": 6.5,
        "listed_for_sale": False,
        "listed_for_lease": True,
        "vacancy_pct": 10.0,
        "dominant_score_type": "tenant_match",
    }


def _minimal_tenant(lease_expiry_months=12):
    return {
        "company_id": "CO-001",
        "name": "ZZZ Test Co",
        "industry": "Technology",
        "current_submarket": "Tysons",
        "current_sf_occupied": 4000,
        "lease_expiry_months": lease_expiry_months,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


# ── (a) No duplicate salutation ───────────────────────────────────────────────

def test_no_duplicate_salutation_in_tenant_match(monkeypatch):
    import app.services.property_outreach_service as svc

    captured = {}

    def capturing_chat(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return _fake_chat(system, user, **kw)

    with patch.object(svc, "_chat", side_effect=capturing_chat):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    owner_name = "Test Owner LLC"
    system_prompt = captured.get("system", "")
    user_prompt   = captured.get("user", "")

    # The salutation should appear exactly once in the combined prompt.
    combined = system_prompt + user_prompt
    assert f"Dear {owner_name}," in combined, "Salutation must appear in prompt"
    assert combined.count(f"Dear {owner_name},") == 1, (
        f"Salutation 'Dear {owner_name},' must appear ONCE, not twice, in the prompt"
    )


def test_null_owner_uses_fallback_salutation(monkeypatch):
    import app.services.property_outreach_service as svc

    captured = {}

    def capturing_chat(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return _fake_chat(system, user, **kw)

    prop = _minimal_property()
    prop["owner_name"] = None

    with patch.object(svc, "_chat", side_effect=capturing_chat):
        svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    combined = captured.get("system", "") + captured.get("user", "")
    assert "Dear Property Owner," in combined, "Null owner must fall back to 'Dear Property Owner,'"


# ── (b) Month pluralisation ───────────────────────────────────────────────────

@pytest.mark.parametrize("months,expected_fragment", [
    (1,  "1 month"),
    (2,  "2 months"),
    (12, "12 months"),
])
def test_month_pluralisation_tenant_match_property_side(months, expected_fragment):
    import app.services.property_outreach_service as svc

    captured = {}

    def capturing_chat(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return _fake_chat(system, user, **kw)

    with patch.object(svc, "_chat", side_effect=capturing_chat):
        svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(lease_expiry_months=months),
        )

    combined = captured.get("system", "") + captured.get("user", "")
    assert expected_fragment in combined, (
        f"Expected '{expected_fragment}' in prompt for lease_expiry_months={months}"
    )
    if months == 1:
        assert "1 months" not in combined, "Singular '1 months' must never appear"


@pytest.mark.parametrize("months,expected_fragment", [
    (1,  "1 month"),
    (2,  "2 months"),
])
def test_month_pluralisation_tenant_side(months, expected_fragment):
    import app.services.property_outreach_service as svc

    captured = {}

    def capturing_chat(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return _fake_chat(system, user, **kw)

    with patch.object(svc, "_chat", side_effect=capturing_chat):
        svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_minimal_tenant(lease_expiry_months=months),
        )

    combined = captured.get("system", "") + captured.get("user", "")
    assert expected_fragment in combined, (
        f"Expected '{expected_fragment}' in prompt for lease_expiry_months={months}"
    )
    if months == 1:
        assert "1 months" not in combined, "Singular '1 months' must never appear"


# ── (c) No duplicate FSV closing pitch ────────────────────────────────────────

def test_fsv_email_no_duplicate_closing_pitch():
    import app.services.property_outreach_service as svc

    _PHASE1_FSV_CLOSING = svc._PHASE1_FSV_CLOSING

    with patch.object(svc, "_chat", side_effect=_fake_chat):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="for_sale_vacancy",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    email_body = result.get("email_body", "")
    assert _PHASE1_FSV_CLOSING not in email_body, (
        "FSV closing pitch must NOT be injected — it duplicates _HARDCODED_SOCIAL_PROOF"
    )


# ── (d) Centreville submarket mapping ─────────────────────────────────────────

def test_centreville_costar_mapping():
    from app.api.routes.properties import COSTAR_SUBMARKET_MAP

    assert COSTAR_SUBMARKET_MAP.get("centreville") == "Centreville", (
        "CoStar 'centreville' must map to platform submarket 'Centreville'"
    )
