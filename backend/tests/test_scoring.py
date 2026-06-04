"""
Scoring-engine contract tests.

These lock the tenant lease-expiry scoring curve and the derived `late_stage`
flag so a future edit cannot silently shift either. All inputs are fabricated
in-memory — pure-function boundary pins plus a small set of TestClient cases
for the /api/companies/ agent contract. No network, no Claude.

Guards:
  - sig_lease_expiry_proximity pinned at EVERY tier boundary.
  - The curve peaks in the middle (7mo > 2mo AND 7mo > 15mo).
  - late_stage: True for 1/2/3 months, False for 0/4/None.
  - /api/companies/ still exposes the 7 agent-contract fields + late_stage.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.company import Company
from app.services.signal_engine import sig_lease_expiry_proximity, compute_expiry_priority_override


# ── In-memory DB fixtures ──────────────────────────────────────────────────────
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


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ── Curve boundary pins ────────────────────────────────────────────────────────
# Every tier boundary, so a future reweight that shifts a step is caught.
@pytest.mark.parametrize("months,expected", [
    (None, None),   # abstain — unknown expiry never votes
    (-1,   25.0),   # already expired
    (0,    25.0),   # already expired (boundary)
    (1,    35.0),   # 1-3 mo tier
    (3,    35.0),   # 1-3 mo tier (upper boundary)
    (4,    70.0),   # 4-5 mo tier
    (5,    70.0),   # 4-5 mo tier (upper boundary)
    (6,    100.0),  # 6-9 mo PEAK (lower boundary)
    (7,    100.0),  # peak
    (9,    100.0),  # 6-9 mo peak (upper boundary)
    (10,   80.0),   # 10-12 mo tier
    (12,   80.0),   # 10-12 mo tier (upper boundary)
    (13,   55.0),   # 13-18 mo tier
    (18,   55.0),   # 13-18 mo tier (upper boundary)
    (19,   32.0),   # 19-24 mo tier
    (24,   32.0),   # 19-24 mo tier (upper boundary)
    (25,   15.0),   # 25-36 mo tier
    (36,   15.0),   # 25-36 mo tier (upper boundary)
    (37,   0.0),    # beyond horizon
])
def test_lease_expiry_curve_boundaries(months, expected):
    assert sig_lease_expiry_proximity(months) == expected


def test_lease_expiry_none_is_null_safe():
    # Explicit: None must abstain (return None), never raise.
    assert sig_lease_expiry_proximity(None) is None


def test_lease_expiry_curve_peaks_in_the_middle():
    """The strategic intent: a mid-window tenant (7mo) outscores both an
    already-negotiating tenant (2mo) and a too-early one (15mo)."""
    peak = sig_lease_expiry_proximity(7)
    early_negotiation = sig_lease_expiry_proximity(2)
    too_early = sig_lease_expiry_proximity(15)
    assert peak > early_negotiation
    assert peak > too_early


# ── late_stage derivation (via schema serialization) ───────────────────────────
def _serialize_company(lease_expiry_months):
    # Build the response schema directly (its own field defaults apply) so the
    # `late_stage` validator runs on a fully-formed CompanyListOut.
    from app.schemas.company import CompanyListOut
    return CompanyListOut(
        id=1,
        company_id="CO-LS",
        name="LateStage Co",
        industry="Technology",
        current_submarket="Tysons",
        opportunity_score=50.0,
        priority="WORKABLE",
        lease_expiry_months=lease_expiry_months,
    )


@pytest.mark.parametrize("months,expected", [
    (1, True),
    (2, True),
    (3, True),
    (0, False),     # expired is not "late stage"
    (4, False),     # outside the 1-3 window
    (None, False),  # null-safe — never an error
])
def test_late_stage_flag(months, expected):
    out = _serialize_company(months)
    assert out.late_stage is expected


# ── /api/companies/ agent contract + late_stage additive field ─────────────────
def test_companies_list_contract_includes_late_stage(client, db_session):
    db_session.add(Company(
        company_id="CO-AGENT",
        name="Agent Co",
        industry="Technology",
        current_headcount=50,
        current_submarket="Tysons",
        opportunity_score=0.0,
        priority="IGNORE",
        lease_expiry_months=2,   # within late-stage window
    ))
    db_session.commit()

    resp = client.get("/api/companies/")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]

    # The 7 fields outreach_agent.py depends on must remain present.
    for field in (
        "company_id", "priority", "current_headcount", "headcount_growth_pct",
        "lease_expiry_months", "current_submarket", "opportunity_score",
    ):
        assert field in row, f"/api/companies/ must still expose {field}"

    # New additive field must appear and reflect the 2-month tenant.
    assert "late_stage" in row
    assert row["late_stage"] is True

    # expiry_priority_override must also appear (False — 2-month tenant is not in 3-12 window).
    assert "expiry_priority_override" in row
    assert row["expiry_priority_override"] is False


# ── compute_expiry_priority_override boundary pins ─────────────────────────────
@pytest.mark.parametrize("insufficient_data,months,expected", [
    # Override requires insufficient_data=True AND 3 <= months <= 12.
    (True,  3,    True),    # lower boundary of peak window
    (True,  6,    True),    # mid window
    (True,  12,   True),    # upper boundary
    (True,  2,    False),   # below floor — tenant already in late-stage negotiations
    (True,  13,   False),   # above ceiling — too far out, missing signals matter
    (True,  None, False),   # null-safe: no expiry data → no override
    (False, 6,    False),   # sufficient data — override is inapplicable
    (False, None, False),   # sufficient data + null expiry → False
])
def test_expiry_priority_override_boundaries(insufficient_data, months, expected):
    assert compute_expiry_priority_override(insufficient_data, months) is expected


def test_expiry_priority_override_null_safe():
    # Explicit: None months with insufficient_data=True must never raise.
    result = compute_expiry_priority_override(True, None)
    assert result is False


# ── Agent skip contract ────────────────────────────────────────────────────────
def test_agent_skip_passes_expiry_override(client, db_session):
    """A thin-data company with expiry_priority_override=True must survive the skip filter."""
    db_session.add(Company(
        company_id="CO-OVERRIDE",
        name="Override Co",
        industry="Technology",
        current_submarket="Tysons",
        lease_expiry_months=6,
        opportunity_score=0.0,
        priority="IGNORE",
        insufficient_data=True,
        expiry_priority_override=True,
    ))
    db_session.commit()

    resp = client.get("/api/companies/")
    assert resp.status_code == 200
    rows = resp.json()
    ids = [r["company_id"] for r in rows]
    assert "CO-OVERRIDE" in ids, "Override company must appear in /api/companies/ response"
    override_row = next(r for r in rows if r["company_id"] == "CO-OVERRIDE")
    assert override_row["expiry_priority_override"] is True


def test_agent_skip_blocks_thin_data_no_override(client, db_session):
    """A thin-data company without override must still be suppressed at the API level.

    Note: /api/companies/ is a listing endpoint, not the agent filter — the agent
    filter lives in outreach_agent.py.  This test asserts the field is correctly
    False so the agent can apply the skip logic correctly.
    """
    db_session.add(Company(
        company_id="CO-NOOVERRIDE",
        name="No Override Co",
        industry="Technology",
        current_submarket="Tysons",
        lease_expiry_months=24,     # beyond the 12-month ceiling
        opportunity_score=0.0,
        priority="IGNORE",
        insufficient_data=True,
        expiry_priority_override=False,
    ))
    db_session.commit()

    resp = client.get("/api/companies/")
    assert resp.status_code == 200
    rows = resp.json()
    target = next((r for r in rows if r["company_id"] == "CO-NOOVERRIDE"), None)
    assert target is not None, "Company must appear in listing regardless of override"
    assert target["expiry_priority_override"] is False
