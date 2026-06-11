"""
CBRE submarket-vacancy urgency logic in outreach emails.

Fix 2 — Tenant-side: cite the submarket vacancy rate ONLY when it is below 10%
        ("quality options are limited"); at/above 10%, omit and rely on lease
        timing for urgency.
Fix 3 — Owner-side: cite the submarket vacancy rate ONLY when it is above 15%
        ("qualified tenants are harder to come by — this is one worth moving on");
        at/below 15%, omit the vacancy line and substitute a lease-timeline line.

Never rent PSF. Offline: _chat is mocked (no live LLM / DB / network).

CBRE Q1 2026 reference vacancies used here:
  Vienna 5.2% (<10%, >  -)   McLean 7.4%   Tysons 27.3% (>15%)   Falls Church 10.4%
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
    m = __import__("re").search(r"Open the email with exactly '([^']+)'", system)
    salutation = m.group(1) if m else "Dear Property Owner,"
    return (
        "SUBJECT: Opportunity\n"
        "EMAIL:\n"
        f"{salutation}\n\n"
        "Quick note about a strong fit I'm tracking for you.\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        "Thank you,\n\nJack Zamer\n571-205-6228\n"
        "OPENING:\no\nCORE:\nc\nPAIN_PROBE:\np\nCLOSE:\nx"
    )


def _render(prop, direction):
    with patch.object(svc, "_chat", side_effect=_mock_chat):
        return svc.generate_property_outreach(
            property_dict=prop,
            outreach_type="tenant_match",
            direction=direction,
            tenant_dict=_tenant(),
        )["email_body"]


# ── thresholds + helper units ───────────────────────────────────────────────────
def test_threshold_constants():
    assert svc.TENANT_VACANCY_URGENCY_THRESHOLD == 0.10
    assert svc.OWNER_VACANCY_URGENCY_THRESHOLD == 0.15


def test_tenant_vacancy_sentence_helper():
    # Vienna 5.2% < 10% → cited.
    assert svc._tenant_vacancy_sentence("Vienna") == (
        "With Vienna's vacancy rate at 5.2%, quality options are limited."
    )
    # Tysons 27.3% >= 10% → omitted.
    assert svc._tenant_vacancy_sentence("Tysons") is None
    assert svc._tenant_vacancy_sentence("Nowheresville") is None


def test_owner_vacancy_sentence_helper():
    # Tysons 27.3% > 15% → cited.
    assert svc._owner_vacancy_sentence("Tysons") == (
        "With Tysons's vacancy rate at 27.3%, qualified tenants are harder to "
        "come by — this is one worth moving on."
    )
    # Vienna 5.2% <= 15% → omitted (caller substitutes lease-timeline line).
    assert svc._owner_vacancy_sentence("Vienna") is None
    assert svc._owner_vacancy_sentence("Nowheresville") is None


# ── tenant-side rendered behaviour (Fix 2) ──────────────────────────────────────
def test_tenant_low_vacancy_cites_line():
    body = _render(_property(submarket="Vienna"), "tenant_side")
    assert "vacancy rate at 5.2%, quality options are limited" in body
    assert body.index("quality options are limited") < body.index("I'd welcome a brief call")


def test_tenant_high_vacancy_omits_line():
    body = _render(_property(submarket="Tysons"), "tenant_side")  # 27.3% ≥ 10%
    assert "vacancy rate at" not in body
    assert "quality options are limited" not in body


# ── owner-side rendered behaviour (Fix 3) ───────────────────────────────────────
def test_owner_high_vacancy_cites_line():
    body = _render(_property(submarket="Tysons"), "property_side")  # 27.3% > 15%
    assert "qualified tenants are harder to come by — this is one worth moving on" in body
    assert "vacancy rate at 27.3%" in body


def test_owner_low_vacancy_uses_lease_timeline():
    body = _render(_property(submarket="Vienna"), "property_side")  # 5.2% ≤ 15%
    assert "vacancy rate at" not in body, "owner email must omit vacancy at/below 15%"
    assert "lease expiring in approximately 12 months" in body
    assert "worth moving on" in body


# ── no rent PSF anywhere ────────────────────────────────────────────────────────
@pytest.mark.parametrize("submarket,direction", [
    ("Vienna", "tenant_side"),
    ("Tysons", "tenant_side"),
    ("Tysons", "property_side"),
    ("Vienna", "property_side"),
])
def test_no_rent_psf(submarket, direction):
    body = _render(_property(submarket=submarket), direction)
    assert "/SF" not in body and "PSF" not in body.upper()
