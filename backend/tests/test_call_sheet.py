"""
CALL SHEET — the deterministic call-side reference sheet that replaced the
recited call script (OPENING / DATA / ANGLE / DISCOVERY / PAIN PROBE /
THE CLOSE).

Locks:
  - build_call_sheet is pure code: NO LLM call ever (the OpenAI mock is
    asserted never invoked)
  - DATA renders every one of its ten labeled fields, and every missing value
    reads "not on file" — never invented, never blank, never a 500
  - ANGLE derives from the SAME select_rent_story rung + direction selection
    as the email rent line (per-rung/direction unit lock here; the end-to-end
    email ↔ sheet story match lives in test_rent_gap_ladder.py)
  - provisional submarket benchmarks (Fix 4) are flagged
    "(provisional — verify before quoting)" on the sheet and are NEVER quoted
    by email rungs 2/4/6 — the ladder falls through or drops the rent line
  - DISCOVERY is the core-message question set as a bulleted checklist; PAIN
    PROBE and THE CLOSE are single-line cues (close references the free lease
    analysis)

All inputs are in-memory. No live DB, network, or CoStar.
"""
import json
import re
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.config import SUBMARKET_BENCHMARKS, is_provisional_submarket
import app.services.outreach_service as svc
from app.services.outreach_service import (
    DISCOVERY_QUESTIONS,
    FREE_ANALYSIS_LINE,
    FULL_SERVICE_LINE,
    NOT_ON_FILE,
    PAIN_PROBE_CUE,
    PROVISIONAL_SUFFIX,
    build_angle_line,
    build_call_sheet,
    build_rent_gap_line,
    select_rent_story,
)

THIS_YEAR = date.today().year

TYSONS_ASKING      = SUBMARKET_BENCHMARKS["Tysons"]["market_rent_psf"]        # 39.10
CENTREVILLE_ASKING = SUBMARKET_BENCHMARKS["Centreville"]["market_rent_psf"]   # 30.49 (provisional proxy)


def _full_company():
    return {
        "name": "Acme Corp",
        "current_submarket": "Tysons",
        "current_headcount": 120,
        "current_sf_occupied": 20000,
        "current_building_class": "Class B",
        "lease_expiry_months": 9,
        "effective_rent_psf": 34.5,
        "starting_rent_psf": 28.5,
        "building_asking_rent_psf": 38.0,
        "lease_signed_year": 2021,
    }


# ── No LLM call in call-sheet generation ────────────────────────────────────────

def test_build_call_sheet_never_invokes_llm():
    """The call sheet is built deterministically in code — the OpenAI client is
    NEVER constructed or called, even with a fully-populated tenant."""
    openai_mock = MagicMock()
    anthropic_mock = MagicMock()
    with patch("openai.OpenAI", openai_mock), patch.dict(
        "os.environ", {"OPENAI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"}
    ):
        with patch("anthropic.Anthropic", anthropic_mock, create=True):
            sheet = build_call_sheet(_full_company())
            build_call_sheet({})   # empty tenant must not fall back to a model either
    openai_mock.assert_not_called()
    anthropic_mock.assert_not_called()
    assert set(sheet.keys()) == {
        "opening", "data", "angle", "core_message", "pain_probe", "the_close"
    }


# ── OPENING / DISCOVERY / PAIN PROBE / THE CLOSE cues ───────────────────────────

def test_opening_is_single_line_cue_with_identity_and_timing():
    sheet = build_call_sheet(_full_company())
    assert sheet["opening"] == (
        "Jack Zamer, The Commercial Real Estate Group — lease expiring in ~9 months."
    )
    assert "\n" not in sheet["opening"]


def test_opening_with_no_expiry_says_not_on_file():
    company = _full_company()
    company["lease_expiry_months"] = None
    sheet = build_call_sheet(company)
    assert sheet["opening"] == (
        "Jack Zamer, The Commercial Real Estate Group — lease expiry not on file."
    )


def test_discovery_is_bulleted_checklist_one_question_per_line():
    sheet = build_call_sheet(_full_company())
    lines = sheet["core_message"].splitlines()
    assert lines == [f"• {q}" for q in DISCOVERY_QUESTIONS]
    # The existing core-message topics survive as questions.
    joined = sheet["core_message"]
    for topic in ("rent", "growth", "floor-plan", "parking", "hybrid", "looking", "decision"):
        assert topic in joined, f"discovery checklist lost the {topic!r} question"


def test_pain_probe_and_close_are_single_line_cues():
    sheet = build_call_sheet(_full_company())
    assert sheet["pain_probe"] == PAIN_PROBE_CUE
    assert "\n" not in sheet["pain_probe"]
    # THE CLOSE still references the free lease analysis.
    assert sheet["the_close"] == FREE_ANALYSIS_LINE
    assert "free lease analysis" in sheet["the_close"]


def test_sheet_carries_no_prose_framing():
    """The call side carries no framing language — that lives in the email."""
    sheet = build_call_sheet(_full_company())
    whole = "\n".join(sheet.values())
    assert FULL_SERVICE_LINE not in whole
    email_line = build_rent_gap_line(34.5, 28.5, 38.0, "Tysons")
    assert email_line not in whole


# ── DATA block ──────────────────────────────────────────────────────────────────

DATA_LABELS = (
    "Effective Rent",
    "Starting Rent",
    "Building Asking Rent",
    "Lease Signed Year",
    "Lease Expires (months)",
    "SF Occupied",
    "Headcount",
    "Building Class",
    "Submarket Vacancy %",
    "Submarket Asking Rent",
)


def test_data_renders_all_values_when_present():
    sheet = build_call_sheet(_full_company())
    data = sheet["data"]
    assert "Effective Rent: $34.50/SF" in data
    assert "Starting Rent: $28.50/SF" in data
    assert "Building Asking Rent: $38.00/SF" in data
    assert "Lease Signed Year: 2021" in data
    assert "Lease Expires (months): 9" in data
    assert "SF Occupied: 20,000" in data
    assert "Headcount: 120" in data
    assert "Building Class: Class B" in data
    assert f"Submarket Vacancy %: {SUBMARKET_BENCHMARKS['Tysons']['vacancy_pct']:.1f}%" in data
    assert f"Submarket Asking Rent: ${TYSONS_ASKING:.2f}/SF" in data
    assert NOT_ON_FILE not in data


@pytest.mark.parametrize("label", DATA_LABELS)
def test_every_data_field_renders_not_on_file_when_null(label):
    """A tenant with nothing on record renders 'not on file' for EVERY field —
    never invented, never blank, never a 500."""
    sheet = build_call_sheet({"name": "Empty Co"})
    line = next(
        (l for l in sheet["data"].splitlines() if l.startswith(f"{label}:")), None
    )
    assert line is not None, f"DATA is missing the {label!r} row entirely"
    assert line == f"{label}: {NOT_ON_FILE}"


def test_data_zero_values_read_not_on_file_not_zero_dollars():
    """Zero/negative rents count as unset (matching the ladder), so the sheet
    never shows a $0.00 figure Jack could misquote."""
    sheet = build_call_sheet({
        "effective_rent_psf": 0.0,
        "starting_rent_psf": -1.0,
        "current_sf_occupied": 0,
        "current_headcount": 0,
    })
    data = sheet["data"]
    assert "Effective Rent: not on file" in data
    assert "Starting Rent: not on file" in data
    assert "SF Occupied: not on file" in data
    assert "Headcount: not on file" in data
    assert "$0.00" not in data
    # lease_expiry_months == 0 IS meaningful (expires now) and renders as 0.
    assert "Lease Expires (months): 0" in build_call_sheet({"lease_expiry_months": 0})["data"]


# ── ANGLE — same rung + direction selection as the email ────────────────────────

@pytest.mark.parametrize("eff,starting,bldg,rung,direction", [
    (30.0, None, 38.0, 1, "below"),
    (45.0, None, 38.0, 1, "above"),
    (30.0, None, None, 2, "below"),
    (45.0, None, None, 2, "above"),
    (None, 28.5, 38.0, 3, "below"),
    (None, 45.0, 38.0, 3, "above"),
    (None, 28.5, None, 4, "below"),
    (None, 45.0, None, 4, "above"),
    (None, None, 38.0, 5, None),
    (None, None, None, 6, None),
])
def test_select_rent_story_rung_and_direction(eff, starting, bldg, rung, direction):
    story = select_rent_story(eff, starting, bldg, "Tysons")
    assert story is not None
    assert story.rung == rung
    assert story.direction == direction


@pytest.mark.parametrize("eff,starting,bldg,marker", [
    (30.0, None, 38.0, "Below-market lease"),
    (45.0, None, 38.0, "Paying above current asking"),
    (None, 28.5, 38.0, "Below-market start"),
    (None, 45.0, 38.0, "Started above current asking"),
    (None, None, 38.0, "hedge off building asking"),
    (None, None, None, "hedge off Tysons asking"),
])
def test_angle_states_the_story_the_numbers_support(eff, starting, bldg, marker):
    angle = build_angle_line(eff, starting, bldg, "Tysons")
    assert marker in angle
    assert "\n" not in angle  # one line


def test_angle_hedge_claims_no_direction():
    for angle in (
        build_angle_line(None, None, 38.0, "Tysons"),
        build_angle_line(None, None, None, "Tysons"),
    ):
        assert "claim no direction" in angle
        for banned in ("Below", "above current asking", "market's moved"):
            assert banned not in angle


def test_angle_no_data_hedge_when_nothing_quotable():
    assert build_angle_line(None, None, None, "Nowheresville") == (
        "No rent data on file — no rent angle; lead with lease timing and discovery."
    )


def test_angle_flags_long_held_lease():
    """A below-market story with an old signing year reads as a long-held
    lease (the 'below-market long-held lease' story)."""
    year = THIS_YEAR - 5
    angle = build_angle_line(30.0, None, 38.0, "Tysons", lease_signed_year=year)
    assert f"held since {year}" in angle
    # A fresh signing is not called long-held.
    fresh = build_angle_line(30.0, None, 38.0, "Tysons", lease_signed_year=THIS_YEAR)
    assert "held since" not in fresh


def test_angle_and_email_figures_agree():
    """The dollar figures the ANGLE cites are exactly the tenant-value +
    benchmark pair the email rent line uses (numeric parity at the unit level)."""
    dollar = re.compile(r"\$\d[\d,]*\.\d{2}")
    for eff, starting, bldg in [
        (30.0, None, 38.0), (45.0, None, 38.0), (30.0, None, None),
        (None, 28.5, 38.0), (None, 45.0, None),
    ]:
        email_line = build_rent_gap_line(eff, starting, bldg, "Tysons")
        angle = build_angle_line(eff, starting, bldg, "Tysons")
        assert set(dollar.findall(angle)) == set(dollar.findall(email_line))


# ── Provisional benchmarks (Fix 4) ──────────────────────────────────────────────

def test_centreville_is_provisional_and_defaults_stay_real():
    """Centreville's numbers are a Fairfax Center proxy → provisional. Every
    other entry defaults to real (measured) without needing the key."""
    assert is_provisional_submarket("Centreville")
    for submarket in SUBMARKET_BENCHMARKS:
        if submarket != "Centreville":
            assert not is_provisional_submarket(submarket), (
                f"{submarket} unexpectedly flagged provisional"
            )
    assert not is_provisional_submarket("Nowheresville")
    assert not is_provisional_submarket(None)


def test_sheet_flags_provisional_submarket_numbers():
    sheet = build_call_sheet({"current_submarket": "Centreville"})
    data = sheet["data"]
    assert f"Submarket Asking Rent: ${CENTREVILLE_ASKING:.2f}/SF {PROVISIONAL_SUFFIX}" in data
    assert f"Submarket Vacancy %: 27.2% {PROVISIONAL_SUFFIX}" in data
    # Measured submarkets carry no flag.
    clean = build_call_sheet({"current_submarket": "Tysons"})["data"]
    assert PROVISIONAL_SUFFIX not in clean


def test_email_rungs_2_4_6_never_quote_provisional_asking_rent():
    """Rungs 2/4/6 fall through (or the rent line is dropped) rather than
    mailing a placeholder benchmark to a real tenant."""
    # Rung 2 (effective only) → no quotable benchmark → no line at all.
    assert build_rent_gap_line(34.5, None, None, "Centreville") is None
    # Rung 4 (starting only) → same.
    assert build_rent_gap_line(None, 28.5, None, "Centreville") is None
    # Rung 6 (nothing set) → same.
    assert build_rent_gap_line(None, None, None, "Centreville") is None
    # Building-asking rungs are unaffected — the building number is the
    # tenant's own, not the placeholder benchmark.
    line = build_rent_gap_line(34.5, None, 38.0, "Centreville")
    assert line is not None and "$38.00" in line and f"{CENTREVILLE_ASKING:.2f}" not in line
    line = build_rent_gap_line(None, 28.5, 38.0, "Centreville")
    assert line is not None and f"{CENTREVILLE_ASKING:.2f}" not in line
    # The ANGLE follows the same selection — never the placeholder figure.
    assert "hedge" not in build_angle_line(34.5, None, 38.0, "Centreville")
    assert f"{CENTREVILLE_ASKING:.2f}" not in build_angle_line(None, None, None, "Centreville") \
        or build_angle_line(None, None, None, "Centreville").startswith("No rent data")


# ── End-to-end: generate_outreach (mocked email LLM) ────────────────────────────

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

_EMAIL_ONLY_JSON = json.dumps({
    "email": {
        "subject": "Your lease and the market window",
        "body": (
            "Hi Jane,\n\nThe market window favors tenants right now.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            f"{SIGNATURE}"
        ),
    },
})


def _generate(company, monkeypatch, capture=None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    capture = capture if capture is not None else []
    def _factory(*args, **kwargs):
        return _FakeOpenAI(_EMAIL_ONLY_JSON, capture)
    with patch("openai.OpenAI", _factory):
        return svc.generate_outreach(company), capture


def test_generate_outreach_provisional_submarket_end_to_end(monkeypatch):
    """A Centreville tenant with no rent fields: the email carries NO rent line
    and the placeholder figure never reaches the prompt or the body; the CALL
    SHEET still shows the numbers, flagged provisional."""
    company = {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 40,
        "headcount_growth_pct": None,
        "current_sf_occupied": 8000,
        "current_submarket": "Centreville",
        "current_building_class": None,
        "lease_expiry_months": 12,
        "lease_expiry_date": None,
        "primary_contact_name": "Jane Smith",
        "primary_contact_title": None,
        "tenant_representative": None,
        "current_rent_psf": None,
        "effective_rent_psf": None,
        "starting_rent_psf": None,
        "building_asking_rent_psf": None,
        "lease_signed_year": None,
        "future_move_flag": False,
        "future_move_type": None,
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 60,
        "priority": "WORKABLE",
    }
    result, capture = _generate(company, monkeypatch)
    placeholder = f"{CENTREVILLE_ASKING:.2f}"

    # Never quoted to the model, never in the body.
    for msg in capture[0]["messages"]:
        assert placeholder not in msg["content"]
        assert "27.2" not in msg["content"]
    assert placeholder not in result["email"]["body"]
    assert "Asking rents" not in result["email"]["body"]  # rung 6 dropped

    # The sheet still shows the numbers — flagged.
    data = result["call_script"]["data"]
    assert f"Submarket Asking Rent: ${CENTREVILLE_ASKING:.2f}/SF {PROVISIONAL_SUFFIX}" in data
    assert result["call_script"]["angle"].startswith("No rent data on file")


def test_generate_outreach_calls_llm_once_for_email_only(monkeypatch):
    """generate_outreach makes exactly ONE model call (the email); the call
    sheet adds zero LLM calls."""
    result, capture = _generate(
        {**_full_company(), "industry": "Technology", "priority": "HIGH",
         "opportunity_score": 80, "lease_trajectory": "AUTO"},
        monkeypatch,
    )
    assert len(capture) == 1
    assert capture[0]["model"] == "gpt-4o"
    # And the sheet arrived fully populated despite the email-only mock output.
    assert "Effective Rent: $34.50/SF" in result["call_script"]["data"]
