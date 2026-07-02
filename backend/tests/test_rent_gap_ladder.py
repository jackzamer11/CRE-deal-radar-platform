"""
Rent-gap ladder — locks all four rungs to their expected rent line in BOTH the
cold email body and the phone call script (Fix 4), plus the Lease Activity
effective-rent import (Fix 2).

Ladder contract (sharpest set field wins):
  (a) effective + building asking both set → direct gap claim vs building asking
  (b) effective set, building null         → direct gap claim vs submarket asking
  (c) effective null, building set         → hedge framing off building asking
  (d) both null                            → hedge framing off submarket asking

Every rung, both formats: lease-timing opener (~X months, respecting the
existing expiry gating), full-service positioning (TI allowance, base-year
op-ex, taxes, escalations), and a free lease-analysis close.

All inputs are in-memory: the OpenAI call is mocked (and deliberately returns a
body/script WITHOUT the required lines, so these tests prove the deterministic
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


# ── Expected rent lines, built exactly as the service builds them ──────────────

def _direct_line(eff: float, ask: float, where: str) -> str:
    gap = abs(eff - ask)
    return (
        f"You're at ${eff:.2f}/SF effective against ${ask:.2f}/SF asking in {where} "
        f"right now — that's a ${gap:.2f}/SF gap on your lease, and it's exactly the "
        f"number to pin down before you renew."
    )


def _hedge_line(ask: float, where: str) -> str:
    return (
        f"Asking rents in {where} are quoting around ${ask:.2f}/SF right now — a lot of "
        f"tenants who signed before {RENT_GAP_PIVOT_YEAR} are sitting above that. I don't "
        f"know where your lease lands, but that gap is exactly what I check."
    )


RUNGS = {
    # rung: (effective, building, expected line)
    "a_direct_vs_building":  (45.0, 38.0, _direct_line(45.0, 38.0, "your building")),
    "b_direct_vs_submarket": (45.0, None, _direct_line(45.0, TYSONS_ASKING, "Tysons")),
    "c_hedge_vs_building":   (None, 38.0, _hedge_line(38.0, "your building")),
    "d_hedge_vs_submarket":  (None, None, _hedge_line(TYSONS_ASKING, "Tysons")),
}


# ── Unit: the pure ladder function ──────────────────────────────────────────────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_build_rent_gap_line_rungs(rung):
    eff, bldg, expected = RUNGS[rung]
    assert build_rent_gap_line(eff, bldg, "Tysons") == expected


def test_rung_a_wins_over_submarket():
    """Both fields set → the building asking rent is used, not the submarket's."""
    line = build_rent_gap_line(45.0, 38.0, "Tysons")
    assert "your building" in line
    assert f"${TYSONS_ASKING:.2f}" not in line


def test_hedge_rungs_cite_config_pivot_year():
    assert str(RENT_GAP_PIVOT_YEAR) in build_rent_gap_line(None, 38.0, "Tysons")
    assert str(RENT_GAP_PIVOT_YEAR) in build_rent_gap_line(None, None, "Tysons")
    # Direct rungs make the gap claim without the pivot-year hedge.
    assert str(RENT_GAP_PIVOT_YEAR) not in build_rent_gap_line(45.0, 38.0, "Tysons")


def test_no_rent_reference_yields_no_line():
    """Both fields null AND unknown submarket → no line, never an invented number."""
    assert build_rent_gap_line(None, None, "Nowheresville") is None
    assert build_rent_gap_line(None, None, None) is None


def test_zero_and_negative_values_count_as_unset():
    assert build_rent_gap_line(0.0, -1.0, "Tysons") == RUNGS["d_hedge_vs_submarket"][2]


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

# The mock output deliberately contains NONE of the required ladder lines, so
# any assertion below passes only via the service's deterministic injection.
_BARE_LLM_JSON = json.dumps({
    "call_script": {
        "opening": "Hi Jane — do you have a couple of minutes for a few quick questions?",
        "core_message": "How is your current space fitting the team?",
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


def _company(effective=None, building=None, submarket="Tysons", lease_mo=9):
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
        "building_asking_rent_psf": building,
        "future_move_flag": False,
        "future_move_type": None,
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


def _generate(company, monkeypatch, capture=None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    capture = capture if capture is not None else []
    def _factory(*args, **kwargs):
        return _FakeOpenAI(_BARE_LLM_JSON, capture)
    with patch("openai.OpenAI", _factory):
        return svc.generate_outreach(company)


# ── The four rungs land in BOTH the email body and the call script ─────────────

@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_rent_line_in_email_and_call_script(rung, monkeypatch):
    eff, bldg, expected_line = RUNGS[rung]
    result = _generate(_company(effective=eff, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    call_core  = result["call_script"]["core_message"]
    assert expected_line in email_body, (
        f"rung {rung}: rent line missing from email body:\n{email_body}"
    )
    assert expected_line in call_core, (
        f"rung {rung}: rent line missing from call script core:\n{call_core}"
    )


@pytest.mark.parametrize("rung", RUNGS.keys())
def test_rung_timing_positioning_and_close_in_both_formats(rung, monkeypatch):
    """Every rung, both formats: lease-timing opener, full-service positioning,
    free lease-analysis close."""
    eff, bldg, _ = RUNGS[rung]
    result = _generate(_company(effective=eff, building=bldg), monkeypatch)

    email_body = result["email"]["body"]
    cs = result["call_script"]

    # Lease-timing opener (~9 months) in both formats.
    assert "9 month" in email_body
    assert cs["opening"].startswith(
        "I know your lease is expiring in about 9 months"
    )
    # Full-service positioning in both formats.
    assert FULL_SERVICE_LINE in email_body
    assert FULL_SERVICE_LINE in cs["core_message"]
    # Free lease-analysis close in both formats.
    assert FREE_ANALYSIS_LINE in email_body
    assert FREE_ANALYSIS_LINE in cs["the_close"]


def test_rent_line_ordered_before_closing_ask_in_email(monkeypatch):
    _, _, expected = RUNGS["a_direct_vs_building"]
    body = _generate(_company(effective=45.0, building=38.0), monkeypatch)["email"]["body"]
    assert body.index(expected) < body.index("I'd welcome a brief call")
    assert body.index(FREE_ANALYSIS_LINE) < body.index("I'd welcome a brief call")


def test_rent_line_fed_to_model_prompt(monkeypatch):
    """The ladder line is also instructed verbatim in the GPT-4o prompt (model
    stays gpt-4o), so real generations weave it rather than relying on the
    post-LLM injection alone."""
    capture: list = []
    _generate(_company(effective=45.0, building=38.0), monkeypatch, capture=capture)
    assert capture, "OpenAI client was never called"
    assert capture[0]["model"] == "gpt-4o"
    system_msg = capture[0]["messages"][0]["content"]
    assert RUNGS["a_direct_vs_building"][2] in system_msg
    assert FULL_SERVICE_LINE in system_msg
    assert FREE_ANALYSIS_LINE in system_msg


def test_unknown_submarket_with_no_fields_never_500s_and_has_no_rent_line(monkeypatch):
    """Rung (d) with no benchmark → no rent line at all, but the draft still
    carries positioning + free analysis and no invented dollar figure."""
    result = _generate(
        _company(effective=None, building=None, submarket="Nowheresville"), monkeypatch
    )
    email_body = result["email"]["body"]
    assert "Asking rents" not in email_body
    assert "$" not in result["call_script"]["core_message"]
    assert FULL_SERVICE_LINE in email_body
    assert FREE_ANALYSIS_LINE in result["call_script"]["the_close"]


def test_expiry_gating_respected_when_months_unknown(monkeypatch):
    """No lease_expiry_months → no fabricated '~X months' opener anywhere."""
    result = _generate(_company(effective=45.0, building=38.0, lease_mo=None), monkeypatch)
    assert "expiring in about" not in result["email"]["body"]
    assert "expiring in about" not in result["call_script"]["opening"]
    # The rent line itself is unaffected by the missing expiry.
    assert RUNGS["a_direct_vs_building"][2] in result["email"]["body"]


def test_verbatim_model_output_is_not_duplicated(monkeypatch):
    """When the model already includes the exact lines, injection must not
    double them."""
    eff, bldg, expected = RUNGS["a_direct_vs_building"]
    compliant = json.dumps({
        "call_script": {
            "opening": "I know your lease is expiring in about 9 months, so I'll keep this quick.",
            "core_message": f"How is the space fitting?\n\n{expected}\n\n{FULL_SERVICE_LINE}",
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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    def _factory(*args, **kwargs):
        return _FakeOpenAI(compliant, [])
    with patch("openai.OpenAI", _factory):
        result = svc.generate_outreach(_company(effective=eff, building=bldg))
    assert result["email"]["body"].count(expected) == 1
    assert result["email"]["body"].count(FREE_ANALYSIS_LINE) == 1
    assert result["call_script"]["core_message"].count(expected) == 1


# ── Fix 2: Lease Activity import → effective_rent_psf on name-matched tenants ──

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


def _add_company(db, name, effective=None):
    from app.models.company import Company
    c = Company(company_id=f"CO-{db.query(Company).count() + 1:03d}",
                name=name, industry="Technology",
                effective_rent_psf=effective)
    db.add(c)
    db.commit()
    return c


_CSV_HEADER = (
    "Property Address,Rent/SF/Yr,Lease Signed Date,Space Use,"
    "Submarket Name,Tenant Name,Effective Rent (Annual)\n"
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


def test_import_never_overwrites_existing_effective_rent(db_session):
    c = _add_company(db_session, "Acme Corp", effective=28.00)
    csv = _CSV_HEADER + '100 Main St,$40.00,01/15/2026,Office,Tysons,Acme Corp,$33.75\n'
    result = run_costar_lease_activity_import(csv.encode(), "leases.csv", db_session)
    db_session.refresh(c)
    assert c.effective_rent_psf == pytest.approx(28.00)
    assert result["tenants_matched"] == 0
    assert any("not overwritten" in s["reason"] for s in result["tenant_skips"])


def test_import_without_effective_rent_columns_is_null_safe(db_session):
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
