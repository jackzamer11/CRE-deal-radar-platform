"""Contactless entries — every entry ends up on a contact, and Jack puts it there.

The rules this file locks:

  - a contactless entry with a company appears on that company's card
  - "Move to contact" works for an existing contact and a newly created one
  - "Move all" moves every entry a company holds, and the facts sourced to them
  - moving a contactless entry teaches the resolver NOTHING
  - nothing auto-assigns: a new contactless entry for a company that already
    has contacts stays on the card
  - an entry with no company can be moved to any contact
  - the needs-a-contact queue lists every contactless entry, and its count
    drops when one is moved
  - contact cards are unchanged by any of it

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
from app.models.contact import Contact, ContactFact
from app.models.email_ingest import ContactAddressOverride
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


def _contact(db, name="Dana Reed", **kw):
    kw.setdefault("contact_type", "tenant")
    kw.setdefault("stage", "Sent")
    c = Contact(name=name, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _entry(db, **kw):
    kw.setdefault("action_type", "EMAIL")
    kw.setdefault("action_taken", "Voicemail to the main line")
    kw.setdefault("log_date", date.today())
    e = ActivityLog(**kw)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _cards(client, **params):
    r = client.get("/api/contacts/company-cards", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _queue(client, **params):
    r = client.get("/api/activity/needs-contact", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── The company card ─────────────────────────────────────────────────────────

def test_contactless_entry_with_a_company_appears_on_that_companys_card(
    client, db_session,
):
    """The core promise: the company holds the entry until Jack assigns it."""
    co = _company(db_session)
    _entry(db_session, company_stamp_id=co.id, log_date=date(2026, 7, 1))
    _entry(
        db_session, company_stamp_id=co.id, log_date=date(2026, 9, 13),
        action_taken="Called, left a message",
    )

    cards = _cards(client)
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == co.id
    assert card["name"] == "Scott Management"
    assert card["company_key"] == "CO-001"
    assert card["entry_count"] == 2
    # Last touch is the most recent held entry, not the oldest.
    assert card["last_touch"] == "2026-09-13"
    assert card["latest_entry_summary"] == "Called, left a message"
    # Nobody to put them on yet — the card has to say so.
    assert card["contact_count"] == 0


def test_card_reads_the_legacy_free_company_link_when_there_is_no_stamp(
    client, db_session,
):
    """54 of the manually-logged contactless entries carry only company_id."""
    co = _company(db_session)
    _entry(db_session, company_id=co.id, company_stamp_id=None)

    cards = _cards(client)
    assert [c["entry_count"] for c in cards] == [1]


def test_a_company_card_is_always_untriaged(client, db_session):
    """A card is work not yet done. Triaged would hide it where it dies."""
    co = _company(db_session, triaged=True)
    _entry(db_session, company_stamp_id=co.id)

    assert _cards(client)[0]["triaged"] is False
    # And the triaged filter behaves accordingly: asking for triaged records
    # returns no cards at all.
    assert _cards(client, triaged=True) == []
    assert len(_cards(client, triaged=False)) == 1


def test_a_stage_filter_returns_no_company_cards(client, db_session):
    """Stage belongs to a person. A held company has no place in a pipeline."""
    co = _company(db_session)
    _entry(db_session, company_stamp_id=co.id)
    assert _cards(client, stage="Interested") == []


def test_card_search_matches_the_company_name(client, db_session):
    co = _company(db_session, name="Scott Management")
    other = _company(db_session, name="Reico", business_id="CO-002")
    _entry(db_session, company_stamp_id=co.id)
    _entry(db_session, company_stamp_id=other.id)

    assert [c["name"] for c in _cards(client, q="reico")] == ["Reico"]


def test_an_entry_already_on_a_contact_never_reaches_a_card(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    _entry(db_session, company_stamp_id=co.id, contact_id=person.id)

    assert _cards(client) == []


# ── No auto-assignment ───────────────────────────────────────────────────────

def test_a_company_with_contacts_still_holds_its_contactless_entry(
    client, db_session,
):
    """Nothing guesses. Three people at Scott Management means three wrong
    answers available, so the entry waits."""
    co = _company(db_session)
    _contact(db_session, name="Dana Reed", company_id=co.id)
    _contact(db_session, name="Mark Liu", company_id=co.id)
    held = _entry(db_session, company_stamp_id=co.id)

    cards = _cards(client)
    assert len(cards) == 1
    assert cards[0]["entry_count"] == 1
    # The card says there ARE people — it just does not pick one.
    assert cards[0]["contact_count"] == 2

    db_session.refresh(held)
    assert held.contact_id is None


# ── Move to contact, per entry ───────────────────────────────────────────────

def test_move_a_contactless_entry_to_an_existing_contact(client, db_session):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    held = _entry(db_session, company_stamp_id=co.id)

    r = client.patch(
        f"/api/activity/{held.id}/assign", json={"contact_id": person.id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contact_id"] == person.id

    db_session.refresh(held)
    assert held.contact_id == person.id
    # The stamp it already had is preserved, not rewritten.
    assert held.company_stamp_id == co.id
    # Off the card.
    assert _cards(client) == []


def test_move_to_a_contact_created_inline_at_that_company(client, db_session):
    """Create the person, then move — the two-step the picker performs."""
    co = _company(db_session)
    held = _entry(db_session, company_stamp_id=co.id)

    created = client.post("/api/contacts/", json={
        "name": "New Person", "email": "new@scottmgmt.com", "company_id": co.id,
    })
    assert created.status_code == 200, created.text
    new_id = created.json()["id"]
    assert created.json()["company_id"] == co.id

    r = client.patch(
        f"/api/activity/{held.id}/assign", json={"contact_id": new_id},
    )
    assert r.status_code == 200, r.text
    db_session.refresh(held)
    assert held.contact_id == new_id


def test_moving_an_entry_with_no_stamp_stamps_it_from_the_contact(
    client, db_session,
):
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id)
    held = _entry(db_session, company_id=None, company_stamp_id=None)

    client.patch(f"/api/activity/{held.id}/assign", json={"contact_id": person.id})
    db_session.refresh(held)
    assert held.company_stamp_id == co.id


# ── Entries with no company ──────────────────────────────────────────────────

def test_an_entry_with_no_company_can_be_moved_to_any_contact(client, db_session):
    """No company is no obstacle: the picker searches everyone."""
    other_co = _company(db_session, name="Reico", business_id="CO-002")
    person = _contact(db_session, name="Ann Waller", company_id=other_co.id)
    orphan = _entry(
        db_session, company_id=None, company_stamp_id=None,
        action_taken="cold emailed CEO of DataCoreAI",
    )

    # It is in the queue, and it is not on any card.
    assert _cards(client) == []
    assert orphan.id in [e["id"] for e in _queue(client)["entries"]]

    r = client.patch(
        f"/api/activity/{orphan.id}/assign", json={"contact_id": person.id},
    )
    assert r.status_code == 200, r.text
    db_session.refresh(orphan)
    assert orphan.contact_id == person.id
    assert orphan.company_stamp_id == other_co.id


def test_a_contact_can_be_created_against_a_typed_company_name(client, db_session):
    """"…or create a new contact with a company I choose or type." A typed name
    that matches an existing company reuses it rather than forking the record."""
    existing = _company(db_session, name="Scott Management")

    r = client.post("/api/contacts/", json={
        "name": "Typed Person", "company_name": "Scott Management LLC",
    })
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == existing.id

    # A name nothing matches creates the company.
    r2 = client.post("/api/contacts/", json={
        "name": "Another Person", "company_name": "Brand New Tenant Co",
    })
    assert r2.status_code == 200, r2.text
    new_company_id = r2.json()["company_id"]
    assert new_company_id is not None and new_company_id != existing.id
    made = db_session.query(Company).filter(Company.id == new_company_id).first()
    assert made.name == "Brand New Tenant Co"


def test_a_picked_company_wins_over_a_typed_one(client, db_session):
    picked = _company(db_session, name="Scott Management")
    _company(db_session, name="Reico", business_id="CO-002")

    r = client.post("/api/contacts/", json={
        "name": "Person", "company_id": picked.id, "company_name": "Reico",
    })
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == picked.id


# ── Move all, per company ────────────────────────────────────────────────────

def test_move_all_moves_every_entry_and_every_fact(client, db_session):
    """One action drains the card — entries and the facts sourced to them."""
    co = _company(db_session)
    sender = _contact(db_session, name="Ann Waller")
    target = _contact(db_session, name="Dana Reed", company_id=co.id)

    held = [
        _entry(db_session, company_stamp_id=co.id, log_date=date(2026, 7, 1)),
        _entry(db_session, company_stamp_id=co.id, log_date=date(2026, 8, 1)),
        _entry(db_session, company_id=co.id, log_date=date(2026, 9, 1)),
    ]
    # A roundup writes the deal's fact onto the SENDER while the deal entry
    # itself stays contactless. The fact has to travel with the entry.
    fact = ContactFact(
        contact_id=sender.id, fact_text="Needs 4,000 SF by Q1",
        source_entry_id=held[0].id, learned_date=date(2026, 7, 1),
    )
    # A fact Ann owns that came from somewhere else must NOT move.
    hers = ContactFact(
        contact_id=sender.id, fact_text="Ann covers the whole NoVA book",
        source_entry_id=None, learned_date=date(2026, 7, 1),
    )
    db_session.add_all([fact, hers])
    db_session.commit()

    r = client.post("/api/activity/move-all-to-contact", json={
        "company_id": co.id, "contact_id": target.id,
    })
    assert r.status_code == 200, r.text
    assert r.json()["moved"] == 3
    assert r.json()["facts_moved"] == 1

    for e in held:
        db_session.refresh(e)
        assert e.contact_id == target.id

    db_session.refresh(fact)
    db_session.refresh(hers)
    assert fact.contact_id == target.id
    assert hers.contact_id == sender.id

    # The card is gone and the queue is empty.
    assert _cards(client) == []
    assert _queue(client)["total"] == 0


def test_move_all_leaves_other_companies_alone(client, db_session):
    co = _company(db_session)
    other = _company(db_session, name="Reico", business_id="CO-002")
    target = _contact(db_session, company_id=co.id)
    mine = _entry(db_session, company_stamp_id=co.id)
    theirs = _entry(db_session, company_stamp_id=other.id)

    client.post("/api/activity/move-all-to-contact", json={
        "company_id": co.id, "contact_id": target.id,
    })
    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.contact_id == target.id
    assert theirs.contact_id is None


def test_move_all_404s_on_an_unknown_company_or_contact(client, db_session):
    co = _company(db_session)
    person = _contact(db_session)
    assert client.post("/api/activity/move-all-to-contact", json={
        "company_id": 9999, "contact_id": person.id,
    }).status_code == 404
    assert client.post("/api/activity/move-all-to-contact", json={
        "company_id": co.id, "contact_id": 9999,
    }).status_code == 404


# ── The teaching guard ───────────────────────────────────────────────────────

def test_moving_a_contactless_entry_teaches_the_resolver_nothing(
    client, db_session,
):
    """Moving an entry off NOBODY says where it belongs, never whose address it
    is. Teaching here would file every future email from that address under
    whoever happened to receive one held entry."""
    co = _company(db_session)
    target = _contact(db_session, name="Dana Reed", company_id=co.id)
    held = _entry(
        db_session, company_stamp_id=co.id,
        sender_email="ann@brokerage.com",
        source_message_id="<msg-1@mail>",
    )

    r = client.patch(
        f"/api/activity/{held.id}/assign", json={"contact_id": target.id},
    )
    assert r.status_code == 200, r.text

    assert db_session.query(ContactAddressOverride).count() == 0


def test_move_all_teaches_the_resolver_nothing(client, db_session):
    co = _company(db_session)
    target = _contact(db_session, company_id=co.id)
    _entry(db_session, company_stamp_id=co.id, sender_email="ann@brokerage.com")
    _entry(db_session, company_stamp_id=co.id, sender_email="pat@brokerage.com")

    r = client.post("/api/activity/move-all-to-contact", json={
        "company_id": co.id, "contact_id": target.id,
    })
    assert r.status_code == 200, r.text
    assert db_session.query(ContactAddressOverride).count() == 0


# ── The needs-a-contact queue ────────────────────────────────────────────────

def test_the_queue_lists_every_contactless_entry_oldest_first(client, db_session):
    co = _company(db_session)
    with_company = _entry(
        db_session, company_stamp_id=co.id, log_date=date(2026, 8, 1),
    )
    oldest = _entry(
        db_session, company_id=None, company_stamp_id=None,
        log_date=date(2026, 4, 14),
    )
    newest = _entry(
        db_session, company_id=None, company_stamp_id=None,
        log_date=date(2026, 9, 15),
    )
    # An entry already on a person is not in the queue.
    person = _contact(db_session, company_id=co.id)
    _entry(db_session, company_stamp_id=co.id, contact_id=person.id)

    page = _queue(client)
    assert page["total"] == 3
    assert [e["id"] for e in page["entries"]] == [oldest.id, with_company.id, newest.id]

    # Company context travels with the row so the picker can open on it.
    row = next(e for e in page["entries"] if e["id"] == with_company.id)
    assert row["effective_company_id"] == co.id
    assert row["effective_company_name"] == "Scott Management"
    orphan_row = next(e for e in page["entries"] if e["id"] == oldest.id)
    assert orphan_row["effective_company_id"] is None


def test_the_queue_narrows_to_one_company(client, db_session):
    """Opening a card shows what THAT company is holding, and `total` follows."""
    co = _company(db_session)
    other = _company(db_session, name="Reico", business_id="CO-002")
    mine = _entry(db_session, company_stamp_id=co.id)
    _entry(db_session, company_stamp_id=other.id)
    _entry(db_session, company_id=None, company_stamp_id=None)

    page = _queue(client, company_id=co.id)
    assert page["total"] == 1
    assert [e["id"] for e in page["entries"]] == [mine.id]
    # Unfiltered, it is still the whole queue.
    assert _queue(client)["total"] == 3


def test_the_queue_count_drops_when_an_entry_is_moved(client, db_session):
    """Drain it to zero — the point of the surface."""
    co = _company(db_session)
    target = _contact(db_session, company_id=co.id)
    a = _entry(db_session, company_stamp_id=co.id)
    _entry(db_session, company_id=None, company_stamp_id=None)

    assert client.get("/api/activity/needs-contact/count").json()["total"] == 2

    client.patch(f"/api/activity/{a.id}/assign", json={"contact_id": target.id})
    assert client.get("/api/activity/needs-contact/count").json()["total"] == 1
    assert _queue(client)["total"] == 1


def test_the_queue_paginates_without_losing_the_badge_count(client, db_session):
    """`total` is how much is left, not how much this page shows."""
    for i in range(5):
        _entry(
            db_session, company_id=None, company_stamp_id=None,
            log_date=date(2026, 6, 1) + timedelta(days=i),
        )
    page = _queue(client, limit=2)
    assert page["total"] == 5
    assert len(page["entries"]) == 2


def test_a_stage_change_divider_is_never_in_the_queue(client, db_session):
    _entry(
        db_session, action_type="STAGE_CHANGE", action_taken="Stage: Sent → Replied",
        stage_from="Sent", stage_to="Replied",
    )
    assert _queue(client)["total"] == 0


# ── Nothing else moved ───────────────────────────────────────────────────────

def test_contact_cards_are_unchanged(client, db_session):
    """The By Contact list is untouched by any of this: same rows, same counts,
    and contactless entries stay invisible to it."""
    co = _company(db_session)
    person = _contact(db_session, company_id=co.id, triaged=True)
    _entry(
        db_session, company_stamp_id=co.id, contact_id=person.id,
        log_date=date(2026, 9, 1), action_taken="Emailed about the renewal",
    )
    _entry(db_session, company_stamp_id=co.id, log_date=date(2026, 9, 20))

    r = client.get("/api/contacts/")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == person.id
    # The held entry counts toward nothing on the person's row.
    assert row["entry_count"] == 1
    assert row["latest_entry_date"] == "2026-09-01"
    assert row["latest_entry_summary"] == "Emailed about the renewal"


def test_moving_an_entry_between_two_contacts_leaves_their_facts_alone(
    client, db_session,
):
    """Fact-following is for contactless entries only. A correction between two
    people is not a claim about where a fact belongs."""
    co = _company(db_session)
    first = _contact(db_session, name="Dana Reed", company_id=co.id)
    second = _contact(db_session, name="Mark Liu", company_id=co.id)
    entry = _entry(db_session, company_stamp_id=co.id, contact_id=first.id)
    fact = ContactFact(
        contact_id=first.id, fact_text="Prefers a 5-year term",
        source_entry_id=entry.id, learned_date=date.today(),
    )
    db_session.add(fact)
    db_session.commit()

    client.patch(f"/api/activity/{entry.id}/assign", json={"contact_id": second.id})
    db_session.refresh(entry)
    db_session.refresh(fact)
    assert entry.contact_id == second.id
    assert fact.contact_id == first.id


def test_moving_an_entry_triages_the_contact_it_lands_on(client, db_session):
    co = _company(db_session)
    target = _contact(db_session, company_id=co.id, triaged=False)
    held = _entry(db_session, company_stamp_id=co.id)

    client.patch(f"/api/activity/{held.id}/assign", json={"contact_id": target.id})
    db_session.refresh(target)
    assert target.triaged is True


def test_the_picker_can_list_everyone_at_a_company(client, db_session):
    """The picker opens on the company's people before Jack types anything."""
    co = _company(db_session)
    other = _company(db_session, name="Reico", business_id="CO-002")
    _contact(db_session, name="Dana Reed", company_id=co.id)
    _contact(db_session, name="Mark Liu", company_id=co.id)
    _contact(db_session, name="Ann Waller", company_id=other.id)

    r = client.get("/api/contacts/search", params={"company_id": co.id})
    assert r.status_code == 200, r.text
    assert sorted(c["name"] for c in r.json()) == ["Dana Reed", "Mark Liu"]

    # An empty term with no company still returns nothing — unchanged.
    assert client.get("/api/contacts/search", params={"q": ""}).json() == []
