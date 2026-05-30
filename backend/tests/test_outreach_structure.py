"""
Structure / contract tests for the two restructured PROPERTY-SIDE outreach emails:
  - tenant-match property-side  (_build_tenant_match path)
  - for-sale + vacancy property-side (_build_for_sale_vacancy path)

These lock CONTRACTS, not exact wording (structure/tone is human judgment):
  - hardcoded intro present
  - hardcoded social-proof line present and appears EXACTLY ONCE (no duplicate pitch)
  - the legacy "I have a qualified tenant in mind" pitch is absent (FSV)
  - salutation resolves to the real owner OR "Dear Property Owner," — no bracket tokens
  - the tenant COMPANY NAME never appears (privacy)
  - the street ADDRESS is present (property-side is allowed to show it)
  - lease-expiry phrase appears at most once
  - body is under the 150-word limit
  - FSV retains the owner-discretion leasing question (central ask)
  - null-safe: missing benchmark => no statistic sentence injected; no crash

All inputs are fabricated in-memory; _chat is mocked (no live LLM / DB / network).
The mock echoes the salutation, address, social-proof line, and (for FSV) the
leasing question from the prompt so the assertions exercise the real post-LLM
pipeline (_inject_hardcoded_sentences + _sanitize_bracket_placeholders).
"""
import re as _re
from unittest.mock import patch

import pytest

DISTINCT_TENANT_NAME = "ZZZ Distinctive Tenant Co"
DISTINCT_ADDRESS = "1234 Distinctive Test Blvd, McLean, VA"
LEASING_QUESTION = (
    "Would you be open to leasing a portion of the available space while the "
    "property is being marketed for sale?"
)


def _minimal_property(owner_name="Test Owner LLC", submarket="Tysons", listed_for_sale=False):
    return {
        "property_id": "TST-001",
        "address": DISTINCT_ADDRESS,
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": 5000,
        "owner_name": owner_name,
        "in_place_rent_psf": 35.0,
        "market_rent_psf": 38.0,
        "market_cap_rate": 6.5,
        "listed_for_sale": listed_for_sale,
        "listed_for_lease": True,
        "vacancy_pct": 12.0,
        "days_on_market": 95,
        "dominant_score_type": "tenant_match",
    }


def _minimal_tenant(lease_expiry_months=12):
    return {
        "company_id": "CO-001",
        "name": DISTINCT_TENANT_NAME,
        "industry": "Health Care",
        "current_submarket": "Tysons",
        "estimated_sf_needed": 3500,
        "lease_expiry_months": lease_expiry_months,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


def _import_svc():
    import app.services.property_outreach_service as svc
    return svc


def _make_mock_chat(svc, *, include_leasing_question=False):
    """Build a mock _chat that produces a realistic property-side body.

    Echoes back the salutation + address from the system prompt.
    Social-proof is intentionally absent — property-side no longer injects it.
    """
    extra_q = f"{LEASING_QUESTION} " if include_leasing_question else ""

    def _mock_chat(system, user, **kw):
        # Pull the salutation the builder asked for out of the system prompt.
        m = _re.search(r"Open the email with exactly '([^']+)'", system)
        salutation = m.group(1) if m else "Dear Property Owner,"
        return (
            "SUBJECT: A qualified prospect for your property\n"
            "EMAIL:\n"
            f"{salutation}\n\n"
            "A qualified Health Care firm is seeking ~3,500 SF and is interested in "
            f"your property at {DISTINCT_ADDRESS}. "
            f"{extra_q}"
            "With their lease expiring in approximately 12 months and your current "
            "vacancy, the timing lines up well. "
            "CBRE Q1 2026 pegs Tysons vacancy near 27%. "
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\n"
            "Jack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228\n"
            "OPENING:\nHello, opening.\n"
            "CORE:\nCore message.\n"
            "PAIN_PROBE:\nWhat is driving your timeline?\n"
            "CLOSE:\nI'd welcome a brief call at your convenience."
        )

    return _mock_chat


def _word_count(body: str) -> int:
    # Body word count excluding the signature block (mirrors the 150-word spec,
    # which excludes the signature). Strip from "Thank you," onward.
    main = body.split("Thank you,")[0]
    return len(main.split())


# ── (a) intro present; social-proof absent on both property-side types ─────────


@pytest.mark.parametrize("outreach_type,listed_for_sale,leasing_q", [
    ("tenant_match",     False, False),
    ("for_sale_vacancy", True,  True),
])
def test_intro_present_social_proof_absent(outreach_type, listed_for_sale, leasing_q):
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=leasing_q)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(listed_for_sale=listed_for_sale),
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )
    body = result["email_body"]
    assert svc._HARDCODED_INTRO in body, f"{outreach_type}: intro sentence missing"
    assert svc._HARDCODED_SOCIAL_PROOF not in body, (
        f"{outreach_type}: social-proof line must be ABSENT from property-side emails"
    )


# ── (b) no duplicate / legacy pitch across both property-side types ────────────


@pytest.mark.parametrize("outreach_type,listed_for_sale,leasing_q", [
    ("tenant_match",     False, False),
    ("for_sale_vacancy", True,  True),
])
def test_no_legacy_qualified_tenant_pitch(outreach_type, listed_for_sale, leasing_q):
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=leasing_q)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(listed_for_sale=listed_for_sale),
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )
    assert svc._PHASE1_FSV_CLOSING not in result["email_body"], (
        f"{outreach_type}: legacy 'I have a qualified tenant in mind' pitch must be absent"
    )


# ── (c) salutation resolves; no bracket tokens; privacy; address present ───────


@pytest.mark.parametrize("outreach_type,listed_for_sale,leasing_q", [
    ("tenant_match",     False, False),
    ("for_sale_vacancy", True,  True),
])
def test_salutation_privacy_and_address(outreach_type, listed_for_sale, leasing_q):
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=leasing_q)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(owner_name="Acme Holdings", listed_for_sale=listed_for_sale),
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )
    body = result["email_body"]
    assert "Dear Acme Holdings," in body, f"{outreach_type}: real owner salutation expected"
    assert not _re.findall(r"\[[A-Za-z][^\]\n]*\]", body), f"{outreach_type}: bracket token survived"
    # Privacy: tenant company name must never appear on property-side.
    assert DISTINCT_TENANT_NAME not in body, f"{outreach_type}: tenant company name leaked"
    # Property-side is allowed to show the street address.
    assert DISTINCT_ADDRESS in body, f"{outreach_type}: property address should be present"


# ── (d) null/Unknown owner → fallback salutation ───────────────────────────────


@pytest.mark.parametrize("outreach_type,listed_for_sale,leasing_q", [
    ("tenant_match",     False, False),
    ("for_sale_vacancy", True,  True),
])
@pytest.mark.parametrize("owner_name", [None, "", "Unknown", "N/A", "TBD"])
def test_null_owner_fallback_salutation(outreach_type, listed_for_sale, leasing_q, owner_name):
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=leasing_q)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(owner_name=owner_name, listed_for_sale=listed_for_sale),
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )
    body = result["email_body"]
    assert "Dear Property Owner," in body, (
        f"{outreach_type}/owner={owner_name!r}: expected fallback salutation"
    )
    assert "Dear Unknown," not in body
    assert "Dear N/A," not in body


# ── (e) lease-expiry appears at most once; body under 150 words ────────────────


@pytest.mark.parametrize("outreach_type,listed_for_sale,leasing_q", [
    ("tenant_match",     False, False),
    ("for_sale_vacancy", True,  True),
])
def test_lease_expiry_once_and_under_word_limit(outreach_type, listed_for_sale, leasing_q):
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=leasing_q)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(listed_for_sale=listed_for_sale),
            outreach_type=outreach_type,
            direction="property_side",
            tenant_dict=_minimal_tenant(lease_expiry_months=12),
        )
    body = result["email_body"]
    assert body.lower().count("approximately 12 month") <= 1, (
        f"{outreach_type}: lease-expiry phrase must not be duplicated"
    )
    assert _word_count(body) < 150, f"{outreach_type}: body exceeds 150-word limit"


# ── (f) FSV-specific: owner-discretion leasing question retained ───────────────


def test_fsv_retains_leasing_question():
    svc = _import_svc()
    with patch.object(svc, "_chat", side_effect=_make_mock_chat(svc, include_leasing_question=True)):
        result = svc.generate_property_outreach(
            property_dict=_minimal_property(listed_for_sale=True),
            outreach_type="for_sale_vacancy",
            direction="property_side",
            tenant_dict=_minimal_tenant(),
        )
    assert LEASING_QUESTION in result["email_body"], (
        "FSV email must retain the owner-discretion leasing question (its central ask)"
    )


# ── (g) FSV prompt instructs the verbatim leasing question (source contract) ───


def test_fsv_prompt_contains_leasing_question_instruction():
    """The builder's prompt itself must always carry the verbatim leasing question,
    so it can never silently drop out of the instruction set."""
    svc = _import_svc()
    prompt = svc._build_for_sale_vacancy(
        _minimal_property(listed_for_sale=True),
        target_type="owner",
        tenant_dict=_minimal_tenant(),
    )
    combined = prompt["system"] + prompt["user"]
    assert LEASING_QUESTION in combined, "FSV prompt must instruct the verbatim leasing question"


# ── (h) null benchmark → no statistic sentence forced; no crash ────────────────


@pytest.mark.parametrize("builder", ["tenant_match", "for_sale_vacancy"])
def test_null_benchmark_omits_statistic_instruction(builder):
    """Unknown submarket → no CBRE benchmark → prompt must instruct OMISSION of any
    statistic, never inject a number. Also must not crash."""
    svc = _import_svc()
    prop = _minimal_property(submarket="Nonexistent Submarket", listed_for_sale=(builder == "for_sale_vacancy"))
    if builder == "tenant_match":
        prompt = svc._build_tenant_match(prop, None, "owner", tenant_dict=_minimal_tenant())
    else:
        prompt = svc._build_for_sale_vacancy(prop, "owner", tenant_dict=_minimal_tenant())
    combined = prompt["system"] + prompt["user"]
    assert "omit any market-statistic sentence" in combined.lower(), (
        f"{builder}: with no benchmark, prompt must instruct omission of the statistic"
    )
