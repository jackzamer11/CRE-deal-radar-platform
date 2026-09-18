"""Two narrow guards, each one a case that used to fail quietly.

  - A colleague's address at a domain Jack owns can become a contact's alias.
    FZamer@simpsondev.com is Fred; jzamer@simpsondev.com is Jack, and is still
    refused. Ingestion keeps the wider domain test unchanged — no colleague is
    ever resolved AS Jack — but Jack deliberately naming an alias is a
    different question from an ingested email being attributed.
  - contact_email / contact_name at the email level are refused, not dropped —
    with deals present and without. ActivityFromEmail did not declare them, so
    Pydantic discarded them and the entry landed on the sender with a 200. The
    same field inside a deal still works — that is where it belongs.

In-memory SQLite, dependency-overridden get_db. No live database, no network,
no OpenAI or Anthropic calls.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers every table on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.email_ingest import ContactAddressOverride


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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _create_contact(client, name, email):
    resp = client.post("/api/contacts/", json={"name": name, "email": email})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══ 1. The alias guard reads the literal addresses only ════════════════════════

def test_a_colleague_at_an_owned_domain_can_be_an_alias(db_session, client):
    """The case the alias feature exists for — Fred's work inbox."""
    fred = _create_contact(client, "Fred Zamer", "fredzamer@cs.com")

    resp = client.post(
        f"/api/contacts/{fred['id']}/addresses",
        json={"email": "FZamer@simpsondev.com"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "fzamer@simpsondev.com"
    assert resp.json()["is_primary"] is False


def test_jacks_own_address_at_that_domain_is_still_refused(db_session, client):
    fred = _create_contact(client, "Fred Zamer", "fredzamer@cs.com")
    before = db_session.query(ContactAddressOverride).count()

    resp = client.post(
        f"/api/contacts/{fred['id']}/addresses",
        json={"email": "jzamer@simpsondev.com"},
    )
    assert resp.status_code == 400
    assert db_session.query(ContactAddressOverride).count() == before


# ══ 2. Per-deal contact fields on the single-entry path ════════════════════════

def test_contact_name_on_a_plain_email_is_refused_and_writes_nothing(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "ann@avisonyoung.com",
        "from_name": "Ann Waller",
        "direction": "inbound",
        "source_message_id": "<msg-stray-contact@mail>",
        "action_taken": "Ann sent her leasing notes.",
        "contact_name": "Mike Johnson",
    })
    assert resp.status_code == 400
    assert "contact_name" in resp.json()["detail"]
    assert db_session.query(ActivityLog).count() == 0


def test_contact_name_beside_deals_is_refused_too(db_session, client):
    """The other half of the same mistake: with deals present a top-level
    contact_name has no deal to belong to, and was dropped just as quietly."""
    resp = client.post("/api/activity/from-email", json={
        "from_email": "ann@avisonyoung.com",
        "from_name": "Ann Waller",
        "direction": "inbound",
        "source_message_id": "<msg-stray-beside-deals@mail>",
        "contact_name": "Mike Johnson",
        "deals": [{
            "action_taken": "Brinks renewal.",
            "company_override": "Brinks",
        }],
    })
    assert resp.status_code == 400
    assert "contact_name" in resp.json()["detail"]
    assert db_session.query(ActivityLog).count() == 0


def test_the_same_field_inside_a_deal_still_works(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "ann@avisonyoung.com",
        "from_name": "Ann Waller",
        "direction": "inbound",
        "source_message_id": "<msg-deal-contact@mail>",
        "deals": [{
            "action_taken": "Brinks renewal — Mike is the decision maker.",
            "company_override": "Brinks",
            "contact_name": "Mike Johnson",
        }],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entries"][0]["contact_name"] == "Mike Johnson"
