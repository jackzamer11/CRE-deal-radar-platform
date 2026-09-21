"""Current status — Jack's own line about where a person stands.

The contact card otherwise shows the latest entry's summary, which is what
HAPPENED. That is often not where things STAND, and after a long thread the
newest entry is frequently the least informative line available. So a status,
when set, takes that slot; when empty the card falls back and reads exactly as
it did before.

What this file locks:

  - a status set shows on the card, alongside (not instead of) the fallback,
    so the frontend can make the swap
  - an empty status falls back to the latest entry summary
  - writing a status creates NO activity entry and leaves stage, stage date,
    last touch and entry count untouched

In-memory SQLite, dependency-overridden get_db. No live DB file, no network.
"""
from datetime import date, timedelta

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


def _company(db, name="Vienna Clinic", business_id="CO-001"):
    c = Company(company_id=business_id, name=name, industry="Medical")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _contact(db, name="Dana Reed", **kw):
    kw.setdefault("contact_type", "tenant")
    kw.setdefault("stage", "Sent")
    kw.setdefault("triaged", True)
    c = Contact(name=name, **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _entry(db, contact, company, when=None, text="Emailed the flyer"):
    e = ActivityLog(
        contact_id=contact.id, company_stamp_id=company.id,
        action_type="EMAIL", action_taken=text,
        log_date=when or date.today(), channel="email",
    )
    db.add(e); db.commit(); db.refresh(e)
    return e


def _row(client, contact_id):
    r = client.get("/api/contacts/")
    assert r.status_code == 200, r.text
    hits = [x for x in r.json() if x["id"] == contact_id]
    assert len(hits) == 1
    return hits[0]


def _set_status(client, contact_id, value):
    r = client.patch(f"/api/contacts/{contact_id}/status",
                     json={"current_status": value})
    assert r.status_code == 200, r.text
    return r.json()


# ── Showing on the card ──────────────────────────────────────────────────────

def test_a_status_set_shows_on_the_card(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _entry(db_session, person, co)

    _set_status(client, person.id, "Waiting on their board, call back after the 15th")

    row = _row(client, person.id)
    assert row["current_status"] == "Waiting on their board, call back after the 15th"
    assert row["current_status_updated_at"] == date.today().isoformat()
    # The fallback is still sent — the swap is the card's to make, and a card
    # handed a blanked field could not render it.
    assert row["latest_entry_summary"] == "Emailed the flyer"


def test_an_empty_status_falls_back_to_the_latest_entry_summary(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _entry(db_session, person, co, text="Emailed the flyer")

    row = _row(client, person.id)
    assert row["current_status"] is None
    assert row["current_status_updated_at"] is None
    assert row["latest_entry_summary"] == "Emailed the flyer"


def test_clearing_a_status_restores_the_fallback(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _entry(db_session, person, co, text="Emailed the flyer")
    _set_status(client, person.id, "Waiting on their board")
    assert _row(client, person.id)["current_status"] == "Waiting on their board"

    for empty in ("", "   ", None):
        _set_status(client, person.id, empty)
        row = _row(client, person.id)
        assert row["current_status"] is None, f"{empty!r} should clear the status"
        # The date goes with it — a stale date attached to nothing would read
        # as a status that is merely not displayed.
        assert row["current_status_updated_at"] is None
        assert row["latest_entry_summary"] == "Emailed the flyer"
        _set_status(client, person.id, "Waiting on their board")


def test_a_status_survives_with_no_activity_at_all(client, db_session):
    """A contact with no entries has no fallback; the status is the only line."""
    person = _contact(db_session)
    _set_status(client, person.id, "Cold lead, revisit in spring")
    row = _row(client, person.id)
    assert row["current_status"] == "Cold lead, revisit in spring"
    assert row["latest_entry_summary"] is None
    assert row["entry_count"] == 0


def test_the_status_reaches_the_contact_panel(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _set_status(client, person.id, "Lease signed, waiting on the countersign")

    r = client.get(f"/api/contacts/{person.id}")
    assert r.status_code == 200, r.text
    assert r.json()["contact"]["current_status"] == "Lease signed, waiting on the countersign"


def test_whitespace_is_stripped(client, db_session):
    person = _contact(db_session)
    assert _set_status(client, person.id, "  spaced out  ")["current_status"] == "spaced out"


def test_status_on_an_unknown_contact_404s(client, db_session):
    r = client.patch("/api/contacts/9999/status", json={"current_status": "x"})
    assert r.status_code == 404


# ── Writing one is not an interaction ────────────────────────────────────────

def test_editing_status_leaves_stage_last_touch_and_count_unchanged(
    client, db_session,
):
    """The load-bearing test. A status is Jack's summary of where things
    stand, not a thing that happened."""
    co = _company(db_session)
    stage_date = date.today() - timedelta(days=9)
    person = _contact(
        db_session, company_id=co.id, stage="Interested",
        stage_changed_at=stage_date,
    )
    touch = date.today() - timedelta(days=4)
    _entry(db_session, person, co, when=touch, text="Emailed the flyer")

    before = _row(client, person.id)
    entries_before = db_session.query(ActivityLog).count()

    _set_status(client, person.id, "Waiting on their board")

    after = _row(client, person.id)
    assert after["stage"] == before["stage"] == "Interested"
    assert after["stage_changed_at"] == stage_date.isoformat()
    assert after["days_in_stage"] == before["days_in_stage"]
    assert after["latest_entry_date"] == before["latest_entry_date"] == touch.isoformat()
    assert after["entry_count"] == before["entry_count"] == 1
    assert after["next_touch_date"] == before["next_touch_date"]

    # No entry was written — not a real one, and not a stage-change divider.
    assert db_session.query(ActivityLog).count() == entries_before
    db_session.refresh(person)
    assert person.stage == "Interested"
    assert person.stage_changed_at == stage_date


def test_setting_a_status_writes_no_stage_change_divider(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _set_status(client, person.id, "Waiting on their board")

    dividers = (
        db_session.query(ActivityLog)
        .filter(ActivityLog.action_type == "STAGE_CHANGE")
        .all()
    )
    assert dividers == []

    r = client.get(f"/api/contacts/{person.id}/timeline")
    assert r.status_code == 200, r.text
    assert r.json()["entries"] == []


def test_resaving_the_same_line_does_not_bump_the_date(client, db_session):
    """A stale status must never read as freshly confirmed."""
    person = _contact(db_session)
    _set_status(client, person.id, "Waiting on their board")

    yesterday = date.today() - timedelta(days=1)
    db_session.refresh(person)
    person.current_status_updated_at = yesterday
    db_session.commit()

    _set_status(client, person.id, "Waiting on their board")
    db_session.refresh(person)
    assert person.current_status_updated_at == yesterday

    # A genuine change does move it.
    _set_status(client, person.id, "Board approved, drafting the LOI")
    db_session.refresh(person)
    assert person.current_status_updated_at == date.today()


def test_setting_a_status_triages_the_contact(client, db_session):
    """Jack typing a status is Jack working this record. Triage is not stage."""
    person = _contact(db_session, triaged=False)
    _set_status(client, person.id, "Waiting on their board")
    db_session.refresh(person)
    assert person.triaged is True
    assert person.stage == "Sent"


# ── Nothing else moved ───────────────────────────────────────────────────────

def test_status_does_not_reach_the_needs_a_contact_queue(client, db_session):
    """The queue is about entries with no person. A person's status is not it."""
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    held = ActivityLog(
        company_stamp_id=co.id, action_type="NOTE",
        action_taken="Voicemail to the main line", log_date=date.today(),
    )
    db_session.add(held); db_session.commit()

    before = client.get("/api/activity/needs-contact/count").json()["total"]
    _set_status(client, person.id, "Waiting on their board")
    assert client.get("/api/activity/needs-contact/count").json()["total"] == before


def test_status_does_not_reach_company_cards(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    held = ActivityLog(
        company_stamp_id=co.id, action_type="NOTE",
        action_taken="Voicemail to the main line", log_date=date.today(),
    )
    db_session.add(held); db_session.commit()

    before = client.get("/api/contacts/company-cards").json()
    _set_status(client, person.id, "Waiting on their board")
    assert client.get("/api/contacts/company-cards").json() == before


def test_a_plain_contact_patch_still_cannot_set_status(client, db_session):
    """One writer. PATCH /contacts/{id} shares a path with the stage-change
    writer, which is exactly why status does not go through it."""
    person = _contact(db_session)
    r = client.patch(f"/api/contacts/{person.id}",
                     json={"current_status": "sneaked in", "title": "VP"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "VP"
    db_session.refresh(person)
    assert person.current_status is None
