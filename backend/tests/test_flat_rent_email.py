"""
Flat-rent email contract — locks the tenant-outreach behavior for the specific
scenario where a tenant's STARTING rent exactly equals the current BUILDING
ASKING rent ("flat since you signed").

Contract:
  * starting_rent_psf == building_asking_rent_psf (effective not set) selects
    rung 3 with the new "flat" direction — a distinct signal, not the generic
    above-asking framing.
  * The email rent line reads the flat-rent structure VERBATIM (starting $ ==
    asking $, "flat since you signed", landlord "hasn't had to compete for your
    renewal yet ... once the clock starts") — and never the escalation-creep or
    above-asking phrasing.
  * The branch fires ONLY when lease_expiry_months is on file. With missing/null
    lease timing OR missing rent fields, it falls back cleanly to the existing
    default line — no crash, no 500.
  * Numeric parity: every dollar figure in the flat-rent email line appears on
    the same tenant's CALL SHEET (DATA block + ANGLE line), and the ANGLE derives
    from the SAME rung + direction selection as the email.
  * GPT-4o stays the (mocked) model, called once, for the EMAIL only; the CALL
    SHEET is built in code.

All inputs are in-memory: the OpenAI client is faked (its output deliberately
omits every required line), the web-intel call is stubbed. No live DB, network,
or GPT-4o call.
"""
import json
import re
from unittest.mock import patch

import pytest

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.config import SUBMARKET_BENCHMARKS
import app.services.outreach_service as svc
from app.services.outreach_service import (
    FREE_ANALYSIS_LINE,
    FULL_SERVICE_LINE,
    build_angle_line,
    build_rent_gap_line,
    select_rent_story,
)

TYSONS_ASKING = SUBMARKET_BENCHMARKS["Tysons"]["market_rent_psf"]

# The one figure both sides of the flat-rent line quote (starting == asking).
FLAT = 38.00

EXPECTED_FLAT_LINE = (
    "Your starting rent was $38.00/SF, and the building's quoting $38.00 today — "
    "flat since you signed. That doesn't necessarily mean you're overpaying, but it "
    "does mean this landlord hasn't had to compete for your renewal yet, and that "
    "usually changes once the clock starts."
)

# Phrasings that belong to OTHER rent scenarios and must never appear in the
# flat-rent line.
_CREEP_MARKER  = "crept closer to that asking number"
_ABOVE_MARKER  = "almost certainly paying more today than a new tenant signing tomorrow"


# ── Unit: the pure ladder selection + rent line ─────────────────────────────────

def test_select_rent_story_flags_flat_direction_on_equality():
    """starting == building asking (no effective) → rung 3, direction 'flat'."""
    story = select_rent_story(None, FLAT, FLAT, "Tysons")
    assert story is not None
    assert story.rung == 3
    assert story.direction == "flat"
    assert story.tenant_value == FLAT
    assert story.benchmark_value == FLAT
    assert story.benchmark_kind == "building"


def test_flat_rent_line_exact_wording_when_lease_timing_on_file():
    """The exact flat-rent contract — asserted against literal text, not a helper,
    so a drive-by rewrite fails here."""
    line = build_rent_gap_line(None, FLAT, FLAT, "Tysons", lease_expiry_months=9)
    assert line == EXPECTED_FLAT_LINE
    # Never the escalation-creep or above-asking framing.
    assert _CREEP_MARKER not in line
    assert _ABOVE_MARKER not in line


def test_flat_rent_line_never_claims_overpaying_or_behind():
    """Neutral framing — flat rent is not an accusation."""
    line = build_rent_gap_line(None, FLAT, FLAT, "Tysons", lease_expiry_months=9)
    assert "flat since you signed" in line
    assert "hasn't had to compete for your renewal" in line
    assert "once the clock starts" in line
    # The spec's neutral phrasing explicitly de-accuses ("doesn't necessarily
    # mean you're overpaying") — so guard against the *accusatory* framings only.
    assert "doesn't necessarily mean you're overpaying" in line
    for banned in ("you're behind", "paying above", _CREEP_MARKER, _ABOVE_MARKER):
        assert banned not in line


def test_flat_falls_back_to_above_line_without_lease_timing():
    """No lease_expiry_months on file → the flat branch does NOT fire; the line
    falls back to the existing above-asking default (no crash, no flat phrasing).
    This is exactly what preserves the prior contract for callers that pass no
    lease timing."""
    line = build_rent_gap_line(None, FLAT, FLAT, "Tysons")  # lease_expiry_months default None
    assert "flat since you signed" not in line
    assert _ABOVE_MARKER in line


def test_flat_needs_both_rent_fields_missing_one_is_not_flat():
    """A rent field missing means there is no equality to detect — the branch
    cannot fire. Building-only → hedge; starting-only vs submarket → not flat."""
    # Only building asking on file → rung 5 hedge, never flat.
    hedge = build_rent_gap_line(None, None, FLAT, "Tysons", lease_expiry_months=9)
    assert "flat since you signed" not in hedge
    assert "I don't know where your lease lands" in hedge
    # Starting == submarket asking but NO building number → rung 4, not flat
    # (flat is a building-asking signal only).
    story = select_rent_story(None, TYSONS_ASKING, None, "Tysons")
    assert story.rung == 4
    assert story.direction != "flat"


def test_effective_wins_over_flat_starting():
    """If effective rent is set, rung 1 wins even when starting == building asking
    — flat-rent framing only applies to the starting-vs-building scenario."""
    line = build_rent_gap_line(30.0, FLAT, FLAT, "Tysons", lease_expiry_months=9)
    assert "flat since you signed" not in line
    assert "effective" in line  # rung 1 direct line


def test_other_rent_scenarios_unchanged_by_flat_carveout():
    """Strictly-below and strictly-above starting rents keep their existing
    lines even with lease timing on file — only exact equality is flat."""
    below = build_rent_gap_line(None, 28.5, FLAT, "Tysons", lease_expiry_months=9)
    assert _CREEP_MARKER in below
    assert "flat since you signed" not in below
    above = build_rent_gap_line(None, 45.0, FLAT, "Tysons", lease_expiry_months=9)
    assert _ABOVE_MARKER in above
    assert "flat since you signed" not in above


# ── Unit: CALL SHEET ANGLE parity with the email ────────────────────────────────

def test_angle_reflects_flat_story_with_lease_timing():
    angle = build_angle_line(None, FLAT, FLAT, "Tysons", lease_expiry_months=9)
    assert angle.startswith("Flat since signing")
    assert "hasn't had to compete for the renewal yet" in angle
    assert "\n" not in angle  # one line
    # Same figure(s) as the email line — numeric parity at the unit level.
    dollar = re.compile(r"\$\d[\d,]*\.\d{2}")
    email_line = build_rent_gap_line(None, FLAT, FLAT, "Tysons", lease_expiry_months=9)
    assert set(dollar.findall(angle)) == set(dollar.findall(email_line))


def test_angle_falls_back_without_lease_timing_matching_the_email():
    """No lease timing → both email and angle fall back to the above-asking story,
    so they never diverge."""
    angle = build_angle_line(None, FLAT, FLAT, "Tysons")
    assert "Flat since signing" not in angle
    assert angle.startswith("Started above current asking")


# ── Null-safety: nothing here should ever 500 ───────────────────────────────────

@pytest.mark.parametrize("eff,starting,bldg,lease_mo", [
    (None, None, None, None),
    (None, FLAT, FLAT, None),
    (0.0, FLAT, FLAT, 0),        # lease_mo == 0 is meaningful (expires now)
    (None, FLAT, FLAT, 12),
    (None, None, FLAT, 12),
])
def test_flat_paths_never_raise(eff, starting, bldg, lease_mo):
    # Neither builder raises on any null/zero combination.
    build_rent_gap_line(eff, starting, bldg, "Tysons", lease_expiry_months=lease_mo)
    build_angle_line(eff, starting, bldg, "Tysons", lease_expiry_months=lease_mo)


def test_lease_mo_zero_still_fires_flat_branch():
    """lease_expiry_months == 0 (expires now) is on-file, not missing — the flat
    branch fires (0 is falsy but not None)."""
    line = build_rent_gap_line(None, FLAT, FLAT, "Tysons", lease_expiry_months=0)
    assert line == EXPECTED_FLAT_LINE


# ── Integration: full generate_outreach (GPT-4o mocked) ─────────────────────────

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


SIGNATURE = "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"

# The mock deliberately omits every required line so the assertions can only pass
# via the service's deterministic post-insert guarantee (not the mock).
_BARE_LLM_JSON = json.dumps({
    "email": {
        "subject": "Your Tysons lease and the market window",
        "body": (
            "Hi Jane,\n\n"
            "The market window favors tenants right now.\n\n"
            "What's the biggest pressure on your space today?\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            f"{SIGNATURE}"
        ),
    },
})


def _flat_company(lease_mo=9):
    return {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 120,
        "headcount_growth_pct": 8.0,
        "current_sf_occupied": 20000,
        "current_submarket": "Tysons",
        "current_building_class": "Class B",
        "lease_expiry_months": lease_mo,
        "lease_expiry_date": None,
        "lease_signed_year": None,
        "primary_contact_name": "Jane Smith",
        "primary_contact_title": "COO",
        "tenant_representative": None,
        "current_rent_psf": FLAT,
        "effective_rent_psf": None,
        "starting_rent_psf": FLAT,
        "building_asking_rent_psf": FLAT,
        "future_move_flag": False,
        "future_move_type": None,
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


def _generate(company, monkeypatch, capture=None, llm_json=None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    capture = capture if capture is not None else []

    def _factory(*args, **kwargs):
        return _FakeOpenAI(llm_json or _BARE_LLM_JSON, capture)

    with patch("openai.OpenAI", _factory):
        return svc.generate_outreach(company)


def test_generated_email_carries_flat_rent_structure(monkeypatch):
    """End-to-end: a flat-rent tenant's generated email body contains the exact
    flat-rent line plus the shared flat-rent email scaffolding (lease timing,
    market window, full-service positioning, free-analysis close)."""
    capture: list = []
    result = _generate(_flat_company(lease_mo=9), monkeypatch, capture=capture)
    body = result["email"]["body"]

    # The flat-rent line, verbatim and exactly once.
    assert EXPECTED_FLAT_LINE in body
    assert body.count(EXPECTED_FLAT_LINE) == 1
    # Never the competing rent-scenario phrasings.
    assert _CREEP_MARKER not in body
    assert _ABOVE_MARKER not in body
    # Shared email scaffolding is present (the structure the spec describes).
    assert "9 month" in body                      # lease timing
    assert FULL_SERVICE_LINE in body              # full-service positioning
    assert FREE_ANALYSIS_LINE in body             # free-analysis close
    # Ordered ahead of the closing ask, no bracket placeholders left behind.
    assert body.index(EXPECTED_FLAT_LINE) < body.index("I'd welcome a brief call")
    assert "[" not in body and "]" not in body
    # GPT-4o, called exactly once, for the email only.
    assert len(capture) == 1
    assert capture[0]["model"] == "gpt-4o"


def test_flat_line_is_fed_verbatim_to_the_model_prompt(monkeypatch):
    """The flat-rent line is instructed VERBATIM in the GPT-4o email prompt, so
    the model is steered to it (and the post-insert guarantees it regardless)."""
    capture: list = []
    _generate(_flat_company(lease_mo=9), monkeypatch, capture=capture)
    system_prompt = capture[0]["messages"][0]["content"]
    assert EXPECTED_FLAT_LINE in system_prompt


def test_generated_call_sheet_numeric_parity_and_flat_angle(monkeypatch):
    """The flat figure the email cites appears on the CALL SHEET (DATA + ANGLE),
    and the ANGLE tells the same flat story."""
    result = _generate(_flat_company(lease_mo=9), monkeypatch)
    cs = result["call_script"]
    sheet = f"{cs['data']}\n{cs['angle']}"

    dollar = re.compile(r"\$\d[\d,]*\.\d{2}")
    for figure in dollar.findall(EXPECTED_FLAT_LINE):
        assert figure in sheet, f"email cites {figure} but the CALL SHEET omits it:\n{sheet}"

    assert cs["angle"].startswith("Flat since signing")
    # DATA block carries both raw fields the flat line derives from.
    assert "Starting Rent: $38.00/SF" in cs["data"]
    assert "Building Asking Rent: $38.00/SF" in cs["data"]


def test_missing_lease_timing_does_not_emit_flat_structure(monkeypatch):
    """A flat-rent tenant with NO lease_expiry_months falls back cleanly: the
    email must not carry the flat-rent line (branch gated off), and generation
    must not raise."""
    result = _generate(_flat_company(lease_mo=None), monkeypatch)
    body = result["email"]["body"]
    assert "flat since you signed" not in body
    # Fallback line (above-asking default) is what appears instead.
    assert _ABOVE_MARKER in body
