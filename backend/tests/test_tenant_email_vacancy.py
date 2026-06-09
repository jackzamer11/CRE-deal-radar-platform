"""
Fix 3 — tenant-side email reintroduces the CBRE submarket VACANCY rate as a single
urgency line, and only when a rate exists for the submarket. Never rent PSF.

Offline: _chat is mocked (no live LLM / DB / network).
"""
from unittest.mock import patch

import pytest

import app.services.property_outreach_service as svc


def _property(submarket="Tysons"):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "sf_avail": 5000,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "vacancy_pct": 12.0,
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


def _mock_chat(system, user, **kw):
    return (
        "SUBJECT: Tysons opportunity\n"
        "EMAIL:\n"
        "Hi Jane Smith,\n\n"
        "With your lease coming up, I wanted to flag an office property that fits.\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        "Thank you,\n\nJack Zamer\n571-205-6228\n"
        "OPENING:\no\nCORE:\nc\nPAIN_PROBE:\np\nCLOSE:\nx"
    )


def _render(prop):
    with patch.object(svc, "_chat", side_effect=_mock_chat):
        return svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_tenant(),
        )["email_body"]


def test_vacancy_helper_known_and_unknown():
    assert svc._vacancy_urgency_sentence("Tysons") == (
        "With Tysons's vacancy rate at 27.3%, quality options are moving quickly."
    )
    assert svc._vacancy_urgency_sentence("Nowheresville") is None
    assert svc._vacancy_urgency_sentence(None) is None


def test_known_submarket_injects_vacancy_line():
    body = _render(_property(submarket="Tysons"))
    assert "vacancy rate at 27.3%, quality options are moving quickly" in body
    # Exactly one urgency line (no duplication).
    assert body.count("quality options are moving quickly") == 1
    # Placed before the closing ask.
    assert body.index("quality options are moving quickly") < body.index("I'd welcome a brief call")


def test_unknown_submarket_omits_vacancy_line():
    body = _render(_property(submarket="Nowheresville"))
    assert "quality options are moving quickly" not in body


def test_no_rent_psf_in_tenant_email():
    body = _render(_property(submarket="Tysons"))
    # Fix 3: vacancy only — never a rent-per-SF figure.
    assert "/SF" not in body and "PSF" not in body.upper()
