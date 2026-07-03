"""
Rent-gap ladder — locks all SIX rungs (with BOTH directions on rungs 1-4) to
their expected rent line in the cold email body, plus email ↔ CALL SHEET
numeric parity and the Lease Activity effective/starting-rent import.

Ladder contract (sharpest set field wins: effective > starting > hedge; within
each, building asking > submarket asking):
  (1) effective + building asking       → direction-framed line vs building asking
  (2) effective, no building            → direction-framed line vs submarket asking
  (3) starting + building, no effective → direction-framed line vs building asking
        (BELOW branch = the spec-locked escalation-creep wording; ABOVE branch
         = neutral "began above today's asking" framing)
  (4) starting alone (no building)      → direction-framed line vs submarket asking
  (5) building asking only              → hedge framing off building asking (no direction)
  (6) nothing set                       → hedge framing off submarket asking (no direction)

Direction contract (rungs 1-4): tenant number BELOW the benchmark → "the
market's moved since you signed" framing, factual, never implying the tenant is
behind or overpaying; tenant number ABOVE the benchmark → neutral information
framing (paying above current asking, worth confirming before renewal), no
blame, no manufactured urgency. Rungs 5/6 claim no direction and are untouched.

PARITY CONTRACT — deliberately rewritten from the old prose parity
--------------------------------------------------------------------
The call side is now a deterministic CALL SHEET (reference material Jack
glances at mid-call), not a script that recites the email. The old tests
locked the identical rent-line PROSE into both the email and the call script's
THE HOOK beat; that beat no longer exists — the call side carries no prose
framing at all. Parity now means NUMERIC parity: every dollar figure (and
year) used in the tenant's email rent line must appear on that tenant's CALL
SHEET (in the DATA block and/or the ANGLE line), and the ANGLE line must be
derived from the SAME rung + direction selection as the email. That is the
guarantee that matters — the call and the email can never tell different
stories for the same tenant — without freezing two copies of the same prose.

All inputs are in-memory: the OpenAI call is mocked (and deliberately returns
an email WITHOUT the required lines and NO call-script keys at all, so these
tests prove the deterministic guarantee, not the mock), the web-intel call is
stubbed, and the import tests use in-memory SQLite. No live DB, network, or
CoStar.
"""
import json
import re
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.config import SUBMARKET_BENCHMARKS
from app.database import Base
import app.services.outreach_service as svc
from app.services.outreach_service import (
    FREE_ANALYSIS_LINE,
    FULL_SERVICE_LINE,
    build_rent_gap_line,
)

THIS_YEAR = date.today().year


TYSONS_ASKING = SUBMARKET_BENCHMARKS["Tysons"]["market_rent_psf"]  # 39.10


# ── Expected rent lines ─────────────────────────────────────────────────────────

def _direct_below(eff: float, ask: float, where: str) -> str:
    return (
        f"You're at ${eff:.2f}/SF effective against ${ask:.2f}/SF asking in {where} "
        f"right now — the market's moved since you signed, and it's worth knowing "
        f"exactly where that leaves you before you renew."
    )


def _direct_above(eff: float, ask: float, where: str) -> str:
    return (
        f"You're at ${eff:.2f}/SF effective against ${ask:.2f}/SF asking in {where} "
        f"right now — you're paying above the current asking number, and it's worth "
        f"confirming what that means for your options before your renewal conversation."
    )


def _creep_below(starting: float, ask: float, quoted_by: str) -> str:
    return (
        f"I see your lease started at ${starting:.2f}/SF, and {quoted_by} quoting "
        f"${ask:.2f} right now. By the nature of things — annual escalations, "
        f"operating expense pass-throughs — your rent's likely crept closer to that "
        f"asking number by now, and that's exactly the kind of thing that's easy to "
        f"miss until the decision to renew or relocate is already on top of you. "
        f"I'd like to dig in and show you exactly where you stand."
    )


def _creep_above(starting: float, ask: float, quoted_by: str) -> str:
    return (
        f"I see your lease started at ${starting:.2f}/SF, and {quoted_by} quoting "
        f"${ask:.2f} right now — your lease began above today's asking number, and "
        f"it's worth confirming what that means for your options before your renewal "
        f"conversation. I'd like to dig in and show you exactly where you stand."
    )


# Lease-vintage clause of the hedge line: vague when the signing year is
# unknown; scaled to real elapsed time when known.
VAGUE_TENURE_CLAUSE = "a lot of tenants who've been in place a while are sitting above that"


def _hedge_line(ask: float, where: str, signed_clause: str = VAGUE_TENURE_CLAUSE) -> str:
    return (
        f"Asking rents in {where} are quoting around ${ask:.2f}/SF right now — "
        f"{signed_clause}. I don't know where your lease lands, but that gap is "
        f"exactly what I check."
    )


# Every rung, BOTH directions on rungs 1-4 (Tysons asking 39.10; building 38.00).
RUNGS = {
    # key: (effective, starting, building, expected line)
    "1_below_building":   (30.0, None, 38.0, _direct_below(30.0, 38.0, "your building")),
    "1_above_building":   (45.0, None, 38.0, _direct_above(45.0, 38.0, "your building")),
    "2_below_submarket":  (30.0, None, None, _direct_below(30.0, TYSONS_ASKING, "Tysons")),
    "2_above_submarket":  (45.0, None, None, _direct_above(45.0, TYSONS_ASKING, "Tysons")),
    "3_below_building":   (None, 28.5, 38.0, _creep_below(28.5, 38.0, "your building's")),
    "3_above_building":   (None, 45.0, 38.0, _creep_above(45.0, 38.0, "your building's")),
    "4_below_submarket":  (None, 28.5, None, _creep_below(28.5, TYSONS_ASKING, "Tysons is")),
    "4_above_submarket":  (None, 45.0, None, _creep_above(45.0, TYSONS_ASKING, "Tysons is")),
    "5_building_hedge":   (None, None, 38.0, _hedge_line(38.0, "your building")),
    "6_submarket_hedge":  (None, None, None, _hedge_line(TYSONS_ASKING, "Tysons")),
}


# ── Unit: the pure ladder function (rungs 1-3 in both directions = 6 cases,
#    plus rung 4 both directions and the two hedges) ────────────────────────────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_build_rent_gap_line_rungs(rung):
    eff, starting, bldg, expected = RUNGS[rung]
    assert build_rent_gap_line(eff, starting, bldg, "Tysons") == expected


def test_below_direction_never_implies_tenant_is_behind():
    """BELOW-benchmark framing is factual market movement — never 'behind',
    'overpaying', or an above-asking claim."""
    for key in ("1_below_building", "2_below_submarket", "3_below_building", "4_below_submarket"):
        eff, starting, bldg, _ = RUNGS[key]
        line = build_rent_gap_line(eff, starting, bldg, "Tysons")
        assert "market's moved" in line or "crept closer" in line
        for banned in ("behind", "overpaying", "paying above", "began above"):
            assert banned not in line, f"{key}: below branch says {banned!r}: {line}"


def test_above_direction_is_neutral_no_blame_no_urgency():
    """ABOVE-benchmark framing is neutral information — paying above current
    asking, worth confirming before renewal. No blame, no manufactured urgency,
    and none of the below-branch claims."""
    for key in ("1_above_building", "2_above_submarket", "3_above_building", "4_above_submarket"):
        eff, starting, bldg, _ = RUNGS[key]
        line = build_rent_gap_line(eff, starting, bldg, "Tysons")
        assert "worth confirming" in line
        assert "above" in line
        for banned in ("market's moved", "crept closer", "easy to miss", "already on top of you"):
            assert banned not in line, f"{key}: above branch says {banned!r}: {line}"


def test_rung_3_exact_spec_wording_below_branch_only():
    """Rung 3's escalation-creep wording is spec-locked — asserted against the
    literal text, not the helper, so a drive-by rewrite fails here. The lock
    applies ONLY to the below-asking branch (starting < building asking); the
    above branch carries the new neutral phrasing and must NOT use the creep
    claim."""
    line = build_rent_gap_line(None, 28.5, 38.0, "Tysons")
    assert line == (
        "I see your lease started at $28.50/SF, and your building's quoting $38.00 "
        "right now. By the nature of things — annual escalations, operating expense "
        "pass-throughs — your rent's likely crept closer to that asking number by now, "
        "and that's exactly the kind of thing that's easy to miss until the decision "
        "to renew or relocate is already on top of you. I'd like to dig in and show "
        "you exactly where you stand."
    )
    # Above branch (starting > building asking): different phrasing, no creep line.
    above = build_rent_gap_line(None, 45.0, 38.0, "Tysons")
    assert "crept closer to that asking number" not in above
    assert "began above today's asking number" in above


def test_effective_wins_outright_over_starting():
    """All three fields set → rung 1 (effective vs building), never the creep line."""
    line = build_rent_gap_line(45.0, 28.5, 38.0, "Tysons")
    assert line == RUNGS["1_above_building"][3]
    assert "started at" not in line


def test_starting_beats_hedge():
    # starting + building → rung 3, not the building hedge (rung 5)
    assert build_rent_gap_line(None, 28.5, 38.0, "Tysons") == RUNGS["3_below_building"][3]
    # starting alone → rung 4, not the submarket hedge (rung 6)
    assert build_rent_gap_line(None, 28.5, None, "Tysons") == RUNGS["4_below_submarket"][3]


# ── Hedge rungs anchored to the tenant's REAL lease vintage (untouched) ─────────

def test_hedge_null_year_uses_vague_phrasing_with_no_year_at_all():
    """Unknown signing year → 'tenants who've been in place a while'; no
    specific year is ever cited (nothing fabricated)."""
    for line in (
        build_rent_gap_line(None, None, 38.0, "Tysons"),
        build_rent_gap_line(None, None, None, "Tysons"),
    ):
        assert VAGUE_TENURE_CLAUSE in line
        assert not re.search(r"\b(19|20)\d{2}\b", line), f"vague hedge cites a year: {line}"


def test_hedge_old_signing_cites_year_back_in():
    """Signed >= 4 years ago → 'signed back in [year]'."""
    year = THIS_YEAR - 5
    line = build_rent_gap_line(None, None, 38.0, "Tysons", lease_signed_year=year)
    assert f"a lot of leases signed back in {year} are sitting above that" in line
    line = build_rent_gap_line(None, None, None, "Tysons", lease_signed_year=year)
    assert f"a lot of leases signed back in {year} are sitting above that" in line


def test_hedge_couple_years_ago_scaled_phrasing():
    """Signed 2-3 years ago → 'a couple of years back, in [year]'."""
    year = THIS_YEAR - 2
    line = build_rent_gap_line(None, None, 38.0, "Tysons", lease_signed_year=year)
    assert f"a lot of leases signed a couple of years back, in {year}, are sitting above that" in line


def test_hedge_recent_signing_is_not_described_as_above_market():
    """The reported bug: a tenant who signed THIS year must never read
    'signed before 2022' (or any claim their fresh lease sits above market)."""
    line = build_rent_gap_line(None, None, 38.0, "Tysons", lease_signed_year=THIS_YEAR)
    assert f"even a lease signed as recently as {THIS_YEAR} can land differently" in line
    assert "sitting above that" not in line
    assert "signed before" not in line
    assert "2022" not in line


def test_hedges_claim_no_direction():
    """Rungs 5/6 hedge — no direction claim in either hedge line."""
    for line in (
        build_rent_gap_line(None, None, 38.0, "Tysons"),
        build_rent_gap_line(None, None, None, "Tysons"),
    ):
        assert "I don't know where your lease lands" in line
        for banned in ("market's moved", "paying above", "began above", "crept closer"):
            assert banned not in line


def test_signed_year_does_not_touch_direct_or_creep_rungs():
    """Rungs 1-4 are unaffected by lease_signed_year."""
    year = THIS_YEAR - 5
    assert build_rent_gap_line(45.0, None, 38.0, "Tysons", lease_signed_year=year) == \
        RUNGS["1_above_building"][3]
    assert build_rent_gap_line(None, 28.5, 38.0, "Tysons", lease_signed_year=year) == \
        RUNGS["3_below_building"][3]


def test_no_hardcoded_pivot_year_remains():
    """RENT_GAP_PIVOT_YEAR is gone from config and '2022' never appears in any
    rung output (all rungs, with and without a signing year)."""
    import app.config as config_module
    assert not hasattr(config_module, "RENT_GAP_PIVOT_YEAR")
    for eff, starting, bldg, _ in RUNGS.values():
        for signed in (None, THIS_YEAR):
            line = build_rent_gap_line(eff, starting, bldg, "Tysons", lease_signed_year=signed)
            assert line is not None
            assert "2022" not in line
            assert "signed before" not in line


def test_no_rent_reference_yields_no_line():
    """Nothing set AND unknown submarket → no line, never an invented number."""
    assert build_rent_gap_line(None, None, None, "Nowheresville") is None
    assert build_rent_gap_line(None, None, None, None) is None
    # Starting alone with no benchmark has no comparator either.
    assert build_rent_gap_line(None, 28.5, None, "Nowheresville") is None


def test_zero_and_negative_values_count_as_unset():
    assert build_rent_gap_line(0.0, -2.0, -1.0, "Tysons") == RUNGS["6_submarket_hedge"][3]


# ── Fake OpenAI client (no network) ─────────────────────────────────────────────

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

# The mock output deliberately contains NONE of the required email lines and NO
# call_script key at all, so any assertion below passes only via the service's
# deterministic guarantees (email post-insert + the code-built CALL SHEET).
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


def _company(effective=None, starting=None, building=None, submarket="Tysons",
             lease_mo=9, signed_year=None):
    return {
        "lease_signed_year": signed_year,
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 120,
        "headcount_growth_pct": 8.0,
        "current_sf_occupied": 20000,
        "current_submarket": submarket,
        "current_building_class": "Class B",
        "lease_expiry_months": lease_mo,
        "lease_expiry_date": None,
        "primary_contact_name": "Jane Smith",
        "primary_contact_title": "COO",
        "tenant_representative": None,
        "current_rent_psf": 40.0,
        "effective_rent_psf": effective,
        "starting_rent_psf": starting,
        "building_asking_rent_psf": building,
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


def _sheet_text(call_script: dict) -> str:
    """All CALL SHEET text a dollar figure could appear in (DATA + ANGLE)."""
    return f"{call_script.get('data', '')}\n{call_script.get('angle', '')}"


_DOLLAR_RE = re.compile(r"\$\d[\d,]*\.\d{2}")


# ── Email ↔ CALL SHEET parity: NUMERIC, not prose (rationale in the module
#    docstring — the call side is a data sheet now, not a recited script) ───────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_numeric_parity_email_vs_call_sheet(rung, monkeypatch):
    """Every dollar figure used in the tenant's email rent line appears on the
    same tenant's CALL SHEET (DATA block and/or ANGLE line)."""
    eff, starting, bldg, expected_line = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    assert expected_line in email_body, (
        f"rung {rung}: rent line missing from email body:\n{email_body}"
    )

    sheet = _sheet_text(result["call_script"])
    email_figures = set(_DOLLAR_RE.findall(expected_line))
    assert email_figures, f"rung {rung}: expected rent line carries no dollar figure"
    for figure in email_figures:
        assert figure in sheet, (
            f"rung {rung}: email rent line cites {figure} but the CALL SHEET "
            f"doesn't carry it:\n{sheet}"
        )


@pytest.mark.parametrize("rung,angle_marker,email_marker", [
    ("1_below_building",  "Below-market lease",         "market's moved since you signed"),
    ("1_above_building",  "Paying above current asking", "paying above the current asking"),
    ("2_below_submarket", "Below-market lease",         "market's moved since you signed"),
    ("2_above_submarket", "Paying above current asking", "paying above the current asking"),
    ("3_below_building",  "Below-market start",         "crept closer to that asking number"),
    ("3_above_building",  "Started above current asking", "began above today's asking number"),
    ("4_below_submarket", "Below-market start",         "crept closer to that asking number"),
    ("4_above_submarket", "Started above current asking", "began above today's asking number"),
    ("5_building_hedge",  "hedge off building asking",   "I don't know where your lease lands"),
    ("6_submarket_hedge", "hedge off Tysons asking",     "I don't know where your lease lands"),
])
def test_angle_matches_email_rung_and_direction(rung, angle_marker, email_marker, monkeypatch):
    """The ANGLE line tells the SAME story (rung + direction) as the email rent
    line for identical input — the call and the email never diverge."""
    eff, starting, bldg, _ = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)
    assert angle_marker in result["call_script"]["angle"]
    assert email_marker in result["email"]["body"]


@pytest.mark.parametrize("rung", RUNGS.keys())
def test_call_sheet_carries_no_prose_framing(rung, monkeypatch):
    """The call side no longer carries prose framing: no email rent line, no
    full-service positioning anywhere on the sheet. DISCOVERY is a checklist."""
    eff, starting, bldg, expected_line = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)
    cs = result["call_script"]
    whole_sheet = "\n".join(
        cs[k] for k in ("opening", "data", "angle", "core_message", "pain_probe", "the_close")
    )
    assert expected_line not in whole_sheet
    assert FULL_SERVICE_LINE not in whole_sheet
    # DISCOVERY: one question per line, bulleted.
    for line in cs["core_message"].splitlines():
        assert line.startswith("• "), f"discovery line not bulleted: {line!r}"
        assert line.rstrip().endswith("?")


@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_timing_positioning_and_close(rung, monkeypatch):
    """Every rung: lease-timing opener + full-service positioning + free
    lease-analysis close in the EMAIL; timing cue + free-analysis close cue on
    the CALL SHEET."""
    eff, starting, bldg, _ = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    cs = result["call_script"]

    assert "9 month" in email_body
    assert FULL_SERVICE_LINE in email_body
    assert FREE_ANALYSIS_LINE in email_body

    assert cs["opening"] == (
        "Jack Zamer, The Commercial Real Estate Group — lease expiring in ~9 months."
    )
    assert cs["the_close"] == FREE_ANALYSIS_LINE  # close references the free lease analysis


def test_model_call_script_output_is_discarded(monkeypatch):
    """The model is no longer asked for a call script; if it returns one anyway,
    the deterministic CALL SHEET replaces it wholesale."""
    _, _, _, expected = RUNGS["3_below_building"]
    noisy = json.dumps({
        "call_script": {
            "opening": "model-invented opening the service must discard",
            "the_hook": "model-invented hook",
            "core_message": f"model-invented questions\n\n{expected}\n\n{FULL_SERVICE_LINE}",
            "pain_probe": "model-invented probe",
            "the_close": "model-invented close",
        },
        "email": {
            "subject": "Your Tysons lease",
            "body": (
                f"Hi Jane,\n\nYour lease is up in about 9 months in Tysons.\n\n{expected}\n\n"
                f"I'd welcome a brief call at your convenience.\n\n{SIGNATURE}"
            ),
        },
    })
    result = _generate(
        _company(starting=28.5, building=38.0), monkeypatch, llm_json=noisy
    )
    cs = result["call_script"]
    assert "model-invented" not in "\n".join(
        cs[k] for k in ("opening", "data", "angle", "core_message", "pain_probe", "the_close")
    )
    assert cs["angle"].startswith("Below-market start")
    # No duplication in the email either.
    assert result["email"]["body"].count(expected) == 1


def test_rent_line_ordered_before_closing_ask_in_email(monkeypatch):
    _, _, _, expected = RUNGS["1_above_building"]
    body = _generate(_company(effective=45.0, building=38.0), monkeypatch)["email"]["body"]
    assert body.index(expected) < body.index("I'd welcome a brief call")
    assert body.index(FREE_ANALYSIS_LINE) < body.index("I'd welcome a brief call")


def test_rent_line_fed_to_model_prompt_but_call_script_not_requested(monkeypatch):
    """The rung line is still instructed verbatim in the GPT-4o EMAIL prompt
    (model stays gpt-4o, called exactly once), but the prompt no longer asks
    for any call-script content — the CALL SHEET is built in code."""
    capture: list = []
    _generate(_company(starting=28.5, building=38.0), monkeypatch, capture=capture)
    assert len(capture) == 1, "GPT-4o must be called exactly once (email only)"
    assert capture[0]["model"] == "gpt-4o"
    system_msg = capture[0]["messages"][0]["content"]
    expected = RUNGS["3_below_building"][3]
    assert expected in system_msg
    assert FULL_SERVICE_LINE in system_msg
    assert FREE_ANALYSIS_LINE in system_msg
    for gone in ("CALL SCRIPT", "THE HOOK", "the_hook", "call_script"):
        assert gone not in system_msg, f"prompt still requests call-script content: {gone}"


def test_generate_known_signed_year_uses_relative_phrasing(monkeypatch):
    """End-to-end regression for the reported bug: a tenant who signed THIS
    year gets email phrasing anchored to that year — never 'signed before
    2022' — and the CALL SHEET carries the same year in DATA."""
    result = _generate(_company(signed_year=THIS_YEAR), monkeypatch)
    expected = _hedge_line(
        TYSONS_ASKING, "Tysons",
        f"even a lease signed as recently as {THIS_YEAR} can land differently "
        f"against today's asking numbers",
    )
    assert expected in result["email"]["body"]
    assert f"Lease Signed Year: {THIS_YEAR}" in result["call_script"]["data"]
    for text in (result["email"]["body"], _sheet_text(result["call_script"])):
        assert "2022" not in text
        assert "signed before" not in text


def test_generate_null_signed_year_uses_vague_phrasing(monkeypatch):
    """No signing year on record → the vague tenure phrasing in the email, and
    'Lease Signed Year: not on file' on the sheet — no year token anywhere in
    the hedge."""
    result = _generate(_company(), monkeypatch)  # rung 6, no signed year
    expected = _hedge_line(TYSONS_ASKING, "Tysons")
    assert expected in result["email"]["body"]
    assert "Lease Signed Year: not on file" in result["call_script"]["data"]
    assert VAGUE_TENURE_CLAUSE not in result["call_script"]["angle"]  # hedge prose is email-only
    assert not re.search(r"\b(19|20)\d{2}\b", result["call_script"]["angle"])


def test_unknown_submarket_with_no_fields_never_500s_and_has_no_rent_line(monkeypatch):
    """No rent fields AND no benchmark → no rent line in the email; the ANGLE
    degrades to the no-data hedge; no invented dollar figure anywhere."""
    result = _generate(_company(submarket="Nowheresville"), monkeypatch)
    email_body = result["email"]["body"]
    cs = result["call_script"]
    assert "Asking rents" not in email_body
    assert cs["angle"] == (
        "No rent data on file — no rent angle; lead with lease timing and discovery."
    )
    assert "$" not in cs["data"]        # every rent row reads "not on file"
    assert "$" not in cs["core_message"]
    assert cs["the_close"] == FREE_ANALYSIS_LINE


def test_expiry_gating_respected_when_months_unknown(monkeypatch):
    """No lease_expiry_months → no fabricated '~X months' opener anywhere."""
    result = _generate(_company(effective=45.0, building=38.0, lease_mo=None), monkeypatch)
    assert "expiring in about" not in result["email"]["body"]
    assert result["call_script"]["opening"] == (
        "Jack Zamer, The Commercial Real Estate Group — lease expiry not on file."
    )
    assert "Lease Expires (months): not on file" in result["call_script"]["data"]
    # The rent line itself is unaffected by the missing expiry.
    assert RUNGS["1_above_building"][3] in result["email"]["body"]


def test_verbatim_model_output_is_not_duplicated(monkeypatch):
    """When the model already includes the exact email lines in their right
    places, nothing gets doubled."""
    eff, starting, bldg, expected = RUNGS["1_above_building"]
    compliant = json.dumps({
        "email": {
            "subject": "Your Tysons lease",
            "body": (
                f"Hi Jane,\n\nYour lease is up in about 9 months in Tysons.\n\n{expected}\n\n"
                f"{FULL_SERVICE_LINE}\n\n{FREE_ANALYSIS_LINE}\n\n"
                f"I'd welcome a brief call at your convenience.\n\n{SIGNATURE}"
            ),
        },
    })
    result = _generate(
        _company(effective=eff, starting=starting, building=bldg),
        monkeypatch, llm_json=compliant,
    )
    assert result["email"]["body"].count(expected) == 1
    assert result["email"]["body"].count(FREE_ANALYSIS_LINE) == 1



# ── Lease Activity import → effective/starting rents on name-matched tenants ──

from app.ingestion.adapters.costar_lease_activity import run_costar_lease_activity_import


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _add_company(db, name, effective=None, starting=None):
    from app.models.company import Company
    c = Company(company_id=f"CO-{db.query(Company).count() + 1:03d}",
                name=name, industry="Technology",
                effective_rent_psf=effective, starting_rent_psf=starting)
    db.add(c)
    db.commit()
    return c


_CSV_HEADER = (
    "Property Address,Rent/SF/Yr,Lease Signed Date,Space Use,"
    "Submarket Name,Tenant Name,Effective Rent (Annual)\n"
)

_CSV_HEADER_BOTH = (
    "Property Address,Rent/SF/Yr,Lease Signed Date,Space Use,"
    "Submarket Name,Tenant Name,Effective Rent (Annual),Starting Rent (Annual)\n"
)


def test_import_sets_effective_rent_on_name_matched_tenant(db_session, capsys):
    c = _add_company(db_session, "Acme Corp, Inc.")
    csv = _CSV_HEADER + (
        '100 Main St,$40.00,01/15/2026,Office,Tysons,"Acme Corp",$33.75\n'
        '200 Oak Ave,$41.00,02/01/2026,Office,Tysons,"Unknown Tenant LLC",$29.00\n'
    )
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)

    db_session.refresh(c)
    # Stored AS-IS — no escalation or vintage adjustment.
    assert c.effective_rent_psf == pytest.approx(33.75)
    assert result["tenants_matched"] == 1
    assert result["tenants_skipped"] == 1
    skip = result["tenant_skips"][0]
    assert skip["tenant_name"] == "Unknown Tenant LLC"
    assert skip["reason"] == "no matching tenant company by name"
    # Summary printed: N matched, N skipped, skipped names.
    out = capsys.readouterr().out
    assert "1 matched" in out and "1 skipped" in out and "Unknown Tenant LLC" in out


def test_import_sets_starting_rent_on_name_matched_tenant(db_session):
    c = _add_company(db_session, "Acme Corp")
    csv = _CSV_HEADER_BOTH + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,$33.75,$28.50\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf == pytest.approx(33.75)
    assert c.starting_rent_psf == pytest.approx(28.50)
    assert result["tenants_matched"] == 1


def test_import_never_overwrites_existing_effective_rent(db_session):
    c = _add_company(db_session, "Acme Corp", effective=28.00)
    csv = _CSV_HEADER + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,$33.75\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf == pytest.approx(28.00)
    assert any(
        s["reason"] == "effective_rent_psf already set — not overwritten"
        for s in result["tenant_skips"]
    )
    # The signing YEAR from the same row is a new datum and is applied.
    assert c.lease_signed_year == 2026


def test_import_never_overwrites_existing_starting_rent(db_session):
    c = _add_company(db_session, "Acme Corp", starting=25.00)
    csv = _CSV_HEADER_BOTH + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,,$28.50\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.starting_rent_psf == pytest.approx(25.00)
    assert c.effective_rent_psf is None  # blank effective cell stays unset
    assert any(
        s["reason"] == "starting_rent_psf already set — not overwritten"
        for s in result["tenant_skips"]
    )


def test_import_sets_lease_signed_year_from_signed_column(db_session):
    """The 'Signed' column name (CoStar variant) parses; the YEAR lands on
    lease_signed_year."""
    c = _add_company(db_session, "Acme Corp")
    csv = (
        "Property Address,Rent/SF/Yr,Signed,Space Use,"
        "Submarket Name,Tenant Name,Effective Rent (Annual)\n"
        '100 Main St,$40.00,03/10/2026,Office,Tysons,Acme Corp,$33.75\n'
    )
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.lease_signed_year == 2026
    assert c.effective_rent_psf == pytest.approx(33.75)
    assert result["tenants_matched"] == 1


def test_import_never_overwrites_existing_lease_signed_year(db_session):
    from app.models.company import Company
    c = _add_company(db_session, "Acme Corp")
    c.lease_signed_year = 2019
    db_session.commit()
    csv = _CSV_HEADER + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,$33.75\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.lease_signed_year == 2019
    assert c.effective_rent_psf == pytest.approx(33.75)
    assert any(
        s["reason"] == "lease_signed_year already set — not overwritten"
        for s in result["tenant_skips"]
    )


def test_import_null_safe_without_signed_column(db_session):
    """No signed-date column at all → rents still apply; year stays null."""
    c = _add_company(db_session, "Acme Corp")
    csv = (
        "Property Address,Rent/SF/Yr,Space Use,"
        "Submarket Name,Tenant Name,Effective Rent (Annual)\n"
        '100 Main St,$40.00,Office,Tysons,Acme Corp,$33.75\n'
    )
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf == pytest.approx(33.75)
    assert c.lease_signed_year is None
    assert result["tenants_matched"] == 1


def test_import_without_rent_columns_is_null_safe(db_session):
    _add_company(db_session, "Acme Corp")
    csv = (
        "Property Address,Rent/SF/Yr,Lease Signed Date,Space Use,Submarket Name\n"
        "100 Main St,$40.00,01/15/2026,Office,Tysons\n"
    )
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    assert result["tenants_matched"] == 0
    assert result["tenant_skips"] == []


def test_import_unparseable_effective_rent_is_skipped_with_reason(db_session):
    c = _add_company(db_session, "Acme Corp")
    csv = _CSV_HEADER + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,Withheld\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf is None
    assert result["tenants_skipped"] == 1
    assert result["tenant_skips"][0]["reason"] == "missing/unparseable Effective Rent (Annual)"


def test_import_latest_signed_lease_wins_for_one_tenant(db_session):
    c = _add_company(db_session, "Acme Corp")
    csv = _CSV_HEADER + (
        '100 Main St,$40.00,01/15/2024,Office,Tysons,Acme Corp,$30.00\n'
        '100 Main St,$40.00,06/15/2026,Office,Tysons,Acme Corp,$35.50\n'
    )
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf == pytest.approx(35.50)
    assert result["tenants_matched"] == 1
