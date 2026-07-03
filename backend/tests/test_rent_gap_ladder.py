"""
Rent-gap ladder — locks all SIX rungs to their expected rent line in the cold
email body and in the call script's THE HOOK beat, plus the Lease Activity
effective/starting-rent import.

Ladder contract (sharpest set field wins: effective > starting > hedge; within
each, building asking > submarket asking):
  (1) effective + building asking       → direct gap claim vs building asking
  (2) effective, no building            → direct gap claim vs submarket asking
  (3) starting + building, no effective → escalation-creep line vs building asking
        (exact wording spec-locked below)
  (4) starting alone (no building)      → escalation-creep line vs submarket asking
  (5) building asking only              → hedge framing off building asking
  (6) nothing set                       → hedge framing off submarket asking

HOOK beat contract: the call script carries a THE HOOK section between OPENING
and CORE MESSAGE holding the rung line + the full-service positioning; CORE
MESSAGE is discovery questions only (no rent stat, no full-service pitch); the
email and the call script select the IDENTICAL rung for identical data.

All inputs are in-memory: the OpenAI call is mocked (and deliberately returns
output WITHOUT the required lines, so these tests prove the deterministic
guarantee, not the mock), the web-intel call is stubbed, and the import tests
use in-memory SQLite. No live DB, network, or CoStar.
"""
import json
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.config import RENT_GAP_PIVOT_YEAR, SUBMARKET_BENCHMARKS
from app.database import Base
import app.services.outreach_service as svc
from app.services.outreach_service import (
    FREE_ANALYSIS_LINE,
    FULL_SERVICE_LINE,
    build_rent_gap_line,
)


TYSONS_ASKING = SUBMARKET_BENCHMARKS["Tysons"]["market_rent_psf"]  # 39.10


# ── Expected rent lines ─────────────────────────────────────────────────────────

def _direct_line(eff: float, ask: float, where: str) -> str:
    gap = abs(eff - ask)
    return (
        f"You're at ${eff:.2f}/SF effective against ${ask:.2f}/SF asking in {where} "
        f"right now — that's a ${gap:.2f}/SF gap on your lease, and it's exactly the "
        f"number to pin down before you renew."
    )


def _creep_line(starting: float, ask: float, quoted_by: str) -> str:
    return (
        f"I see your lease started at ${starting:.2f}/SF, and {quoted_by} quoting "
        f"${ask:.2f} right now. By the nature of things — annual escalations, "
        f"operating expense pass-throughs — your rent's likely crept closer to that "
        f"asking number by now, and that's exactly the kind of thing that's easy to "
        f"miss until the decision to renew or relocate is already on top of you. "
        f"I'd like to dig in and show you exactly where you stand."
    )


def _hedge_line(ask: float, where: str) -> str:
    return (
        f"Asking rents in {where} are quoting around ${ask:.2f}/SF right now — a lot of "
        f"tenants who signed before {RENT_GAP_PIVOT_YEAR} are sitting above that. I don't "
        f"know where your lease lands, but that gap is exactly what I check."
    )


RUNGS = {
    # rung: (effective, starting, building, expected line)
    "1_effective_vs_building":  (45.0, None, 38.0, _direct_line(45.0, 38.0, "your building")),
    "2_effective_vs_submarket": (45.0, None, None, _direct_line(45.0, TYSONS_ASKING, "Tysons")),
    "3_starting_vs_building":   (None, 28.5, 38.0, _creep_line(28.5, 38.0, "your building's")),
    "4_starting_vs_submarket":  (None, 28.5, None, _creep_line(28.5, TYSONS_ASKING, "Tysons is")),
    "5_building_hedge":         (None, None, 38.0, _hedge_line(38.0, "your building")),
    "6_submarket_hedge":        (None, None, None, _hedge_line(TYSONS_ASKING, "Tysons")),
}


# ── Unit: the pure ladder function ──────────────────────────────────────────────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_build_rent_gap_line_rungs(rung):
    eff, starting, bldg, expected = RUNGS[rung]
    assert build_rent_gap_line(eff, starting, bldg, "Tysons") == expected


def test_rung_3_exact_spec_wording():
    """Rung 3's wording is spec-locked — asserted against the literal text, not
    the helper, so a drive-by rewrite of the line fails here."""
    line = build_rent_gap_line(None, 28.5, 38.0, "Tysons")
    assert line == (
        "I see your lease started at $28.50/SF, and your building's quoting $38.00 "
        "right now. By the nature of things — annual escalations, operating expense "
        "pass-throughs — your rent's likely crept closer to that asking number by now, "
        "and that's exactly the kind of thing that's easy to miss until the decision "
        "to renew or relocate is already on top of you. I'd like to dig in and show "
        "you exactly where you stand."
    )


def test_effective_wins_outright_over_starting():
    """All three fields set → rung 1 (effective vs building), never the creep line."""
    line = build_rent_gap_line(45.0, 28.5, 38.0, "Tysons")
    assert line == RUNGS["1_effective_vs_building"][3]
    assert "started at" not in line


def test_starting_beats_hedge():
    # starting + building → rung 3, not the building hedge (rung 5)
    assert build_rent_gap_line(None, 28.5, 38.0, "Tysons") == RUNGS["3_starting_vs_building"][3]
    # starting alone → rung 4, not the submarket hedge (rung 6)
    assert build_rent_gap_line(None, 28.5, None, "Tysons") == RUNGS["4_starting_vs_submarket"][3]


def test_hedge_rungs_cite_config_pivot_year():
    assert str(RENT_GAP_PIVOT_YEAR) in build_rent_gap_line(None, None, 38.0, "Tysons")
    assert str(RENT_GAP_PIVOT_YEAR) in build_rent_gap_line(None, None, None, "Tysons")
    # Direct and creep rungs make their claim without the pivot-year hedge.
    assert str(RENT_GAP_PIVOT_YEAR) not in build_rent_gap_line(45.0, None, 38.0, "Tysons")
    assert str(RENT_GAP_PIVOT_YEAR) not in build_rent_gap_line(None, 28.5, 38.0, "Tysons")


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

_CORE_QUESTIONS = "How is your current space fitting the team?"

# The mock output deliberately contains NONE of the required ladder lines (and
# no the_hook key at all), so any assertion below passes only via the service's
# deterministic guarantee.
_BARE_LLM_JSON = json.dumps({
    "call_script": {
        "opening": "Hi Jane — do you have a couple of minutes for a few quick questions?",
        "core_message": _CORE_QUESTIONS,
        "pain_probe": "What's the single biggest real-estate headache on your plate right now?",
        "the_close": "Thanks for the time.",
    },
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


def _company(effective=None, starting=None, building=None, submarket="Tysons", lease_mo=9):
    return {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 120,
        "headcount_growth_pct": 8.0,
        "current_sf_occupied": 20000,
        "current_submarket": submarket,
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


# ── The six rungs: email + THE HOOK parity ──────────────────────────────────────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_parity_email_and_hook_carry_identical_line(rung, monkeypatch):
    """Identical data → email body and call script select the identical rung."""
    eff, starting, bldg, expected_line = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    the_hook   = result["call_script"]["the_hook"]
    assert expected_line in email_body, (
        f"rung {rung}: rent line missing from email body:\n{email_body}"
    )
    assert expected_line in the_hook, (
        f"rung {rung}: rent line missing from THE HOOK:\n{the_hook}"
    )
    # THE HOOK is exactly the rung line + the full-service positioning.
    assert the_hook == f"{expected_line} {FULL_SERVICE_LINE}"


@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_core_message_has_no_rent_or_full_service(rung, monkeypatch):
    """Positioning lives ONLY in THE HOOK — CORE MESSAGE is discovery questions."""
    eff, starting, bldg, expected_line = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)
    core = result["call_script"]["core_message"]
    assert expected_line not in core
    assert FULL_SERVICE_LINE not in core
    assert _CORE_QUESTIONS in core  # the discovery content survives untouched


@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_timing_positioning_and_close(rung, monkeypatch):
    """Every rung: lease-timing opener, full-service positioning (hook + email),
    free lease-analysis close in both formats."""
    eff, starting, bldg, _ = RUNGS[rung]
    result = _generate(_company(effective=eff, starting=starting, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    cs = result["call_script"]

    assert "9 month" in email_body
    assert cs["opening"].startswith("I know your lease is expiring in about 9 months")
    assert FULL_SERVICE_LINE in email_body
    assert FULL_SERVICE_LINE in cs["the_hook"]
    assert FREE_ANALYSIS_LINE in email_body
    assert FREE_ANALYSIS_LINE in cs["the_close"]


def test_core_message_scrubbed_when_model_echoes_positioning(monkeypatch):
    """A model that stuffs the rent line + full-service pitch into CORE MESSAGE
    gets scrubbed — the strings end up only in THE HOOK."""
    _, _, _, expected = RUNGS["3_starting_vs_building"]
    noisy = json.dumps({
        "call_script": {
            "opening": "Hi Jane — quick call.",
            "the_hook": "wrong hook text the service must overwrite",
            "core_message": f"{_CORE_QUESTIONS}\n\n{expected}\n\n{FULL_SERVICE_LINE}",
            "pain_probe": "Biggest headache?",
            "the_close": "Thanks.",
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
    assert cs["the_hook"] == f"{expected} {FULL_SERVICE_LINE}"
    assert expected not in cs["core_message"]
    assert FULL_SERVICE_LINE not in cs["core_message"]
    assert _CORE_QUESTIONS in cs["core_message"]
    # No duplication in the email either.
    assert result["email"]["body"].count(expected) == 1


def test_rent_line_ordered_before_closing_ask_in_email(monkeypatch):
    _, _, _, expected = RUNGS["1_effective_vs_building"]
    body = _generate(_company(effective=45.0, building=38.0), monkeypatch)["email"]["body"]
    assert body.index(expected) < body.index("I'd welcome a brief call")
    assert body.index(FREE_ANALYSIS_LINE) < body.index("I'd welcome a brief call")


def test_hook_and_rent_line_fed_to_model_prompt(monkeypatch):
    """The rung line + hook are also instructed verbatim in the GPT-4o prompt
    (model stays gpt-4o), so real generations weave them rather than relying on
    the post-LLM guarantee alone."""
    capture: list = []
    _generate(_company(starting=28.5, building=38.0), monkeypatch, capture=capture)
    assert capture, "OpenAI client was never called"
    assert capture[0]["model"] == "gpt-4o"
    system_msg = capture[0]["messages"][0]["content"]
    expected = RUNGS["3_starting_vs_building"][3]
    assert expected in system_msg
    assert "THE HOOK" in system_msg
    assert FULL_SERVICE_LINE in system_msg
    assert FREE_ANALYSIS_LINE in system_msg


def test_unknown_submarket_with_no_fields_never_500s_and_has_no_rent_line(monkeypatch):
    """Rung 6 with no benchmark → no rent line at all; THE HOOK degrades to the
    full-service positioning alone; no invented dollar figure anywhere."""
    result = _generate(
        _company(submarket="Nowheresville"), monkeypatch
    )
    email_body = result["email"]["body"]
    cs = result["call_script"]
    assert "Asking rents" not in email_body
    assert cs["the_hook"] == FULL_SERVICE_LINE
    assert "$" not in cs["core_message"]
    assert FREE_ANALYSIS_LINE in cs["the_close"]


def test_expiry_gating_respected_when_months_unknown(monkeypatch):
    """No lease_expiry_months → no fabricated '~X months' opener anywhere."""
    result = _generate(_company(effective=45.0, building=38.0, lease_mo=None), monkeypatch)
    assert "expiring in about" not in result["email"]["body"]
    assert "expiring in about" not in result["call_script"]["opening"]
    # The rent line itself is unaffected by the missing expiry.
    assert RUNGS["1_effective_vs_building"][3] in result["email"]["body"]


def test_verbatim_model_output_is_not_duplicated(monkeypatch):
    """When the model already includes the exact lines in their right places,
    nothing gets doubled."""
    eff, starting, bldg, expected = RUNGS["1_effective_vs_building"]
    compliant = json.dumps({
        "call_script": {
            "opening": "I know your lease is expiring in about 9 months, so I'll keep this quick.",
            "the_hook": f"{expected} {FULL_SERVICE_LINE}",
            "core_message": _CORE_QUESTIONS,
            "pain_probe": "Biggest headache?",
            "the_close": f"{FREE_ANALYSIS_LINE}",
        },
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
    assert result["call_script"]["the_hook"].count(expected) == 1
    assert result["call_script"]["the_close"].count(FREE_ANALYSIS_LINE) == 1


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
    assert result["tenants_matched"] == 0
    assert any("not overwritten" in s["reason"] for s in result["tenant_skips"])


def test_import_never_overwrites_existing_starting_rent(db_session):
    c = _add_company(db_session, "Acme Corp", starting=25.00)
    csv = _CSV_HEADER_BOTH + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,,$28.50\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.starting_rent_psf == pytest.approx(25.00)
    assert c.effective_rent_psf is None  # blank effective cell stays unset
    assert result["tenants_matched"] == 0
    assert any(
        s["reason"] == "starting_rent_psf already set — not overwritten"
        for s in result["tenant_skips"]
    )


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
