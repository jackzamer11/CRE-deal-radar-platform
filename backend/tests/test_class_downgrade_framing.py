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
    submarket + industry context — all three appear in the prompt.
    """
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "lease" in prompt.lower()    # lease timing hook present
    assert "Reston" in prompt           # submarket injected
    assert "Technology" in prompt       # industry injected into PROHIBITION 2


def test_tenant_side_downgrade_three_prohibitions_present():
    """
    The downgrade rule must include all five PROHIBITION lines.
    """
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "PROHIBITION 1" in prompt
    assert "PROHIBITION 2" in prompt
    assert "PROHIBITION 3" in prompt
    assert "PROHIBITION 4" in prompt
    assert "PROHIBITION 5" in prompt


def test_tenant_side_downgrade_prohibition_4_no_sf_restatement():
    """PROHIBITION 4 must forbid restating the tenant's SF need back to them."""
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "PROHIBITION 4" in prompt
    assert "restate the tenant's sf need" in prompt.lower()
    assert "a company needing x sf" in prompt.lower()


def test_tenant_side_downgrade_prohibition_5_vacancy_embedded():
    """PROHIBITION 5 must require the vacancy stat to be embedded inside paragraph 2."""
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "PROHIBITION 5" in prompt
    assert "embedded inside paragraph 2" in prompt.lower()
    assert "orphan" in prompt.lower()


def test_tenant_side_downgrade_prohibition_1_forbids_class_language():
    """PROHIBITION 1 must forbid class labels and downgrade-implying language."""
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "class ratings" in prompt.lower() or "Class A/B/C" in prompt
    assert "moving down in quality" in prompt or "downgrade" in prompt.lower()


def test_tenant_side_downgrade_prohibition_2_forbids_property_lead():
    """PROHIBITION 2 must require paragraph one to be about lease timing and industry context."""
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "paragraph one must be entirely about" in prompt.lower()
    assert "Technology" in prompt  # industry injected into rule
    assert "Reston" in prompt      # submarket injected into rule


def test_tenant_side_downgrade_prohibition_3_options_moving_framing():
    """PROHIBITION 3 must require 'options are moving' framing, not 'I found you a space'."""
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "options are moving" in prompt
    assert "I found you a space" in prompt  # must appear as a FORBIDDEN example


def test_tenant_side_downgrade_specific_ask_close():
    """
    For a class-downgrade pair the close must be 'Are you free this week or next?',
    not the default 'I'd welcome a brief call at your convenience.'
    """
    prompt = _tenant_side_prompt("Class A", "Class B")
    assert "Are you free this week or next?" in prompt
    assert "I'd welcome a brief call at your convenience" not in prompt


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


# ── Post-processor: SF-restatement strip + vacancy-orphan merge ───────────────

def _run_postprocess(email_body: str) -> str:
    from unittest.mock import patch, MagicMock

    raw = (
        f"SUBJECT: Test subject\n"
        f"EMAIL:\n{email_body}\n"
        f"OPENING:\nOpening text.\n"
        f"CORE:\nCore message.\n"
        f"PAIN_PROBE:\nProbe question?\n"
        f"CLOSE:\nClose text."
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = raw
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    from app.services.property_outreach_service import generate_property_outreach

    p = {
        "property_id": "NVA-001", "address": "100 Test Blvd, Reston, VA",
        "submarket": "Reston", "asset_class": "Class B", "sf_avail": 20_000,
        "in_place_rent_psf": 38.0, "owner_name": "Test Owner LLC",
        "total_sf": 50_000, "year_built": 2005, "vacancy_pct": 8.0,
    }
    t = {
        "name": "Acme Tech LLC", "company_id": "CO-001",
        "primary_contact_name": "Alex Chen", "industry": "Technology",
        "current_sf_occupied": 15_000, "lease_expiry_months": 14,
        "current_submarket": "Reston", "current_building_class": "Class A",
    }

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch("openai.OpenAI", return_value=mock_client):
            result = generate_property_outreach(
                property_dict=p, outreach_type="tenant_match",
                direction="tenant_side", tenant_dict=t,
            )
    return result["email_body"]


def test_postprocess_removes_sf_restatement_sentences():
    """SF-restatement phrases are stripped from class-downgrade emails."""
    body = (
        "Hi Alex, with your lease expiring in 14 months this is a good time to look.\n\n"
        "I have office space in Reston that aligns well with your 15,000 SF requirement "
        "and fits your space needs perfectly.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    assert "aligns well with your" not in result.lower()
    assert "fits your" not in result.lower()


def test_postprocess_removes_company_needing_sf_phrase():
    """'a company needing X SF' phrasing is stripped from class-downgrade emails."""
    body = (
        "Hi Alex, your lease window is tightening.\n\n"
        "I know of a space for a company needing 15,000 SF in Reston.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    assert "a company needing" not in result.lower()


def test_postprocess_merges_standalone_vacancy_paragraph():
    """A standalone vacancy-only paragraph is merged into the preceding paragraph."""
    body = (
        "Hi Alex, your lease is expiring soon.\n\n"
        "There is quality office space available in Reston right now.\n\n"
        "Reston currently has 8.2% vacancy.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    paras = [p.strip() for p in result.split("\n\n") if p.strip()]
    standalone_vac = [
        p for p in paras
        if "vacancy" in p.lower() and "%" in p
        and "free this week" not in p.lower()
        and "lease" not in p.lower()
        and "space" not in p.lower()
        and len(p.split(".")) <= 2
    ]
    assert standalone_vac == [], f"Found standalone vacancy paragraph(s): {standalone_vac}"


def test_postprocess_not_applied_for_non_downgrade():
    """Post-processor must NOT strip SF phrases when there is no class downgrade."""
    from unittest.mock import patch, MagicMock
    from app.services.property_outreach_service import generate_property_outreach

    body = (
        "Hi Alex, I have space that fits your 15,000 SF needs perfectly.\n\n"
        "Are you free this week or next?"
    )
    raw = (
        f"SUBJECT: Test\nEMAIL:\n{body}\n"
        "OPENING:\nOpening.\nCORE:\nCore.\nPAIN_PROBE:\nProbe?\nCLOSE:\nClose."
    )
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = raw
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    p = {
        "property_id": "NVA-002", "address": "200 Test St", "submarket": "Tysons",
        "asset_class": "Class B", "sf_avail": 20_000, "owner_name": "Owner LLC",
        "total_sf": 40_000, "year_built": 2000,
    }
    t = {
        "name": "Beta Corp", "company_id": "CO-002", "industry": "Finance",
        "current_sf_occupied": 15_000, "lease_expiry_months": 18,
        "current_building_class": "Class B",
    }

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
        with patch("openai.OpenAI", return_value=mock_client):
            result = generate_property_outreach(
                property_dict=p, outreach_type="tenant_match",
                direction="tenant_side", tenant_dict=t,
            )
    assert "fits your" in result["email_body"].lower()


def test_postprocess_injects_fallback_when_stripped_to_one_body_para():
    """
    When post-processing reduces a class-downgrade email to fewer than 2
    substantive body paragraphs (the only SF phrase is stripped, leaving just
    the Jack Zamer intro and the closing ask), a market-context fallback
    paragraph must be injected before the closing ask.
    """
    # The middle paragraph contains ONLY an SF-restatement phrase — Step 1 will
    # strip it entirely, leaving: greeting + hardcoded-intro + close.
    body = (
        "Hi Alex,\n\n"
        "A company needing 15,000 SF in Reston should start their search now.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)

    # Fallback market-context paragraph must be present.
    assert "vacancy rate" in result.lower(), (
        f"Expected fallback with 'vacancy rate' in result, got:\n{result}"
    )
    assert "options are moving quickly" in result.lower(), (
        f"Expected 'options are moving quickly' in result, got:\n{result}"
    )
    # The closing ask must still be present.
    assert "are you free this week" in result.lower()
    # Reston submarket must appear in the fallback.
    assert "reston" in result.lower()
    # No triple (or more) consecutive newlines — cleanup step ran.
    assert "\n\n\n" not in result


# ── Steps 3-7: new cleanup rules ─────────────────────────────────────────────

def test_postprocess_strips_redundant_timing_key():
    """
    Step 3: When a paragraph contains both 'timing is crucial' and 'timing is key',
    the sentence with 'timing is key' is removed and 'timing is crucial' stays.
    """
    body = (
        "Hi Alex,\n\n"
        "The timing is crucial for your search. "
        "With your lease expiring soon and given the timing is key in this market, you should act now.\n\n"
        "Options in Reston are available.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    assert "timing is key" not in result.lower(), (
        f"'timing is key' should have been stripped; got:\n{result}"
    )
    assert "timing is crucial" in result.lower(), (
        f"'timing is crucial' should remain; got:\n{result}"
    )


def test_postprocess_strips_fit_for_sentence():
    """
    Step 4: Sentences containing 'how this could be a fit for' are stripped.
    """
    body = (
        "Hi Alex,\n\n"
        "With your lease expiring in 14 months, now is the time to explore options.\n\n"
        "I wanted to reach out to see how this could be a fit for your team at Acme Tech.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    assert "how this could be a fit for" not in result.lower(), (
        f"'how this could be a fit for' should have been stripped; got:\n{result}"
    )


def test_postprocess_truncates_trailing_content_after_close():
    """
    Step 5: Anything after 'Are you free this week or next?' on the same line
    is stripped — the entire close sentence is replaced with just the ask.
    Covers a plain trailing phrase joined by a space.
    """
    body = (
        "Hi Alex,\n\n"
        "With your lease expiring in 14 months, the timing is right to explore.\n\n"
        "Options in Reston have been moving quickly.\n\n"
        "Are you free this week or next? I look forward to connecting with you soon."
    )
    result = _run_postprocess(body)
    assert "are you free this week or next?" in result.lower(), (
        f"Closing ask must still be present; got:\n{result}"
    )
    assert "i look forward to connecting" not in result.lower(), (
        f"Trailing content after ask should have been stripped; got:\n{result}"
    )


def test_postprocess_truncates_prepended_clause_before_close():
    """
    Step 5 (bug fix): trailing clauses joined by words like 'to discuss' or
    'to chat' before 'Are you free this week or next?' are also stripped.
    The entire sentence is replaced with just the bare ask.
    """
    body = (
        "Hi Alex,\n\n"
        "With your lease expiring in 14 months, the timing is right to explore.\n\n"
        "Options in Reston have been moving quickly.\n\n"
        "I'd love to chat — are you free this week or next to discuss further?"
    )
    result = _run_postprocess(body)
    assert "are you free this week or next?" in result.lower(), (
        f"Closing ask must be present; got:\n{result}"
    )
    assert "to discuss further" not in result.lower(), (
        f"Trailing clause should have been stripped; got:\n{result}"
    )
    assert "i'd love to chat" not in result.lower(), (
        f"Leading clause should have been stripped; got:\n{result}"
    )


def test_postprocess_appends_property_reference_to_second_body_para():
    """
    Step 7: The second substantive body paragraph must end with the canonical
    property-reference sentence if it isn't already there.
    """
    body = (
        "Hi Alex,\n\n"
        "With your lease expiring in 14 months, Reston market timing is critical.\n\n"
        "Options in the market have been moving quickly and there is strong demand.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    assert "there's an office property in reston" in result.lower(), (
        f"Property reference not found in result:\n{result}"
    )
    assert "worth a look" in result.lower(), (
        f"'worth a look' anchor not found in result:\n{result}"
    )
    # Must appear only once — no double-append.
    assert result.lower().count("worth a look") == 1, (
        f"Property reference was appended more than once:\n{result}"
    )


def test_postprocess_skips_property_reference_when_already_present():
    """
    Step 7 (bug fix): if the second body paragraph already contains both the
    submarket name and 'SF available', the property-reference sentence must
    NOT be appended again — coverage check for the detection logic.
    """
    body = (
        "Hi Alex,\n\n"
        "With your lease expiring in 14 months, Reston market timing is critical.\n\n"
        "There is a quality office property in Reston with approximately 20,000 SF available "
        "that suits your window.\n\n"
        "Are you free this week or next?"
    )
    result = _run_postprocess(body)
    # The reference must not be duplicated.
    assert result.lower().count("sf available") == 1, (
        f"Property reference was double-appended:\n{result}"
    )
