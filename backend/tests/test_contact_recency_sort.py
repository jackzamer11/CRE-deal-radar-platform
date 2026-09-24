"""By Contact list ordering — most recent activity first.

The By Contact list answers one question: what just happened? So the card at
the top is the record something landed on most recently, whatever put it there
— the 7pm email task, a call Jack logged by hand, or an entry he moved off a
company card onto a person. Everything this file locks follows from that:

  - a new entry moves its contact to the top
  - an entry MOVED onto a contact re-sorts that contact the same way, because
    an entry arriving from a company card is an entry arriving
  - same-day entries break on entry id, newest first — a date alone cannot
    order two things logged on the same afternoon
  - a contact with no entries still appears, at the bottom, where the
    next-touch ordering still governs (it is the only signal left there)
  - company cards sort into the SAME list by their newest contactless entry,
    rather than sitting in a group pinned above it
  - stage and status edits do not re-sort: neither is a thing that happened
    with the person, and a card that jumped on a dropdown change would make
    the list's one promise false

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


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _company(db, name="Scott Management", business_id="CO-001", **kw):
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact(db, name="Joe Tenant", **kw):
    kw.setdefault("contact_type", "tenant")
    kw.setdefault("stage", "Sent")
    kw.setdefault("triaged", True)
    c = Contact(name=name, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _entry(db, **kw):
    kw.setdefault("action_type", "EMAIL")
    kw.setdefault("action_taken", "Logged something")
    kw.setdefault("log_date", date.today())
    log = ActivityLog(**kw)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _names(client, **params):
    r = client.get("/api/contacts/", params=params or None)
    assert r.status_code == 200, r.text
    return [row["name"] for row in r.json()]


TODAY = date.today()


# ══ 1. A new entry moves its contact to the top ═══════════════════════════════

def test_a_new_entry_moves_its_contact_to_the_top(db_session, client):
    stale = _contact(db_session, name="Stale")
    quiet = _contact(db_session, name="Quiet")
    _entry(db_session, contact_id=stale.id, log_date=TODAY - timedelta(days=1))
    _entry(db_session, contact_id=quiet.id, log_date=TODAY - timedelta(days=30))

    assert _names(client) == ["Stale", "Quiet"]

    # The 7pm email task lands one entry on the quiet contact. That alone
    # reorders the list — nothing else about either record changed.
    _entry(db_session, contact_id=quiet.id, log_date=TODAY,
           action_taken="Replied about the Reston space")

    assert _names(client) == ["Quiet", "Stale"]


def test_the_top_card_reports_the_entry_that_put_it_there(db_session, client):
    """The sort key and the text on the card are the same entry. If they can
    disagree, the list is ordered by something the card does not show."""
    person = _contact(db_session, name="Dana Reed")
    _entry(db_session, contact_id=person.id, log_date=TODAY - timedelta(days=5),
           action_taken="Old note")
    newest = _entry(db_session, contact_id=person.id, log_date=TODAY,
                    action_taken="Called about the renewal", channel="call")

    row = client.get("/api/contacts/").json()[0]
    assert row["latest_entry_date"] == TODAY.isoformat()
    assert row["latest_entry_id"] == newest.id
    assert row["latest_entry_summary"] == "Called about the renewal"


# ══ 2. An entry moved onto a contact re-sorts that contact ════════════════════

def test_moving_an_entry_onto_a_contact_re_sorts_that_contact(db_session, client):
    """An entry arriving from a company card is an entry arriving. Jack moving
    a voicemail onto the right person is the moment that person becomes the
    thing he is working on."""
    co = _company(db_session)
    recent = _contact(db_session, name="Recently Touched", company_id=co.id)
    target = _contact(db_session, name="Move Target", company_id=co.id)

    _entry(db_session, contact_id=recent.id, log_date=TODAY - timedelta(days=2))
    _entry(db_session, contact_id=target.id, log_date=TODAY - timedelta(days=90))
    # Held on the company card, newer than anything on either person.
    held = _entry(db_session, company_stamp_id=co.id, log_date=TODAY,
                  action_taken="Voicemail about the sublease")

    assert _names(client) == ["Recently Touched", "Move Target"]

    r = client.patch(f"/api/activity/{held.id}/assign",
                     json={"contact_id": target.id})
    assert r.status_code == 200, r.text

    assert _names(client) == ["Move Target", "Recently Touched"]
    row = client.get("/api/contacts/").json()[0]
    assert row["latest_entry_id"] == held.id
    assert row["latest_entry_summary"] == "Voicemail about the sublease"


def test_moving_an_entry_away_re_sorts_the_contact_it_left(db_session, client):
    """The other half of the same move: a contact whose newest entry was taken
    off them falls back to whatever they actually have left."""
    older = _contact(db_session, name="Older")
    loses_it = _contact(db_session, name="Loses It")
    _entry(db_session, contact_id=older.id, log_date=TODAY - timedelta(days=3))
    _entry(db_session, contact_id=loses_it.id, log_date=TODAY - timedelta(days=10))
    moved = _entry(db_session, contact_id=loses_it.id, log_date=TODAY)

    assert _names(client) == ["Loses It", "Older"]

    client.patch(f"/api/activity/{moved.id}/assign", json={"contact_id": older.id})

    assert _names(client) == ["Older", "Loses It"]


# ══ 3. Same-day ties break on entry id, newest first ══════════════════════════

def test_same_day_entries_break_on_entry_id_newest_first(db_session, client):
    first = _contact(db_session, name="Logged First")
    second = _contact(db_session, name="Logged Second")
    _entry(db_session, contact_id=first.id, log_date=TODAY)
    _entry(db_session, contact_id=second.id, log_date=TODAY)

    assert _names(client) == ["Logged Second", "Logged First"]


def test_the_tie_break_uses_the_newest_entry_not_the_largest_id(db_session, client):
    """A backdated entry must not decide the order. Contact A's newest entry is
    older than contact B's despite A holding the higher id — a note typed today
    about a call last month is not news from today."""
    a = _contact(db_session, name="Holds A Backdated Note")
    b = _contact(db_session, name="Genuinely Newer")

    _entry(db_session, contact_id=a.id, log_date=TODAY - timedelta(days=2))
    _entry(db_session, contact_id=b.id, log_date=TODAY - timedelta(days=1))
    # Written last (highest id) but describing something a month old.
    _entry(db_session, contact_id=a.id, log_date=TODAY - timedelta(days=30),
           action_taken="Backfilled: spoke in August")

    rows = {r["name"]: r for r in client.get("/api/contacts/").json()}
    assert rows["Holds A Backdated Note"]["latest_entry_date"] == (
        TODAY - timedelta(days=2)
    ).isoformat()
    assert _names(client) == ["Genuinely Newer", "Holds A Backdated Note"]


# ══ 4. A contact with no entries still appears, at the bottom ═════════════════

def test_a_contact_with_no_entries_still_appears_at_the_bottom(db_session, client):
    _contact(db_session, name="Never Touched")
    touched = _contact(db_session, name="Touched")
    _entry(db_session, contact_id=touched.id, log_date=TODAY - timedelta(days=200))

    names = _names(client)
    assert "Never Touched" in names, "a contact with no activity is still a contact"
    assert names[-1] == "Never Touched", (
        "no activity sorts last — even behind a touch from 200 days ago"
    )


def test_next_touch_ordering_still_governs_the_no_activity_tail(db_session, client):
    """Overdue-first did not go away; it moved underneath. Among contacts with
    nothing to sort by, it is the only signal there is."""
    _contact(db_session, name="No Date")
    _contact(db_session, name="Future", next_touch_date=TODAY + timedelta(days=30))
    _contact(db_session, name="Overdue", next_touch_date=TODAY - timedelta(days=10))
    _contact(db_session, name="Due Today", next_touch_date=TODAY)
    busy = _contact(db_session, name="Busy")
    _entry(db_session, contact_id=busy.id, log_date=TODAY)

    names = _names(client)
    assert names[0] == "Busy", "activity outranks a next-touch date"
    assert names[1:3] == ["Overdue", "Due Today"], (
        f"overdue first, soonest first, among the untouched; got {names}"
    )


# ══ 5. Company cards sort by their newest contactless entry ═══════════════════

def _cards(client):
    r = client.get("/api/contacts/company-cards", params={"triaged": "false"})
    assert r.status_code == 200, r.text
    return r.json()


def test_company_cards_sort_by_their_newest_contactless_entry(db_session, client):
    old_co = _company(db_session, name="Old Co", business_id="CO-001")
    new_co = _company(db_session, name="New Co", business_id="CO-002")
    _entry(db_session, company_stamp_id=old_co.id, log_date=TODAY - timedelta(days=40))
    _entry(db_session, company_stamp_id=new_co.id, log_date=TODAY - timedelta(days=60))

    assert [c["name"] for c in _cards(client)] == ["Old Co", "New Co"]

    # One newer held entry flips them, exactly as it would for a contact.
    newest = _entry(db_session, company_stamp_id=new_co.id, log_date=TODAY,
                    action_taken="Second voicemail")

    cards = _cards(client)
    assert [c["name"] for c in cards] == ["New Co", "Old Co"]
    assert cards[0]["last_touch"] == TODAY.isoformat()
    assert cards[0]["latest_entry_id"] == newest.id


def test_company_cards_carry_the_key_the_contact_rows_sort_on(db_session, client):
    """The merged list orders cards and contacts against each other, so both
    have to expose the same two-part key. A card missing its entry id would
    land in an arbitrary place among same-day contacts."""
    co = _company(db_session)
    held = _entry(db_session, company_stamp_id=co.id, log_date=TODAY)
    person = _contact(db_session, name="A Person", company_id=co.id)
    _entry(db_session, contact_id=person.id, log_date=TODAY)

    card = _cards(client)[0]
    row = client.get("/api/contacts/").json()[0]
    assert card["last_touch"] == row["latest_entry_date"] == TODAY.isoformat()
    assert card["latest_entry_id"] == held.id
    assert isinstance(row["latest_entry_id"], int)


def test_company_cards_break_same_day_ties_on_entry_id(db_session, client):
    first = _company(db_session, name="Called First", business_id="CO-001")
    second = _company(db_session, name="Called Second", business_id="CO-002")
    _entry(db_session, company_stamp_id=first.id, log_date=TODAY)
    _entry(db_session, company_stamp_id=second.id, log_date=TODAY)

    assert [c["name"] for c in _cards(client)] == ["Called Second", "Called First"]


# ══ 6. Stage and status edits do not re-sort ══════════════════════════════════

def test_a_stage_change_does_not_re_sort(db_session, client):
    """A stage change writes a divider, not a touch. Moving someone to Replied
    from a dropdown is not news arriving — and the list's whole promise is that
    the top card is the last thing that actually happened."""
    top = _contact(db_session, name="Top", stage="Sent")
    bottom = _contact(db_session, name="Bottom", stage="Sent")
    _entry(db_session, contact_id=top.id, log_date=TODAY)
    _entry(db_session, contact_id=bottom.id, log_date=TODAY - timedelta(days=14))

    assert _names(client) == ["Top", "Bottom"]

    r = client.patch(f"/api/contacts/{bottom.id}", json={"stage": "Interested"})
    assert r.status_code == 200, r.text

    assert _names(client) == ["Top", "Bottom"], "a stage change is not a touch"
    row = next(x for x in client.get("/api/contacts/").json() if x["id"] == bottom.id)
    assert row["stage"] == "Interested", "the edit still took"
    assert row["latest_entry_date"] == (TODAY - timedelta(days=14)).isoformat(), (
        "the divider must not become the latest entry"
    )


def test_a_status_edit_does_not_re_sort(db_session, client):
    top = _contact(db_session, name="Top")
    bottom = _contact(db_session, name="Bottom")
    _entry(db_session, contact_id=top.id, log_date=TODAY)
    _entry(db_session, contact_id=bottom.id, log_date=TODAY - timedelta(days=14))

    assert _names(client) == ["Top", "Bottom"]

    r = client.patch(f"/api/contacts/{bottom.id}/status",
                     json={"current_status": "Waiting on their board"})
    assert r.status_code == 200, r.text

    assert _names(client) == ["Top", "Bottom"], "a status line is not a touch"
    row = next(x for x in client.get("/api/contacts/").json() if x["id"] == bottom.id)
    assert row["current_status"] == "Waiting on their board"
    assert row["latest_entry_date"] == (TODAY - timedelta(days=14)).isoformat()


def test_a_next_touch_edit_does_not_re_sort_a_contact_with_activity(
    db_session, client,
):
    """Next-touch now sits below activity. Setting a date on a quiet contact
    plans future work; it does not claim something just happened."""
    top = _contact(db_session, name="Top")
    bottom = _contact(db_session, name="Bottom")
    _entry(db_session, contact_id=top.id, log_date=TODAY)
    _entry(db_session, contact_id=bottom.id, log_date=TODAY - timedelta(days=14))

    client.patch(f"/api/contacts/{bottom.id}",
                 json={"next_touch_date": (TODAY - timedelta(days=5)).isoformat()})

    assert _names(client) == ["Top", "Bottom"]


# ══ 7. The sort does not reach for extra queries ══════════════════════════════

def test_the_recency_sort_adds_no_per_contact_query(db_session, client):
    """The tie-break is a subquery in the same statement, not a lookup per row.
    Guarded here as well as in test_contact_threads because this is the change
    most likely to turn the list into a query loop."""
    co = _company(db_session, business_id="CO-990")
    for i in range(40):
        c = _contact(db_session, name=f"Person {i}", company_id=co.id)
        _entry(db_session, contact_id=c.id, company_stamp_id=co.id,
               log_date=TODAY - timedelta(days=i))

    seen = []
    from sqlalchemy import event
    engine = db_session.get_bind()

    def _count(conn, cursor, statement, *a, **kw):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        rows = client.get("/api/contacts/").json()
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert [r["name"] for r in rows][:3] == ["Person 0", "Person 1", "Person 2"]
    assert len(seen) <= 5, (
        f"expected a small constant number of queries for 40 contacts, ran {len(seen)}"
    )
