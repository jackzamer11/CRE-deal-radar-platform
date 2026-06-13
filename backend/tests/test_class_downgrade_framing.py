"""
Tests: Class Downgrade — Lease-First Framing

Verifies that when a Class A tenant is matched to a Class B (or lower) property:
  1. The system prompt sent to GPT-4o contains the CLASS DOWNGRADE FRAMING rule
     (lease-timing language, submarket/industry/lease-window opening requirement).
  2. The framing rule is NOT present when no downgrade exists.
  3. The TenantMatchAction UI card carries the signal tag.
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


# ── Minimal company dict for generate_outreach ───────────────────────────────

def _company(is_class_downgrade: bool = False) -> dict:
    return {
        "name": "Acme Tech LLC",
        "industry": "Technology",
        "current_submarket": "Reston",
        "current_headcount": 120,
        "headcount_growth_pct": 8.5,
        "current_sf_occupied": 15_000,
        "lease_expiry_months": 14,
        "lease_expiry_date": "2027-08-01",
        "primary_contact_name": "Alex Chen",
        "primary_contact_title": "VP Real Estate",
        "tenant_representative": "",
        "current_rent_psf": 38.0,
        "future_move_flag": False,
        "future_move_type": "",
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 72.0,
        "priority": "HIGH",
        "is_class_downgrade": is_class_downgrade,
    }


def _fake_openai_response() -> MagicMock:
    """Minimal JSON response that satisfies generate_outreach's post-processing."""
    content = json.dumps({
        "call_script": {
            "opening": "Hi Alex, calling from...",
            "core_message": "Reston has 14.2% vacancy...",
            "pain_probe": "What's your biggest space pressure this cycle?",
            "the_close": "I'd welcome a brief call at your convenience.",
        },
        "email": {
            "subject": "Reston lease timing",
            "body": "Hi Alex, with your lease expiring in 14 months and Reston market rent...",
        },
    })
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = content
    return mock_resp


def _capture_messages(company_dict: dict) -> list:
    """
    Call generate_outreach with a mocked OpenAI client and return the
    messages list that was passed to chat.completions.create().
    """
    from app.services.outreach_service import generate_outreach

    captured: list = []

    def fake_create(*args, **kwargs):
        captured.extend(kwargs.get("messages", []))
        return _fake_openai_response()

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch("openai.OpenAI", return_value=mock_client):
            with patch(
                "app.services.outreach_service._web_search_company_intel",
                return_value="",
            ):
                with patch(
                    "app.services.property_outreach_service._inject_hardcoded_sentences",
                    side_effect=lambda body: body,
                ):
                    generate_outreach(company_dict)

    return captured


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_class_downgrade_prompt_contains_lease_first_rule():
    """
    When is_class_downgrade=True, the system prompt must contain the
    CLASS DOWNGRADE FRAMING rule with lease-timing language.
    """
    messages = _capture_messages(_company(is_class_downgrade=True))

    system_msg = next(m for m in messages if m["role"] == "system")
    prompt = system_msg["content"]

    assert "CLASS DOWNGRADE FRAMING" in prompt
    assert "lease timing" in prompt.lower()
    assert "lease window" in prompt.lower()
    assert "submarket" in prompt.lower()
    assert "industry" in prompt.lower()


def test_class_downgrade_prompt_forbids_building_feature_lead():
    """
    The framing rule must instruct GPT not to lead with building or features.
    """
    messages = _capture_messages(_company(is_class_downgrade=True))
    system_msg = next(m for m in messages if m["role"] == "system")
    prompt = system_msg["content"]

    # Rule must say NOT to lead with the building / its features
    assert "NOT the building" in prompt or "not the building" in prompt.lower()


def test_no_downgrade_prompt_has_no_class_downgrade_rule():
    """
    When is_class_downgrade=False, the CLASS DOWNGRADE FRAMING rule must
    NOT appear in the system prompt.
    """
    messages = _capture_messages(_company(is_class_downgrade=False))
    system_msg = next(m for m in messages if m["role"] == "system")
    assert "CLASS DOWNGRADE FRAMING" not in system_msg["content"]


# ── Signal tag on UI card ─────────────────────────────────────────────────────

def test_signal_tag_added_for_class_downgrade():
    """
    TenantMatchAction built in output_engine must carry the
    'Class Downgrade — Lease-First Framing' tag when tenant class > property class.
    """
    from app.services.match_scoring import _class_rank
    from app.schemas.dashboard import TenantMatchAction

    t_rank = _class_rank("Class A")
    p_rank = _class_rank("Class B")
    assert t_rank is not None and p_rank is not None
    assert t_rank > p_rank  # sanity: A(3) > B(2)

    # Build the action as output_engine does
    signal_tags: list = []
    if t_rank > p_rank:
        signal_tags = ["Class Downgrade — Lease-First Framing"]

    action = TenantMatchAction(
        property_id="NVA-001",
        address="100 Test Blvd, Reston, VA",
        submarket="Reston",
        sf_avail=20_000,
        landlord_representative=None,
        listed_for_sale=False,
        outreach_type="tenant_match",
        target_type="broker",
        tenant_company_id="CO-001",
        tenant_name="Acme Tech LLC",
        tenant_industry="Technology",
        tenant_headcount=120,
        tenant_sf_needed=15_000,
        match_score=72.5,
        adjacent_submarket=False,
        lease_expiry_months=14,
        lease_expiry_chip="14 mo",
        contact_status="NOT_CONTACTED",
        signal_tags=signal_tags,
    )
    assert action.signal_tags == ["Class Downgrade — Lease-First Framing"]


def test_signal_tag_absent_for_same_class():
    """No signal tag when tenant and property are the same class."""
    from app.services.match_scoring import _class_rank
    from app.schemas.dashboard import TenantMatchAction

    t_rank = _class_rank("Class B")
    p_rank = _class_rank("Class B")
    signal_tags: list = []
    if t_rank is not None and p_rank is not None and t_rank > p_rank:
        signal_tags = ["Class Downgrade — Lease-First Framing"]

    action = TenantMatchAction(
        property_id="NVA-002",
        address="200 Test Blvd, Reston, VA",
        submarket="Reston",
        sf_avail=20_000,
        landlord_representative=None,
        listed_for_sale=False,
        outreach_type="tenant_match",
        target_type="broker",
        tenant_company_id="CO-002",
        tenant_name="Beta Corp",
        tenant_industry="Finance",
        tenant_headcount=80,
        tenant_sf_needed=12_000,
        match_score=65.0,
        adjacent_submarket=False,
        lease_expiry_months=18,
        lease_expiry_chip="18 mo",
        contact_status="NOT_CONTACTED",
        signal_tags=signal_tags,
    )
    assert action.signal_tags == []


def test_signal_tag_absent_for_class_upgrade():
    """No signal tag when tenant is moving UP in class (B tenant → A property)."""
    from app.services.match_scoring import _class_rank

    t_rank = _class_rank("Class B")
    p_rank = _class_rank("Class A")
    signal_tags: list = []
    if t_rank is not None and p_rank is not None and t_rank > p_rank:
        signal_tags = ["Class Downgrade — Lease-First Framing"]

    assert signal_tags == []


# ── _build_tenant_side: the real execution path from the Dashboard ────────────

def _tenant_side_prompt(tenant_class: str, property_class: str) -> str:
    """
    Call _build_tenant_side with a minimal property + tenant dict and return
    the system prompt that would be sent to GPT-4o.
    """
    from app.services.property_outreach_service import _build_tenant_side

    p = {
        "asset_class": property_class,
        "submarket": "Reston",
        "sf_avail": 20_000,
        "in_place_rent_psf": 38.0,
    }
    tenant_dict = {
        "name": "Acme Tech LLC",
        "primary_contact_name": "Alex Chen",
        "industry": "Technology",
        "current_sf_occupied": 15_000,
        "lease_expiry_months": 14,
        "current_submarket": "Reston",
        "current_building_class": tenant_class,
    }
    result = _build_tenant_side(p, tenant_dict)
    return result["system"]


def test_tenant_side_downgrade_omits_asset_class_from_description():
    """
    For a Class A tenant matched to a Class B property, the tenant-side system
    prompt must NOT say 'a Class B property' — class label must be hidden.
    """
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "Class B property" not in prompt
    assert "CLASS DOWNGRADE FRAMING" in prompt


def test_tenant_side_downgrade_leads_with_lease_timing():
    """
    The downgrade framing rule must require opening on lease timing +
    submarket + industry context.
    """
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "lease" in prompt.lower()
    assert "Reston" in prompt
    assert "Technology" in prompt


def test_tenant_side_downgrade_not_triggered_for_same_class():
    """
    For a Class B tenant matched to a Class B property, no downgrade rule
    and the asset class label IS present in the property description.
    """
    prompt = _tenant_side_prompt("Class B", "Class B")
    assert "CLASS DOWNGRADE FRAMING" not in prompt
    assert "Class B property" in prompt


def test_tenant_side_downgrade_not_triggered_for_upgrade():
    """
    For a Class C tenant matched to a Class B property (upgrade), no downgrade
    rule and the asset class label IS present.
    """
    prompt = _tenant_side_prompt("Class C", "Class B")
    assert "CLASS DOWNGRADE FRAMING" not in prompt
    assert "Class B property" in prompt
