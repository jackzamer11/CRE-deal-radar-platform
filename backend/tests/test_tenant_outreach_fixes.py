"""
Tenant Outreach modal fixes — backend contract tests.

Covers the two backend-verifiable guarantees of the Tenant Outreach changes:

  Fix 1 (email): the generated tenant email introduces the broker exactly once
    (only in the signature block — no injected "My name is Jack Zamer…" intro and
    no duplicated "I work exclusively in NoVA…" social-proof line), and never
    shows a property street address (submarket + class only).

  Fix 3 (recipient → Activity Log): the recipient captured on the modal persists
    and appears in the Activity Log payload exactly the way the Daily Briefing
    modal writes it — embedded in the "Emailed <Name> re <Company>" action line and
    surfaced back as `contact_name`. Empty recipient never 500s.

All inputs are in-memory: the OpenAI call is mocked, the web-intel call is
stubbed, and the Activity Log uses in-memory SQLite. No live DB, network, or CoStar.
"""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.api.routes.activity import parse_contact_name
from app.main import app
import app.services.outreach_service as svc


# ── Fake OpenAI client (no network) ───────────────────────────────────────────

class _FakeMessage:
    def __init__(self, content): self.content = content


class _FakeChoice:
    def __init__(self, content): self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content): self._content = content
    def create(self, **kwargs): return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content): self.completions = _FakeCompletions(content)


class _FakeOpenAI:
    def __init__(self, content): self.chat = _FakeChat(content)


def _openai_factory(content):
    """Return a drop-in replacement for `openai.OpenAI(...)`."""
    def _factory(*args, **kwargs):
        return _FakeOpenAI(content)
    return _factory


SIGNATURE = "Thank you,\n\nJack Zamer\nVice President, The Commercial Real Estate Group\n571-205-6228"


def _llm_json(body: str) -> str:
    return json.dumps({
        "call_script": {
            "opening": "Hi Jane — do you have a couple of minutes for a few quick questions?",
            "core_message": (
                "How is your current space fitting the team? What are you paying in rent now? "
                "How's your growth trajectory, floor-plan needs, and parking count? In-office or "
                "hybrid? Have you started looking yet, and who else weighs in on the decision?"
            ),
            "pain_probe": "What's the single biggest real-estate headache on your plate right now?",
            "the_close": "I'd be glad to pull together a free, no-obligation market read on your options.",
        },
        "email": {
            "subject": "Your Tysons lease and the market window",
            "body": body,
        },
    })


def _company():
    return {
        "name": "Acme Corp",
        "industry": "Technology",
        "current_headcount": 120,
        "headcount_growth_pct": 8.0,
        "current_sf_occupied": 20000,
        "current_submarket": "Tysons",
        "lease_expiry_months": 9,
        "lease_expiry_date": None,
        "primary_contact_name": "Jane Smith",
        "primary_contact_title": "COO",
        "tenant_representative": None,
        "current_rent_psf": 40.0,
        "future_move_flag": False,
        "future_move_type": None,
        "lease_trajectory": "AUTO",
        "contraction_signal": False,
        "opportunity_score": 80,
        "priority": "HIGH",
    }


def _generate(body: str, monkeypatch) -> dict:
    """Run generate_outreach with the OpenAI call + web intel fully stubbed."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    with patch("openai.OpenAI", _openai_factory(_llm_json(body))):
        return svc.generate_outreach(_company())


# ── Fix 1: broker name appears exactly once; no street address ────────────────

def test_tenant_email_broker_name_appears_exactly_once(monkeypatch):
    body = (
        "Hi Jane,\n\n"
        "Your lease is up in about 9 months in Tysons, and a renewal-or-relocate decision is "
        "best driven early. Right now landlords are competing hard with concessions. What's the "
        "biggest pressure on your space today?\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        f"{SIGNATURE}"
    )
    result = _generate(body, monkeypatch)
    email_body = result["email"]["body"]
    assert email_body.count("Jack Zamer") == 1, (
        f"broker name must appear exactly once (signature only); body:\n{email_body}"
    )


def test_tenant_email_no_injected_intro_or_social_proof(monkeypatch):
    """Regression: the old post-LLM injection added a second broker intro and a
    duplicate 'I work exclusively in the Northern Virginia office market…' line —
    the source of the double intro and triple 'exclusively NoVA'. Neither is injected now."""
    body = (
        "Hi Jane,\n\n"
        "Your lease is up in about 9 months in Tysons. The market window favors tenants right now.\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        f"{SIGNATURE}"
    )
    result = _generate(body, monkeypatch)
    email_body = result["email"]["body"]
    assert "My name is Jack Zamer" not in email_body
    assert "I work exclusively in the Northern Virginia office market" not in email_body


def test_tenant_email_strips_street_address(monkeypatch):
    """Tenant side never shows a property street address (submarket + class only)."""
    body = (
        "Hi Jane,\n\n"
        "Your lease is up in about 9 months in Tysons. There is strong space at 1750 Tysons Boulevard "
        "and 100 Test Ave worth a look.\n\n"
        "I'd welcome a brief call at your convenience.\n\n"
        f"{SIGNATURE}"
    )
    result = _generate(body, monkeypatch)
    email_body = result["email"]["body"]
    assert "Test Ave" not in email_body
    assert "Tysons Boulevard" not in email_body
    # Submarket reference (no street number) is preserved.
    assert "Tysons" in email_body


def test_tenant_email_generation_null_safe_on_missing_growth_and_headcount(monkeypatch):
    """Missing growth_rate / headcount must not 500 the generator (scale-proofing)."""
    company = _company()
    company["headcount_growth_pct"] = None
    company["current_headcount"] = None
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    body = f"Hi Jane,\n\nYour lease is up soon in Tysons.\n\n{SIGNATURE}"
    with patch("openai.OpenAI", _openai_factory(_llm_json(body))):
        result = svc.generate_outreach(company)
    assert result["email"]["body"].count("Jack Zamer") == 1


# ── Fix 3: recipient persists into the Activity Log payload ───────────────────

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


def test_recipient_persists_and_appears_in_activity_payload(client):
    """The recipient captured on the Tenant Outreach modal flows into the Activity
    Log exactly like the Daily Briefing recipient — written into the action line and
    surfaced back as `contact_name`."""
    resp = client.post("/api/activity/", json={
        "action_type": "EMAIL",
        "action_taken": "Emailed Jane Smith re Acme Corp",
        "outcome": "Outreach sent: Your Tysons lease and the market window to Jane Smith",
        "outreach_type": "tenant",
        "target_type": "tenant",
        "contact_method": "email",
        "subject": "Your Tysons lease and the market window",
    })
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # Recipient persisted in the stored action line …
    assert "Jane Smith" in payload["action_taken"]
    # … and surfaced back as the parsed contact on the Activity Log payload.
    assert payload["contact_name"] == "Jane Smith"


def test_empty_recipient_is_null_safe(client):
    """An empty recipient ('Emailed contact') must persist without 500 and read
    back with no contact_name (the generic placeholder is not a real recipient)."""
    resp = client.post("/api/activity/", json={
        "action_type": "EMAIL",
        "action_taken": "Emailed contact",
        "outreach_type": "tenant",
        "target_type": "tenant",
        "contact_method": "email",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["contact_name"] is None


def test_parse_contact_name_round_trips_recipient():
    """Unit guard on the parse the Activity Log uses to expose the recipient."""
    assert parse_contact_name("Emailed Jane Smith re Acme Corp") == "Jane Smith"
    assert parse_contact_name("Called Dana Lee") == "Dana Lee"
    assert parse_contact_name("Emailed contact") is None
