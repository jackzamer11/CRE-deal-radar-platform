"""Ingestion recognises Jack by his exact addresses, never by domain.

Jack's own addresses are exactly jzamer@z-reg.com, jzamer@simpsondev.com,
jackzamer1@gmail.com and jfzamer@wm.edu. Ingestion used to treat every
simpsondev.com address as Jack's, so Ann Waller, Karl Acorda and Fred Zamer
(FZamer@simpsondev.com) resolved to nobody and their mail — the brokerage's
leasing notes, availabilities and renewal figures — landed unattached.

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
from app.models.contact import Contact
from app.models.email_ingest import ContactAddressOverride
from app.services.email_ingest_service import (
    record_address_override, resolve_contact_for_address,
    resolve_contacts_for_addresses,
)

JACKS_ADDRESSES = [
    "jzamer@z-reg.com", "jzamer@simpsondev.com",
    "jackzamer1@gmail.com", "jfzamer@wm.edu",
]


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


def _post(client, **payload):
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Colleagues are contacts ─────────────────────────────────────────────────

def test_ann_waller_at_simpsondev_resolves_to_a_contact(db_session, client):
    body = _post(
        client,
        from_email="awaller@simpsondev.com",
        from_name="Ann Waller",
        to_recipients=[{"email": "jzamer@z-reg.com", "name": "Jack Zamer"}],
        direction="inbound",
        subject="Updated Leasing Notes",
        source_message_id="<ann-1@mail>",
        action_taken="Ann Waller sent the September 15 leasing notes.",
    )

    ann = db_session.query(Contact).filter(
        Contact.email == "awaller@simpsondev.com"
    ).one()
    assert ann.name == "Ann Waller"
    assert body["contact_id"] == ann.id
    assert "awaller@simpsondev.com" not in body["skipped_own_addresses"]
    assert body["skipped_own_addresses"] == ["jzamer@z-reg.com"]


def test_colleagues_on_the_to_and_cc_lines_become_contacts(db_session, client):
    body = _post(
        client,
        from_email="awaller@simpsondev.com",
        from_name="Ann Waller",
        to_recipients=[
            {"email": "kacorda@simpsondev.com", "name": "Karl Acorda"},
            {"email": "jzamer@simpsondev.com", "name": "Jack Zamer"},
        ],
        cc_recipients=[{"email": "FZamer@simpsondev.com", "name": "Fred Zamer"}],
        direction="inbound",
        source_message_id="<ann-2@mail>",
        action_taken="Ann Waller confirmed the 2111 Eisenhower lobby meeting.",
    )

    emails = {c.email for c in db_session.query(Contact).all()}
    assert emails == {
        "awaller@simpsondev.com", "kacorda@simpsondev.com", "fzamer@simpsondev.com",
    }
    assert len(body["participant_entry_ids"]) == 1       # Karl, written to
    assert len(body["participation_entry_ids"]) == 1     # Fred, copied
    assert body["skipped_own_addresses"] == ["jzamer@simpsondev.com"]


def test_fred_at_simpsondev_resolves_to_his_existing_contact_by_alias(db_session, client):
    fred = Contact(name="Fred Zamer", contact_type="tenant", stage="Sent")
    db_session.add(fred)
    db_session.flush()
    db_session.add(ContactAddressOverride(
        email="fzamer@simpsondev.com", contact_id=fred.id, is_primary=False,
    ))
    db_session.commit()

    body = _post(
        client,
        from_email="FZamer@simpsondev.com",
        from_name="Fred Zamer",
        direction="inbound",
        source_message_id="<fred-1@mail>",
        action_taken="Fred Zamer asked for the license expiration sheet.",
    )

    assert body["contact_id"] == fred.id
    assert db_session.query(Contact).count() == 1


def test_a_deal_can_name_a_colleague_as_its_contact(db_session, client):
    body = _post(
        client,
        from_email="awaller@simpsondev.com",
        from_name="Ann Waller",
        direction="inbound",
        source_message_id="<ann-3@mail>",
        deals=[
            {"company_override": "Scott Management",
             "contact_email": "kacorda@simpsondev.com", "contact_name": "Karl Acorda",
             "action_taken": "Karl Acorda is showing the prepared 5,405 square feet."},
            {"company_override": "Reico",
             "action_taken": "Reico is moving to 2850 Eisenhower Avenue."},
        ],
    )

    karl = db_session.query(Contact).filter(
        Contact.email == "kacorda@simpsondev.com"
    ).one()
    assert body["entries"][0]["contact_id"] == karl.id
    assert body["entries"][0]["contact_email_ignored"] is None


# ── Jack's own addresses never do ───────────────────────────────────────────

def test_jzamer_at_simpsondev_never_resolves_to_a_contact(db_session, client):
    body = _post(
        client,
        from_email="jzamer@simpsondev.com",
        from_name="Jack Zamer",
        to_recipients=[{"email": "jzamer@z-reg.com"}],
        cc_recipients=[{"email": "JZamer@SimpsonDev.com"}],
        direction="outbound",
        source_message_id="<jack-1@mail>",
        action_taken="Note to self.",
    )

    assert body["contact_id"] is None
    assert sorted(body["skipped_own_addresses"]) == [
        "jzamer@simpsondev.com", "jzamer@z-reg.com",
    ]
    assert db_session.query(Contact).count() == 0
    assert db_session.query(ActivityLog).count() == 1    # the entry still logs


@pytest.mark.parametrize("address", JACKS_ADDRESSES)
def test_none_of_jacks_addresses_resolve_create_or_teach(db_session, address):
    contact, created = resolve_contact_for_address(db_session, address, "Jack Zamer")
    assert contact is None and created is False

    assert resolve_contacts_for_addresses(db_session, [(address, "Jack Zamer")]) == {}

    someone = Contact(name="Someone", contact_type="tenant", stage="Sent")
    db_session.add(someone)
    db_session.flush()
    assert record_address_override(db_session, address, someone) is None
    assert db_session.query(ContactAddressOverride).count() == 0
    assert db_session.query(Contact).count() == 1


def test_a_deal_naming_jzamer_at_simpsondev_falls_back_to_the_sender(db_session, client):
    body = _post(
        client,
        from_email="awaller@simpsondev.com",
        from_name="Ann Waller",
        direction="inbound",
        source_message_id="<ann-4@mail>",
        deals=[{"company_override": "Scott Management",
                "contact_email": "jzamer@simpsondev.com", "contact_name": "Jack Zamer",
                "action_taken": "Scott renewal."}],
    )

    ann = db_session.query(Contact).filter(
        Contact.email == "awaller@simpsondev.com"
    ).one()
    assert body["entries"][0]["contact_id"] == ann.id
    assert body["entries"][0]["contact_email_ignored"] == "jzamer@simpsondev.com"
    assert db_session.query(Contact).filter(
        Contact.email == "jzamer@simpsondev.com"
    ).count() == 0
