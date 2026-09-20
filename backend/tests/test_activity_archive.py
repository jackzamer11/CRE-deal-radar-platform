"""The archived flag — noise out of the queue, not out of the database.

The Needs a Contact queue exists to be drained to zero. A hundred manual-log
entries that will never have a counterparty ("called Hina, no answer, no
voicemail left") put zero permanently out of reach, and a number that can never
be reached stops meaning anything — so the surface gets abandoned. Archiving is
the release valve, and its whole contract is how NARROW it is:

  - an archived entry leaves the queue and the badge count
  - it stays in the database and stays searchable
  - it stays on its company's card, which still counts it
  - it stays in All Activity
  - unarchive puts it back, fully

In-memory SQLite, dependency-overridden get_db. No live DB file, no network.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.main import app


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


def _company(db, name="Scott Management", business_id="CO-001", **kw):
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _entry(db, **kw):
    kw.setdefault("action_type", "NOTE")
    kw.setdefault("action_taken", "called Hina, no answer, no voicemail left")
    kw.setdefault("log_date", date.today())
    e = ActivityLog(**kw)
    db.add(e); db.commit(); db.refresh(e)
    return e


def _count(client):
    return client.get("/api/activity/needs-contact/count").json()["total"]


def _queue(client, **params):
    r = client.get("/api/activity/needs-contact", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _archive(client, entry_id, archived=True):
    r = client.patch(f"/api/activity/{entry_id}/archived", json={"archived": archived})
    assert r.status_code == 200, r.text
    return r.json()


# ── Leaving the queue ────────────────────────────────────────────────────────

def test_an_archived_entry_leaves_the_queue_and_the_count(client, db_session):
    co = _company(db_session)
    noise = _entry(db_session, company_stamp_id=co.id)
    real = _entry(db_session, company_stamp_id=co.id, action_taken="Real lead")

    assert _count(client) == 2
    assert _archive(client, noise.id)["archived"] is True

    assert _count(client) == 1
    page = _queue(client)
    assert page["total"] == 1
    assert [e["id"] for e in page["entries"]] == [real.id]


def test_entries_default_to_unarchived(client, db_session):
    """Nothing is archived until Jack says so — the correct backfill for every
    row that pre-dates the column."""
    e = _entry(db_session)
    db_session.refresh(e)
    assert bool(e.archived) is False
    assert _queue(client)["total"] == 1


def test_the_show_archived_filter_brings_them_back(client, db_session):
    co = _company(db_session)
    noise = _entry(db_session, company_stamp_id=co.id)
    real = _entry(db_session, company_stamp_id=co.id, action_taken="Real lead")
    _archive(client, noise.id)

    shown = _queue(client, include_archived=True)
    assert shown["total"] == 2
    assert sorted(e["id"] for e in shown["entries"]) == sorted([noise.id, real.id])
    flags = {e["id"]: e["archived"] for e in shown["entries"]}
    assert flags[noise.id] is True and flags[real.id] is False

    # The badge is unmoved by the toggle: it is "how much is left to do".
    assert _count(client) == 1


# ── Staying everywhere else ──────────────────────────────────────────────────

def test_an_archived_entry_stays_searchable(client, db_session):
    _entry(db_session, action_taken="called Hina, no answer, no voicemail left")
    e = db_session.query(ActivityLog).first()
    _archive(client, e.id)

    r = client.get("/api/activity/", params={"q": "Hina"})
    assert r.status_code == 200, r.text
    found = [row for row in r.json() if row["id"] == e.id]
    assert len(found) == 1, "archiving must not remove an entry from search"
    assert found[0]["archived"] is True


def test_an_archived_entry_stays_in_all_activity(client, db_session):
    e = _entry(db_session)
    _archive(client, e.id)
    r = client.get("/api/activity/", params={"limit": 100})
    assert e.id in [row["id"] for row in r.json()]


def test_an_archived_entry_stays_on_its_company_card(client, db_session):
    """Archiving says "keep this out of my queue", never "this company is no
    longer holding it"."""
    co = _company(db_session)
    a = _entry(db_session, company_stamp_id=co.id)
    _entry(db_session, company_stamp_id=co.id, action_taken="Real lead")
    _archive(client, a.id)

    cards = client.get("/api/contacts/company-cards").json()
    assert len(cards) == 1, "the card must not disappear"
    card = cards[0]
    assert card["entry_count"] == 2, "the card counts archived entries too"
    assert card["archived_count"] == 1, "and says how many"


def test_a_card_whose_every_entry_is_archived_still_exists(client, db_session):
    co = _company(db_session)
    a = _entry(db_session, company_stamp_id=co.id)
    _archive(client, a.id)

    cards = client.get("/api/contacts/company-cards").json()
    assert [(c["entry_count"], c["archived_count"]) for c in cards] == [(1, 1)]
    # ...while the queue and badge are empty.
    assert _count(client) == 0
    assert _queue(client)["total"] == 0


def test_opening_a_card_shows_its_archived_entries_too(client, db_session):
    """The panel must never show fewer than the card counted."""
    co = _company(db_session)
    a = _entry(db_session, company_stamp_id=co.id)
    b = _entry(db_session, company_stamp_id=co.id, action_taken="Real lead")
    _archive(client, a.id)

    page = _queue(client, company_id=co.id, include_archived=True)
    assert page["total"] == 2
    assert sorted(e["id"] for e in page["entries"]) == sorted([a.id, b.id])


# ── Coming back ──────────────────────────────────────────────────────────────

def test_unarchive_restores_the_entry_fully(client, db_session):
    co = _company(db_session)
    e = _entry(db_session, company_stamp_id=co.id)

    _archive(client, e.id)
    assert _count(client) == 0

    assert _archive(client, e.id, archived=False)["archived"] is False
    assert _count(client) == 1
    page = _queue(client)
    assert [row["id"] for row in page["entries"]] == [e.id]
    assert page["entries"][0]["archived"] is False
    db_session.refresh(e)
    assert bool(e.archived) is False


def test_archiving_is_idempotent(client, db_session):
    e = _entry(db_session)
    _archive(client, e.id)
    _archive(client, e.id)
    assert _count(client) == 0
    _archive(client, e.id, archived=False)
    _archive(client, e.id, archived=False)
    assert _count(client) == 1


def test_archiving_an_unknown_entry_404s(client, db_session):
    r = client.patch("/api/activity/9999/archived", json={"archived": True})
    assert r.status_code == 404


# ── Nothing else moved ───────────────────────────────────────────────────────

def test_archiving_does_not_touch_the_contact_or_the_stamp(client, db_session):
    co = _company(db_session)
    e = _entry(db_session, company_stamp_id=co.id)
    _archive(client, e.id)
    db_session.refresh(e)
    assert e.contact_id is None
    assert e.company_stamp_id == co.id


def test_an_entry_on_a_contact_can_be_archived_without_leaving_its_thread(
    client, db_session,
):
    """The flag is general. Archiving one does not detach it from anybody —
    the queue never contained it in the first place."""
    co = _company(db_session)
    person = Contact(name="Dana Reed", company_id=co.id, contact_type="tenant",
                     stage="Sent", triaged=True)
    db_session.add(person); db_session.commit(); db_session.refresh(person)
    e = _entry(db_session, company_stamp_id=co.id, contact_id=person.id)

    _archive(client, e.id)
    db_session.refresh(e)
    assert e.contact_id == person.id

    r = client.get(f"/api/contacts/{person.id}/timeline")
    assert r.status_code == 200, r.text
    assert e.id in [row["id"] for row in r.json()["entries"]]


def test_archiving_a_roundup_deal_entry_leaves_it_deal_sourced(client, db_session):
    """The 9 roundup entries are a separate mechanic; archiving must not blur
    them into ordinary entries."""
    co = _company(db_session)
    e = _entry(db_session, company_stamp_id=co.id, deal_sourced=True,
               source_note="From Ann Waller's leasing notes.")
    _archive(client, e.id)
    db_session.refresh(e)
    assert bool(e.deal_sourced) is True
    assert e.source_note == "From Ann Waller's leasing notes."
