"""
Concession-scrub contract — locks the removal of the unverified CBRE Q1 2026
concession statistics (free-rent months, TI-allowance $/SF) from generated
outreach, and the gate that governs their reintroduction.

Locks:
  - CONCESSION_DATA_VERIFIED defaults to False and is read at GENERATION time
    (never cached at import), so flipping it needs no other code change.
  - With the gate CLOSED (False):
      * the tenant EMAIL body carries the fixed qualitative concession paragraph
        and NO concession digit-plus-unit figure (no "X months free", no
        "$Y/SF TI"), and NO named source (CBRE / JLL / Cushman / CoStar);
      * the CALL SHEET holds the same — no concession figure, no named source;
      * the prompt handed to GPT-4o carries no concession figure and no named
        source either (the model is never given fabrication material);
      * email and call sheet are in concession numeric parity (neither carries a
        concession figure → parity trivially holds).
  - With the gate OPEN (True) and verified figures present, the same concession
    numbers appear in BOTH the email body and the call sheet (parity holds with
    real numbers).

All inputs are in-memory: the OpenAI call is mocked, the web-intel call stubbed.
No live DB, network, or CoStar.
"""
import json
import re
from unittest.mock import patch

import pytest

import app.config as config
import app.services.outreach_service as svc
from app.services.outreach_service import (
    CONCESSION_QUALITATIVE_PARAGRAPH,
    generate_outreach,
)


NAMED_SOURCES = ("CBRE", "JLL", "Cushman", "CoStar")

# Digit-plus-unit concession patterns — free-rent months and TI-allowance $/SF.
# Deliberately same-line ([^\n]) so an allowed asking-rent "$39.10/SF" that
# happens to share a block with the word "TI" elsewhere can't false-positive.
_CONCESSION_DIGIT_UNIT = re.compile(
    r"\d[\d.,]*\s*\+?\s*months?\s+(?:of\s+)?free"        # "6 months free" / "6+ months of free"
    r"|months?\s+of\s+free\s+rent"                        # "months of free rent"
    r"|\$\s*\d[\d.,]*\s*\+?\s*/?\s*SF[^\n]{0,24}\bTI\b"   # "$60/SF in TI"
    r"|\bTI\b[^\n]{0,24}\$\s*\d",                         # "TI allowance ... $60"
    re.IGNORECASE,
)


SIGNATURE = "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"

# A minimal, compliant model body (no numbers, no sources): the deterministic
# path is what injects the qualitative concession paragraph, rent line, etc.
_EMAIL_ONLY_JSON = json.dumps({
    "email": {
        "subject": "Your Tysons lease and the window",
        "body": (
            "Hi Jane,\n\n"
            "Your lease is up in about 9 months in Tysons, and getting ahead of the "
            "renewal-or-relocate decision pays.\n\n"
            "What's the biggest pressure on your space right now?\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            f"{SIGNATURE}"
        ),
    },
})


class _FakeMessage:
    def __init__(self, content): self.content = content


class _FakeChoice:
    def __init__(self, content): self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content, capture): self._content, self._capture = content, capture
    def create(self, **kwargs):
        self._capture.append(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content, capture): self.completions = _FakeCompletions(content, capture)


class _FakeOpenAI:
    def __init__(self, content, capture): self.chat = _FakeChat(content, capture)


def _company():
    return {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 120,
        "headcount_growth_pct": 8.0,
        "current_sf_occupied": 20000,
        "current_submarket": "Tysons",
        "current_building_class": "Class B",
        "lease_expiry_months": 9,
        "lease_expiry_date": None,
        "primary_contact_name": "Jane Smith",
        "primary_contact_title": "COO",
        "tenant_representative": None,
        "effective_rent_psf": None,
        "starting_rent_psf": None,
        "building_asking_rent_psf": None,
        "lease_signed_year": None,
        "future_move_flag": False,
        "future_move_type": None,
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


def _generate(monkeypatch, company=None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    capture: list = []

    def _factory(*args, **kwargs):
        return _FakeOpenAI(_EMAIL_ONLY_JSON, capture)

    with patch("openai.OpenAI", _factory):
        result = generate_outreach(company or _company())
    return result, capture


def _sheet_text(call_script: dict) -> str:
    return "\n".join(
        call_script[k]
        for k in ("opening", "data", "angle", "core_message", "pain_probe", "the_close")
    )


# ── The gate itself ─────────────────────────────────────────────────────────────

def test_flag_defaults_to_false():
    assert config.CONCESSION_DATA_VERIFIED is False


def test_flag_is_read_at_generation_time_not_cached(monkeypatch):
    """Flipping config.CONCESSION_DATA_VERIFIED changes output with NO other
    change — the generator reads the module attribute at call time."""
    closed, _ = _generate(monkeypatch)
    assert CONCESSION_QUALITATIVE_PARAGRAPH in closed["email"]["body"]

    monkeypatch.setattr(config, "CONCESSION_DATA_VERIFIED", True)
    opened, _ = _generate(monkeypatch)
    assert CONCESSION_QUALITATIVE_PARAGRAPH not in opened["email"]["body"]


# ── Gate CLOSED (default): no numbers, no sources, qualitative present ───────────

def test_email_body_has_qualitative_paragraph_no_concession_figures(monkeypatch):
    result, _ = _generate(monkeypatch)
    body = result["email"]["body"]
    assert CONCESSION_QUALITATIVE_PARAGRAPH in body
    hit = _CONCESSION_DIGIT_UNIT.search(body)
    assert hit is None, f"email body carries a concession figure: {hit.group(0)!r}"


def test_email_body_names_no_source(monkeypatch):
    body = _generate(monkeypatch)[0]["email"]["body"]
    for src in NAMED_SOURCES:
        assert src not in body, f"email body attributes to {src}"


def test_call_sheet_has_no_concession_figure_and_no_source(monkeypatch):
    sheet = _sheet_text(_generate(monkeypatch)[0]["call_script"])
    hit = _CONCESSION_DIGIT_UNIT.search(sheet)
    assert hit is None, f"call sheet carries a concession figure: {hit.group(0)!r}"
    for src in NAMED_SOURCES:
        assert src not in sheet, f"call sheet attributes to {src}"


def test_prompt_never_hands_model_concession_figures(monkeypatch):
    """The GPT-4o prompt hands the model no concession figure (no free-rent /
    TI number) to quote. (The prompt DOES name CBRE/JLL/Cushman/CoStar — but
    only inside the anti-fabrication rule that forbids attributing to them.)"""
    _, capture = _generate(monkeypatch)
    assert len(capture) == 1
    prompt_text = "\n".join(m["content"] for m in capture[0]["messages"])
    hit = _CONCESSION_DIGIT_UNIT.search(prompt_text)
    assert hit is None, f"prompt hands the model a concession figure: {hit.group(0)!r}"


def test_prompt_carries_the_anti_fabrication_rule(monkeypatch):
    """The system prompt forbids inventing figures and attributing to any named
    market-research source."""
    _, capture = _generate(monkeypatch)
    system_prompt = capture[0]["messages"][0]["content"]
    assert "ANTI-FABRICATION" in system_prompt
    for src in NAMED_SOURCES:
        assert src in system_prompt, f"anti-fabrication rule must name {src} as off-limits"


def test_email_and_call_sheet_concession_parity_when_closed(monkeypatch):
    """Numeric parity: with the gate closed neither surface carries a concession
    figure, so any concession figure in one appears in the other (both empty)."""
    result, _ = _generate(monkeypatch)
    body = result["email"]["body"]
    sheet = _sheet_text(result["call_script"])
    assert _CONCESSION_DIGIT_UNIT.search(body) is None
    assert _CONCESSION_DIGIT_UNIT.search(sheet) is None


# ── Gate OPEN: verified figures flow to BOTH surfaces (parity holds) ─────────────

def test_verified_figures_appear_in_both_email_and_sheet(monkeypatch):
    """When the gate is open, the SAME verified concession numbers appear in the
    email body and the call sheet — any figure in one appears in the other."""
    monkeypatch.setattr(config, "CONCESSION_DATA_VERIFIED", True)
    free_rent = svc.NOVA_OFFICE_BENCHMARKS["avg_free_rent_months"]
    ti_psf = svc.NOVA_OFFICE_BENCHMARKS["avg_ti_psf"]

    result, _ = _generate(monkeypatch)
    body = result["email"]["body"]
    sheet = _sheet_text(result["call_script"])

    for token in (str(free_rent), str(ti_psf)):
        assert token in body, f"verified concession figure {token} missing from email"
        assert token in sheet, f"verified concession figure {token} missing from call sheet"
