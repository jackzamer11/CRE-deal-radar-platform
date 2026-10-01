"""Outreach uses what the tenant said — under guardrails.

Tenant outreach now draws on the tenant's own statements and, for a tenant Jack
has a confirmed lease for, that lease's size and expiry. Like the privacy tests,
these assert what never even REACHES the email model. What this file locks:

  - the email prompt gets this tenant's current statements; older ones arrive
    as questions; the call sheet gets everything, budget included
  - a budget figure never reaches the email model (rent-figure parity rule)
  - another company's facts, a broker's market talk and a requirement still
    waiting for an owner never come back for this tenant
  - lease facts: only this company's current, confirmed lease; size and
    expiry month only, never the address; never once the recipient has left
  - a draft containing a figure from another tenant's lease is refused

In-memory SQLite; the OpenAI call is faked and the prompt captured. No network.
"""
import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.lease import Lease
from app.models.observation import Observation
from app.services import outreach_service as svc
from app.services.outreach_facts import outreach_context

TODAY = date.today()
ADDRESS = "4400 Distinctive Lease Ave"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _company(db, name, **kw):
    n = db.query(Company).count() + 1
    c = Company(company_id=f"CO-{n:03d}", name=name, industry="Medical",
                current_submarket="Merrifield", current_sf_occupied=2400,
                primary_contact_name="Maria Chen", **kw)
    db.add(c)
    db.commit()
    return c


def _note(db, company, days_ago=10, contact=None, **facts):
    log = ActivityLog(log_date=TODAY - timedelta(days=days_ago), action_type="CALL",
                      action_taken="call", contact_id=contact.id if contact else None,
                      company_stamp_id=company.id)
    db.add(log)
    db.commit()
    about = facts.pop("_about", None)
    for field, value in facts.items():
        db.add(Observation(entity_type="company", entity_id=company.id, field=field, value=value,
                           source_doc=f"activity_log:{log.id}", human_verified=True,
                           verified_by="auto", about=about))
    db.commit()
    return log


@pytest.fixture()
def halverson(db):
    co = _company(db, "Halverson Dental")
    maria = Contact(name="Maria Chen", company_id=co.id, contact_type="tenant", stage="Sent")
    db.add(maria)
    db.commit()
    _note(db, co, contact=maria, req_sf_min="3000", req_sf_max="3500",
          req_budget_max_psf="38", req_access_needs="ground floor")
    _note(db, co, days_ago=500, req_must_haves="two operatories")
    # Things that must never come back for Halverson:
    other = _company(db, "Bright Smiles")
    _note(db, other, req_sf_min="9999", req_budget_max_psf="61")
    _note(db, co, req_sf_max="777", _about="unassigned")       # waiting for an owner
    _note(db, co, mkt_asking_rent="$44/SF", _about="market")   # broker market talk
    return co, maria, other


# ══ 1. What reaches the model ═════════════════════════════════════════════════

def test_this_tenants_statements_come_back_split_for_email_and_call_sheet(db, halverson):
    co, _, _ = halverson
    ctx = outreach_context(db, co)
    assert ("min SF", "3000") in ctx["stated_requirements"]
    assert ("access", "ground floor") in ctx["stated_requirements"]
    assert ("must-haves", "two operatories") in ctx["reconfirm_requirements"]
    assert ("budget", "38") in ctx["call_sheet_stated"]
    everything = json.dumps(ctx)
    for leak in ("9999", "61", "777", "$44"):
        assert leak not in everything


def _capture(company: dict, monkeypatch, body="Hi Maria,\n\nThanks.") -> tuple[dict, str]:
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured["prompt"] = " ".join(m["content"] for m in kwargs["messages"])
            content = json.dumps({"email": {"subject": "Your Merrifield lease", "body": body}})
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": content})()})()]})()

    class _OpenAI:
        def __init__(self, *a, **k):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_web_search_company_intel", lambda name: "")
    with patch("openai.OpenAI", _OpenAI):
        result = svc.generate_outreach(company)
    return result, captured["prompt"]


def _company_dict(db, co):
    d = {
        "name": co.name, "industry": co.industry, "current_submarket": co.current_submarket,
        "current_sf_occupied": co.current_sf_occupied, "lease_expiry_months": 8,
        "primary_contact_name": co.primary_contact_name, "opportunity_score": 80,
        "priority": "HIGH",
    }
    d.update(outreach_context(db, co))
    return d


def test_the_email_prompt_has_their_words_but_never_the_budget(db, halverson, monkeypatch):
    co, _, _ = halverson
    result, prompt = _capture(_company_dict(db, co), monkeypatch)
    assert "WHAT THE TENANT HAS TOLD JACK" in prompt
    assert "min SF: 3000" in prompt
    assert "SAID OVER A YEAR AGO" in prompt and "two operatories" in prompt
    assert "38" not in prompt.split("WHAT THE TENANT HAS TOLD JACK")[1].split("TENANT DATA")[0]
    # The call sheet carries the budget for Jack on the call.
    assert "Tenant said — budget: 38" in result["call_script"]["data"]


# ══ 2. Lease facts — this tenant's own lease only ═════════════════════════════

def _lease(db, company, sf, *, confirmed=True):
    db.add(Lease(company_id=company.id, is_current=True, rentable_sf=sf,
                 premises_address=ADDRESS, suite="Suite 210",
                 expiration_date=date(2034, 5, 31),
                 confirmed_at=datetime(2027, 4, 1) if confirmed else None))
    db.commit()


def test_their_own_confirmed_lease_reaches_the_prompt_without_the_address(db, halverson, monkeypatch):
    co, _, _ = halverson
    _lease(db, co, 3200)
    _, prompt = _capture(_company_dict(db, co), monkeypatch)
    assert "Size: 3,200 SF" in prompt and "Expires: May 2034" in prompt
    assert ADDRESS not in prompt and "Suite 210" not in prompt


def test_an_unconfirmed_lease_and_another_companys_lease_never_do(db, halverson, monkeypatch):
    co, _, other = halverson
    _lease(db, co, 3200, confirmed=False)
    _lease(db, other, 8800)
    _, prompt = _capture(_company_dict(db, co), monkeypatch)
    assert "THEIR CURRENT LEASE" not in prompt
    assert "8,800" not in prompt and "8800" not in prompt


def test_once_the_recipient_has_left_the_lease_stays_out(db, halverson, monkeypatch):
    co, maria, other = halverson
    _lease(db, co, 3200)
    maria.former_company_id, maria.company_id = co.id, other.id
    maria.left_company_on = TODAY
    db.commit()
    _, prompt = _capture(_company_dict(db, co), monkeypatch)
    assert "THEIR CURRENT LEASE" not in prompt


@pytest.mark.parametrize("text", [
    "your 3,200 square feet", "3200 SF suite", "about 2,400 RSF", "a 1,500 sq ft office",
])
def test_a_square_footage_is_not_scrubbed_as_a_street_address(text):
    assert svc._strip_street_address(text) == text


@pytest.mark.parametrize("text", [
    "space at 1234 Wilson Blvd", "4400 Distinctive Lease Ave", "8300 Greensboro Dr Suite 800",
])
def test_a_street_address_is_still_scrubbed(text):
    cleaned = svc._strip_street_address(text)
    assert not any(token in cleaned for token in ("Blvd", "Ave", "Dr"))


def test_a_draft_quoting_another_tenants_lease_is_refused(db, halverson, monkeypatch):
    co, _, other = halverson
    _lease(db, co, 3200)
    _lease(db, other, 8800)
    with pytest.raises(svc.OutreachBlocked):
        _capture(_company_dict(db, co), monkeypatch,
                 body="Hi Maria,\n\nYour team's 8,800 square feet...")
    # Their own size is fine.
    result, _ = _capture(_company_dict(db, co), monkeypatch,
                         body="Hi Maria,\n\nYour team's 3,200 square feet...")
    assert "3,200" in result["email"]["body"]
