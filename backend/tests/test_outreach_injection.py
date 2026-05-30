"""
Hardcoded sentence injection + prompt quality contract tests.

Guards:
(a) tenant-match property-side email CONTAINS the intro sentence AND the
    social-proof sentence after the post-LLM injection pipeline.
(b) all three outreach types (tenant_match/property_side,
    for_sale_vacancy/property_side, tenant_match/tenant_side) contain the
    social-proof sentence — parametrized so no type can silently lose it.
(c) tenant-side output contains the lease-expiry phrase exactly once even
    when the LLM echoes it twice.
(d) no email output contains a literal [bracket] placeholder after sanitization.
(e) null/Unknown owner_name renders "Dear Property Owner," — not a placeholder.
"""
import re as _re
from unittest.mock import patch

import pytest


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _structured_llm_response(greeting="Dear Property Owner,", body_extra=""):
    """A syntactically valid LLM response that _parse_response can parse."""
    return (
        "SUBJECT: Test Subject\n"
        "EMAIL:\n"
        f"{greeting}\n\n"
        f"This is the main email body paragraph. {body_extra}\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        "Thank you,\n\n"
        "Jack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228\n"
        "OPENING:\nHello, this is the opening.\n"
        "CORE:\nThis is the core message.\n"
        "PAIN_PROBE:\nWhat are your current lease decision drivers?\n"
        "CLOSE:\nI'd welcome a brief call at your convenience."
    )


def _minimal_property(owner_name="Test Owner LLC", submarket="Tysons"):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": 5000,
        "owner_name": owner_name,
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
        "name": "Test Co",
        "industry": "Technology",
        "current_submarket": "Tysons",
        "estimated_sf_needed": 4000,
        "lease_expiry_months": lease_expiry_months,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


# ── (a) tenant-match property-side contains intro but NOT social-proof ────────


def test_tenant_match_property_side_intro_present_social_proof_absent():
    import app.services.property_outreach_service as svc

    with patch.object(svc, "_chat", side_effect=lambda s, u, **kw: _structured_llm_response()):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    body = result["email_body"]
    assert svc._HARDCODED_INTRO in body, (
        "tenant-match property-side must contain the hardcoded intro sentence"
    )
    assert svc._HARDCODED_SOCIAL_PROOF not in body, (
        "tenant-match property-side must NOT contain the social-proof sentence (property-side only)"
    )


# ── (b) social-proof absent on property-side; present on tenant-side ──────────


@pytest.mark.parametrize("outreach_type", [
    "tenant_match",
    "for_sale_vacancy",
])
def test_social_proof_absent_on_property_side(outreach_type):
    import app.services.property_outreach_service as svc

    prop = _minimal_property()
    if outreach_type == "for_sale_vacancy":
        prop["listed_for_sale"] = True

    with patch.object(svc, "_chat", side_effect=lambda s, u, **kw: _structured_llm_response()):
        result = svc.generate_property_outreach(
            property_dict=prop,
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    assert svc._HARDCODED_SOCIAL_PROOF not in result["email_body"], (
        f"{outreach_type}/property_side must NOT contain the social-proof sentence"
    )


def test_social_proof_present_on_tenant_side():
    import app.services.property_outreach_service as svc

    with patch.object(svc, "_chat", side_effect=lambda s, u, **kw: _structured_llm_response()):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_minimal_tenant(),
        )

    assert svc._HARDCODED_SOCIAL_PROOF in result["email_body"], (
        "tenant_match/tenant_side must contain the social-proof sentence"
    )


# ── (c) lease-expiry phrase appears exactly once on tenant-side ───────────────


def test_tenant_side_lease_expiry_appears_exactly_once():
    import app.services.property_outreach_service as svc

    expiry = 1
    phrase = f"approximately {expiry} month"  # singular

    def _dup_lease_chat(system, user, **kw):
        """Returns an email body where the lease-expiry phrase appears TWICE."""
        return (
            "SUBJECT: Test\n"
            "EMAIL:\n"
            "Hi Test Co Team,\n\n"
            f"With your lease expiring in {phrase}, I wanted to reach out about an "
            "opportunity in Tysons that may be a strong fit for your team.\n\n"
            f"Given your lease expiring in {phrase}, we have the perfect property for you.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\n"
            "Jack Zamer\n571-205-6228\n"
            "OPENING:\nHello.\n"
            "CORE:\nCore.\n"
            "PAIN_PROBE:\nPain?\n"
            "CLOSE:\nClose."
        )

    with patch.object(svc, "_chat", side_effect=_dup_lease_chat):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_minimal_tenant(lease_expiry_months=expiry),
        )

    body = result["email_body"]
    count = body.lower().count(phrase.lower())
    assert count == 1, (
        f"Lease-expiry phrase '{phrase}' must appear exactly once; found {count} times in:\n{body}"
    )


# ── (d) no bracket placeholder survives in output ─────────────────────────────


@pytest.mark.parametrize("outreach_type,direction", [
    ("tenant_match",     "property_side"),
    ("for_sale_vacancy", "property_side"),
    ("tenant_match",     "tenant_side"),
])
def test_no_bracket_placeholder_in_output(outreach_type, direction):
    import app.services.property_outreach_service as svc

    def _bracket_chat(system, user, **kw):
        """LLM returns email with bracket template tokens."""
        return (
            "SUBJECT: Test\n"
            "EMAIL:\n"
            "Dear [Owner's Name],\n\n"
            "I wanted to discuss [property details] and [asset type].\n\n"
            "Thank you,\n\nJack Zamer\n"
            "OPENING:\nHello.\n"
            "CORE:\nCore.\n"
            "PAIN_PROBE:\nPain?\n"
            "CLOSE:\nClose."
        )

    with patch.object(svc, "_chat", side_effect=_bracket_chat):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type=outreach_type,
            direction=direction,
            tenant_dict=_minimal_tenant(),
        )

    body = result["email_body"]
    leftovers = _re.findall(r'\[[A-Za-z][^\]\n]*\]', body)
    assert not leftovers, (
        f"{outreach_type}/{direction} must not contain bracket placeholders; "
        f"found: {leftovers}"
    )


# ── (e) null / Unknown owner_name → "Dear Property Owner," ───────────────────


# ── (f) call-ask contract: single "I'd welcome" ask; no "I propose a short call" ─


def test_tenant_match_property_side_call_ask_contract():
    import app.services.property_outreach_service as svc

    with patch.object(svc, "_chat", side_effect=lambda s, u, **kw: _structured_llm_response()):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(),
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    body = result["email_body"]
    assert "I'd welcome a brief call" in body, (
        "tenant-match property-side must contain 'I'd welcome a brief call at your convenience.'"
    )
    assert "I propose a short call" not in body, (
        "tenant-match property-side must NOT contain the redundant 'I propose a short call' sentence"
    )


# ── (e) null / Unknown owner_name → "Dear Property Owner," ───────────────────


@pytest.mark.parametrize("owner_name", [None, "", "Unknown", "unknown", "N/A"])
def test_null_or_unknown_owner_renders_fallback_salutation(owner_name):
    import app.services.property_outreach_service as svc

    def _bracket_salutation_chat(system, user, **kw):
        """Simulate LLM echoing a bracket salutation when owner is unknown."""
        return (
            "SUBJECT: Test\n"
            "EMAIL:\n"
            "Dear [Owner's Name],\n\n"
            "Some content here.\n\n"
            "Thank you,\n\nJack Zamer\n"
            "OPENING:\nHello.\n"
            "CORE:\nCore.\n"
            "PAIN_PROBE:\nPain?\n"
            "CLOSE:\nClose."
        )

    prop = _minimal_property(owner_name=owner_name)
    with patch.object(svc, "_chat", side_effect=_bracket_salutation_chat):
        result = svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )

    body = result["email_body"]
    assert "Dear [" not in body, (
        f"Bracket salutation must not survive for owner_name={owner_name!r}"
    )
    assert "Dear Property Owner," in body, (
        f"Expected 'Dear Property Owner,' fallback for owner_name={owner_name!r}"
    )
