"""Contact threads — structural contracts.

A Contact is a first-class record that owns its pipeline stage and next-touch
date; activity entries attach to it. This file locks the behaviours that are
easy to break silently:

  - timeline ordering and entry counts
  - the immutability of company_stamp_id (a departed contact's history stays on
    the old company)
  - stage changes writing a timeline event
  - Re-engage Today surfacing contact-sourced dates
  - the /api/companies/ contract outreach_agent.py depends on
  - from-email: idempotency, free-mail handling, the inbound stage rules
  - triage flipping through normal use rather than a queue
  - superseded facts leaving the active set but staying retrievable
  - message-ids reading the indexed column with no ceiling
  - counterparty thread text never reaching tenant-facing generated copy

In-memory SQLite, dependency-overridden get_db. No live DB file, no network,
no CoStar, no OpenAI or Anthropic calls.
"""
from datetime import date, timedelta
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
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, ContactFact
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

def _company(db, name="Acme Corp", business_id="CO-001", **kw):
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact(db, name="Joe Tenant", **kw):
    kw.setdefault("contact_type", "tenant")
    kw.setdefault("stage", "Sent")
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


# ══ 1. Timeline ordering and entry counts ═════════════════════════════════════

def test_three_entries_return_newest_first_with_entry_count_three(db_session, client):
    company = _company(db_session)
    contact = _contact(db_session, company_id=company.id, triaged=True)

    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           log_date=date(2026, 3, 1), action_taken="Oldest")
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           log_date=date(2026, 5, 1), action_taken="Middle")
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           log_date=date(2026, 7, 1), action_taken="Newest")

    page = client.get(f"/api/contacts/{contact.id}/timeline").json()
    assert page["total"] == 3
    assert [e["action_taken"] for e in page["entries"]] == ["Newest", "Middle", "Oldest"]

    rows = client.get("/api/contacts/").json()
    row = next(r for r in rows if r["id"] == contact.id)
    assert row["entry_count"] == 3
    assert row["latest_entry_date"] == "2026-07-01"
    assert row["latest_entry_summary"] == "Newest"


def test_empty_timeline_and_missing_facts_do_not_500(db_session, client):
    contact = _contact(db_session, name="No History")
    page = client.get(f"/api/contacts/{contact.id}/timeline")
    assert page.status_code == 200
    assert page.json()["total"] == 0
    assert page.json()["entries"] == []

    header = client.get(f"/api/contacts/{contact.id}")
    assert header.status_code == 200
    body = header.json()
    assert body["facts"] == []
    assert body["relationship_lines"] == []
    assert body["company_name"] is None       # null company is fine
    assert body["conflicts"] == []


def test_timeline_paginates_without_relying_on_a_high_default(db_session, client):
    company = _company(db_session)
    contact = _contact(db_session, company_id=company.id)
    for i in range(250):
        _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
               log_date=date(2026, 1, 1) + timedelta(days=i),
               action_taken=f"Entry {i}")

    first = client.get(f"/api/contacts/{contact.id}/timeline?limit=100&offset=0").json()
    assert first["total"] == 250
    assert len(first["entries"]) == 100

    # Page 3 must exist — no 200-style cap silently truncating the tail, which
    # is exactly the shape of the July Activity Log bug.
    third = client.get(f"/api/contacts/{contact.id}/timeline?limit=100&offset=200").json()
    assert len(third["entries"]) == 50
    assert third["entries"][0]["action_taken"] == "Entry 49"


# ══ 2. Null contact_id, valid company_stamp_id ════════════════════════════════

def test_unattached_entry_appears_on_company_timeline_only(db_session, client):
    company = _company(db_session, business_id="CO-010")
    contact = _contact(db_session, company_id=company.id)
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           action_taken="Call with Joe")
    # A voicemail to the main line: a company, but no person.
    _entry(db_session, contact_id=None, company_stamp_id=company.id,
           action_type="CALL", action_taken="Voicemail to main line")

    company_page = client.get(f"/api/companies/{company.company_id}/timeline")
    assert company_page.status_code == 200
    body = company_page.json()
    assert body["total"] == 2
    summaries = [e["action_taken"] for e in body["entries"]]
    assert "Voicemail to main line" in summaries
    # The unattached entry keeps a null contact_name rather than dropping out.
    unattached = next(e for e in body["entries"] if e["action_taken"] == "Voicemail to main line")
    assert unattached["contact_id"] is None
    assert unattached["contact_name"] is None

    # ...and it is on no contact's timeline.
    contact_page = client.get(f"/api/contacts/{contact.id}/timeline").json()
    assert [e["action_taken"] for e in contact_page["entries"]] == ["Call with Joe"]


def test_company_timeline_for_company_with_no_entries_is_empty_not_500(db_session, client):
    company = _company(db_session, business_id="CO-011")
    resp = client.get(f"/api/companies/{company.company_id}/timeline")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["entries"] == []


# ══ 3. company_stamp_id is immutable ══════════════════════════════════════════

def test_changing_a_contacts_company_leaves_entries_stamped_to_the_original(db_session, client):
    old_co = _company(db_session, name="Old Employer", business_id="CO-100")
    new_co = _company(db_session, name="New Employer", business_id="CO-200")
    contact = _contact(db_session, name="Dana Mover", company_id=old_co.id)

    for i in range(3):
        _entry(db_session, contact_id=contact.id, company_stamp_id=old_co.id,
               log_date=date(2026, 1, 1) + timedelta(days=i),
               action_taken=f"Conversation {i} at Old Employer")

    # Dana changes jobs.
    resp = client.patch(f"/api/contacts/{contact.id}", json={"company_id": new_co.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["company_name"] == "New Employer"

    # Her history stays on the old company's page...
    old_page = client.get(f"/api/companies/{old_co.company_id}/timeline").json()
    assert old_page["total"] == 3
    new_page = client.get(f"/api/companies/{new_co.company_id}/timeline").json()
    assert new_page["total"] == 0

    # ...and her own thread still returns all of them.
    thread = client.get(f"/api/contacts/{contact.id}/timeline").json()
    assert thread["total"] == 3

    # The stamp itself never moved.
    stamps = {
        log.company_stamp_id
        for log in db_session.query(ActivityLog).filter(
            ActivityLog.contact_id == contact.id
        ).all()
    }
    assert stamps == {old_co.id}


def test_assign_does_not_rewrite_an_existing_stamp(db_session, client):
    old_co = _company(db_session, name="Stamped Co", business_id="CO-300")
    new_co = _company(db_session, name="Current Co", business_id="CO-301")
    contact = _contact(db_session, name="Dana Late", company_id=new_co.id)
    # A March voicemail, stamped to the company it was actually about.
    entry = _entry(db_session, contact_id=None, company_stamp_id=old_co.id,
                   action_type="CALL", action_taken="March voicemail")

    resp = client.patch(f"/api/activity/{entry.id}/assign",
                        json={"contact_id": contact.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["contact_id"] == contact.id
    # Retroactive assignment attaches the person without rewriting history.
    assert resp.json()["company_stamp_id"] == old_co.id


def test_assign_fills_a_missing_stamp_from_the_contact(db_session, client):
    company = _company(db_session, business_id="CO-302")
    contact = _contact(db_session, name="Dana Unstamped", company_id=company.id)
    entry = _entry(db_session, contact_id=None, company_stamp_id=None,
                   action_type="CALL", action_taken="Unstamped voicemail")

    resp = client.patch(f"/api/activity/{entry.id}/assign",
                        json={"contact_id": contact.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["company_stamp_id"] == company.id


# ══ 4. Stage change updates the contact and writes a timeline event ═══════════

def test_stage_change_updates_contact_and_writes_a_timeline_event(db_session, client):
    company = _company(db_session, business_id="CO-400")
    contact = _contact(db_session, name="Stage Mover", company_id=company.id,
                       stage="Replied")

    resp = client.patch(f"/api/contacts/{contact.id}", json={"stage": "In Play"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "In Play"

    db_session.refresh(contact)
    assert contact.stage == "In Play"
    assert contact.stage_changed_at == date.today()

    timeline = client.get(f"/api/contacts/{contact.id}/timeline").json()
    events = [e["action_taken"] for e in timeline["entries"]]
    assert "Stage: Replied → In Play" in events, (
        f"a stage change must preserve the transition on the thread; got {events}"
    )


def test_stage_is_never_auto_advanced_or_drifted_to_dormant(db_session, client):
    contact = _contact(db_session, name="Left Alone", stage="Interested",
                       next_touch_date=date.today() - timedelta(days=400))
    # Reading the thread and the re-engage list must not move anyone.
    client.get(f"/api/contacts/{contact.id}")
    client.get("/api/activity/re-engage")
    client.get("/api/contacts/")
    db_session.refresh(contact)
    assert contact.stage == "Interested"


def test_invalid_stage_is_400_not_500(db_session, client):
    contact = _contact(db_session)
    resp = client.patch(f"/api/contacts/{contact.id}", json={"stage": "Bogus"})
    assert resp.status_code == 400


# ══ 5. Re-engage Today ════════════════════════════════════════════════════════

def test_re_engage_returns_a_contact_whose_next_touch_date_is_today(db_session, client):
    company = _company(db_session, business_id="CO-500")
    contact = _contact(db_session, name="Due Today", company_id=company.id,
                       stage="Interested", next_touch_date=date.today())
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           action_taken="Emailed Due Today re Acme")

    rows = client.get("/api/activity/re-engage").json()
    names = [r["contact_name"] for r in rows]
    assert "Due Today" in names
    row = next(r for r in rows if r["contact_name"] == "Due Today")
    assert row["next_touch_date"] == date.today().isoformat()
    assert row["stage"] == "Interested"


def test_re_engage_surfaces_a_contact_with_no_entries_yet(db_session, client):
    _contact(db_session, name="Dated But Silent", stage="Dormant",
             next_touch_date=date.today() - timedelta(days=2))
    rows = client.get("/api/activity/re-engage").json()
    assert "Dated But Silent" in [r["contact_name"] for r in rows]


def test_re_engage_still_surfaces_legacy_entry_level_dates(db_session, client):
    """The 355 existing entries are not migrated by this build. Every person
    surfacing in Re-engage Today does so on an entry-level date, so the legacy
    source must keep working or the section silently empties."""
    _entry(db_session, contact_id=None, action_taken="Called Desta Desta",
           stage="Interested", next_touch_date=date.today() - timedelta(days=5))
    rows = client.get("/api/activity/re-engage").json()
    assert "Desta Desta" in [r["contact_name"] for r in rows]


def test_re_engage_does_not_double_count_a_backfilled_person(db_session, client):
    company = _company(db_session, business_id="CO-501")
    contact = _contact(db_session, name="Backfilled Person", company_id=company.id,
                       stage="Interested", next_touch_date=date.today())
    # The entry still carries its legacy date, as it would mid-backfill.
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           action_taken="Emailed Backfilled Person re Acme",
           stage="Interested", next_touch_date=date.today())

    rows = client.get("/api/activity/re-engage").json()
    assert [r["contact_name"] for r in rows].count("Backfilled Person") == 1


def test_re_engage_excludes_future_dates_and_is_empty_safe(client):
    assert client.get("/api/activity/re-engage").json() == []


def test_re_engage_excludes_a_contact_due_tomorrow(db_session, client):
    _contact(db_session, name="Not Yet", stage="Interested",
             next_touch_date=date.today() + timedelta(days=1))
    assert client.get("/api/activity/re-engage").json() == []


# ══ 6. /api/companies/ contract ═══════════════════════════════════════════════

def test_companies_list_still_returns_all_seven_contract_fields(db_session, client):
    _company(
        db_session, name="Contract Co", business_id="CO-600",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Tysons", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    resp = client.get("/api/companies/")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["company_id"] == "CO-600")

    # The seven fields outreach_agent.py reads, under their real serialized
    # names. Renaming or dropping any of these breaks the CLI silently.
    assert row["company_id"] == "CO-600"
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42            # headcount
    assert row["headcount_growth_pct"] == 12.5       # growth_rate
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"      # submarket
    assert row["opportunity_score"] == 77.0          # score


def test_companies_needs_outreach_filter_still_works(db_session, client):
    _company(db_session, name="Needs Outreach", business_id="CO-601",
             priority="HIGH", opportunity_score=50.0)
    resp = client.get("/api/companies/?outreach_status=needs-outreach")
    assert resp.status_code == 200
    assert "CO-601" in [r["company_id"] for r in resp.json()]


# ══ 7. from-email ═════════════════════════════════════════════════════════════

def test_from_email_creates_untriaged_contact_and_company_and_is_idempotent(db_session, client):
    payload = {
        "from_email": "Miriam Miller <Miriam@MM-RealEstate.com>",
        "from_name": "Miriam Miller",
        "to_email": "jzamer@z-reg.com",
        "direction": "inbound",
        "subject": "1205 N Pitt St",
        "action_taken": "Miriam reported two offers on the unit.",
        "source_message_id": "<msg-abc-123@mail>",
        "sent_at": "2026-09-09",
    }
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contact_id"] is not None
    assert body["company_stamp_id"] is not None
    assert body["source_message_id"] == "<msg-abc-123@mail>"
    assert body["direction"] == "inbound"
    assert body["channel"] == "email"

    contact = db_session.query(Contact).filter(Contact.id == body["contact_id"]).first()
    assert contact.auto_created is True
    assert contact.triaged is False
    assert contact.email == "miriam@mm-realestate.com"   # normalized, case-insensitive

    company = db_session.query(Company).filter(Company.id == body["company_stamp_id"]).first()
    assert company.auto_created is True
    assert company.triaged is False
    assert company.company_type is None       # no guess until Jack sets one
    assert company.email_domain == "mm-realestate.com"

    # Second POST with the same message id creates no duplicate.
    again = client.post("/api/activity/from-email", json=payload)
    assert again.status_code == 409
    assert db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id == "<msg-abc-123@mail>"
    ).count() == 1
    assert db_session.query(Contact).count() == 1
    assert db_session.query(Company).count() == 1


def test_from_email_matches_an_existing_hand_entered_company_by_name(db_session, client):
    _company(db_session, name="Corcoran McEnearney", business_id="CO-700")
    resp = client.post("/api/activity/from-email", json={
        "from_email": "someone@corcoran-mcenearney.com",
        "from_name": "Some One",
        "direction": "inbound",
        "source_message_id": "<msg-dup-check@mail>",
    })
    assert resp.status_code == 200, resp.text
    # No "Corcoran Mcenearney" duplicate of the hand-entered record.
    assert db_session.query(Company).count() == 1
    company = db_session.query(Company).first()
    assert company.name == "Corcoran McEnearney"
    assert company.email_domain == "corcoran-mcenearney.com"


def test_from_email_with_a_gmail_sender_creates_no_company(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "jim.woolwine@gmail.com",
        "from_name": "Jim Woolwine",
        "direction": "inbound",
        "action_taken": "Jim asked about small Alexandria space.",
        "source_message_id": "<msg-gmail-1@mail>",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    contact = db_session.query(Contact).filter(Contact.id == body["contact_id"]).first()
    assert contact is not None
    assert contact.company_id is None, "a free-mail sender must not get a company"
    assert db_session.query(Company).count() == 0, "never create a company called Gmail"


@pytest.mark.parametrize("addr", [
    "a@gmail.com", "b@outlook.com", "c@yahoo.com", "d@hotmail.com",
    "e@icloud.com", "f@aol.com", "g@proton.me", "h@protonmail.com",
])
def test_every_free_mail_domain_is_skipped(db_session, client, addr):
    resp = client.post("/api/activity/from-email", json={
        "from_email": addr, "from_name": "Free Mailer", "direction": "inbound",
        "source_message_id": f"<msg-{addr}@mail>",
    })
    assert resp.status_code == 200, resp.text
    assert db_session.query(Company).count() == 0


def test_from_email_inbound_sets_responded_and_moves_sent_to_replied(db_session, client):
    contact = _contact(db_session, name="Reply Guy", email="reply@acme-corp.com",
                       stage="Sent")
    resp = client.post("/api/activity/from-email", json={
        "from_email": "reply@acme-corp.com",
        "from_name": "Reply Guy",
        "direction": "inbound",
        "source_message_id": "<msg-reply-1@mail>",
    })
    assert resp.status_code == 200, resp.text
    db_session.refresh(contact)
    assert contact.responded is True
    assert contact.stage == "Replied"


def test_from_email_never_regresses_a_contact_already_in_play(db_session, client):
    contact = _contact(db_session, name="Deep In It", email="deep@acme-corp.com",
                       stage="In Play")
    resp = client.post("/api/activity/from-email", json={
        "from_email": "deep@acme-corp.com",
        "direction": "inbound",
        "source_message_id": "<msg-reply-2@mail>",
    })
    assert resp.status_code == 200, resp.text
    db_session.refresh(contact)
    assert contact.responded is True
    assert contact.stage == "In Play", "an inbound email must never regress a stage"


def test_from_email_outbound_does_not_set_responded(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "to_email": "prospect@newco-example.com",
        "direction": "outbound",
        "source_message_id": "<msg-out-1@mail>",
    })
    assert resp.status_code == 200, resp.text
    contact = db_session.query(Contact).first()
    assert contact.responded is False
    assert contact.stage == "Sent"


def test_from_email_is_atomic_when_the_entry_cannot_be_written(db_session, client):
    """Resolve contact, resolve company, stamp, create entry — one transaction.

    The company is created and flushed before the contact is. A failure between
    the two must not leave an orphan company with no contact and no entry.
    """
    before_contacts = db_session.query(Contact).count()
    before_companies = db_session.query(Company).count()

    with patch("app.api.routes.activity.resolve_or_create_contact",
               side_effect=RuntimeError("boom")):
        resp = client.post("/api/activity/from-email", json={
            "from_email": "ghost@failco-example.com",
            "direction": "inbound",
            "source_message_id": "<msg-fail-1@mail>",
        })

    assert resp.status_code == 500
    assert db_session.query(Contact).count() == before_contacts
    assert db_session.query(Company).count() == before_companies, (
        "a half-built thread must roll back, not leave an orphan company"
    )
    assert db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id == "<msg-fail-1@mail>"
    ).count() == 0

    # And the message id is still free, so the next run logs it properly.
    retry = client.post("/api/activity/from-email", json={
        "from_email": "ghost@failco-example.com",
        "direction": "inbound",
        "source_message_id": "<msg-fail-1@mail>",
    })
    assert retry.status_code == 200, retry.text


def test_from_email_rejects_a_bad_direction_with_400(client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "x@acme-corp.com", "direction": "sideways",
    })
    assert resp.status_code == 400


# ══ 8. Dedup via message-ids ══════════════════════════════════════════════════

def test_message_ids_returns_known_ids_without_a_limit_ceiling(db_session, client):
    # More than the old limit=1000 scan would have carried.
    for i in range(1200):
        _entry(db_session, action_taken=f"Email {i}",
               source_message_id=f"<bulk-{i}@mail>")
    # Entries with no message id must not appear as nulls.
    _entry(db_session, action_taken="Manually logged call", action_type="CALL")

    ids = client.get("/api/activity/message-ids").json()
    assert len(ids) == 1200, "no ceiling — the oldest markers must not fall off"
    assert "<bulk-0@mail>" in ids
    assert "<bulk-1199@mail>" in ids
    assert None not in ids


def test_message_ids_since_filter_narrows_the_window(db_session, client):
    _entry(db_session, log_date=date(2026, 1, 1), source_message_id="<old@mail>")
    _entry(db_session, log_date=date.today(), source_message_id="<new@mail>")
    ids = client.get(
        f"/api/activity/message-ids?since={(date.today() - timedelta(days=7)).isoformat()}"
    ).json()
    assert ids == ["<new@mail>"]


def test_duplicate_source_message_id_on_create_is_409_not_500(db_session, client):
    first = client.post("/api/activity/", json={
        "action_type": "EMAIL", "action_taken": "First",
        "source_message_id": "<dupe@mail>",
    })
    assert first.status_code == 200, first.text
    second = client.post("/api/activity/", json={
        "action_type": "EMAIL", "action_taken": "Second",
        "source_message_id": "<dupe@mail>",
    })
    assert second.status_code == 409, second.text
    assert "already exists" in second.json()["detail"]


def test_editing_a_note_cannot_destroy_the_dedup_marker(db_session, client):
    """The marker used to live in `notes`, the same field "Add Note" edits —
    so editing a note relogged the email on the next run."""
    created = client.post("/api/activity/", json={
        "action_type": "EMAIL", "action_taken": "Emailed someone",
        "source_message_id": "<durable@mail>", "notes": "original note",
    }).json()

    client.patch(f"/api/activity/{created['id']}/notes",
                 json={"notes": "Jack rewrote this note entirely"})

    ids = client.get("/api/activity/message-ids").json()
    assert "<durable@mail>" in ids, "the marker must survive a note edit"


# ══ 9. Triage maintains itself ════════════════════════════════════════════════

def test_logging_a_call_triages_the_contact_and_their_company(db_session, client):
    company = _company(db_session, business_id="CO-800", auto_created=True, triaged=False)
    contact = _contact(db_session, name="Auto Person", company_id=company.id,
                       auto_created=True, triaged=False)
    assert contact.triaged is False and company.triaged is False

    resp = client.post("/api/activity/", json={
        "action_type": "CALL",
        "action_taken": "Called Auto Person",
        "contact_id": contact.id,
        "channel": "call",
    })
    assert resp.status_code == 200, resp.text

    db_session.refresh(contact)
    db_session.refresh(company)
    assert contact.triaged is True
    assert company.triaged is True, "triaging a contact triages their company"


def test_changing_a_stage_triages(db_session, client):
    company = _company(db_session, business_id="CO-801", triaged=False)
    contact = _contact(db_session, name="Stage Triage", company_id=company.id,
                       triaged=False)
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Interested"})
    db_session.refresh(contact)
    db_session.refresh(company)
    assert contact.triaged is True
    assert company.triaged is True


def test_setting_a_next_touch_date_triages(db_session, client):
    contact = _contact(db_session, name="Date Triage", triaged=False)
    client.patch(f"/api/contacts/{contact.id}",
                 json={"next_touch_date": date.today().isoformat()})
    db_session.refresh(contact)
    assert contact.triaged is True


def test_adding_a_fact_triages(db_session, client):
    contact = _contact(db_session, name="Fact Triage", triaged=False)
    resp = client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "Prefers Tysons",
    })
    assert resp.status_code == 200, resp.text
    db_session.refresh(contact)
    assert contact.triaged is True


def test_default_list_shows_triaged_only_and_untriaged_stay_searchable(db_session, client):
    _contact(db_session, name="Shown Person", triaged=True)
    _contact(db_session, name="Hidden Person", triaged=False, auto_created=True)

    shown = client.get("/api/contacts/?triaged=true").json()
    assert [r["name"] for r in shown] == ["Shown Person"]

    behind_toggle = client.get("/api/contacts/?triaged=false").json()
    assert [r["name"] for r in behind_toggle] == ["Hidden Person"]

    # Untriaged records are fully searchable from the moment they exist.
    found = client.get("/api/contacts/search?q=hidden").json()
    assert [c["name"] for c in found] == ["Hidden Person"]


def test_manual_triage_toggle_works_in_both_directions(db_session, client):
    contact = _contact(db_session, name="Manual Toggle", triaged=False)
    client.patch(f"/api/contacts/{contact.id}", json={"triaged": True})
    db_session.refresh(contact)
    assert contact.triaged is True
    client.patch(f"/api/contacts/{contact.id}", json={"triaged": False})
    db_session.refresh(contact)
    assert contact.triaged is False, "an explicit untriage must not be re-triaged"


# ══ 10. Conflicts ═════════════════════════════════════════════════════════════

def _seed_conflict(db_session, client, company):
    return client.post(f"/api/contacts/conflicts/{company.id}/report", json={
        "field": "lease_expiry",
        "value": "2028-06-30",
        "reported_at": "2026-09-09",
    })


def test_rejecting_a_conflict_leaves_the_company_field_and_sets_the_marker(db_session, client):
    company = _company(db_session, name="Acme", business_id="CO-900",
                       lease_expiry_date=date(2027, 6, 30))
    contact = _contact(db_session, name="Joe Claimer", company_id=company.id)
    entry = _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
                   action_taken="Joe said the lease runs to 2028 on the Sept 9 call")
    fact = client.post("/api/contacts/facts", json={
        "contact_id": contact.id,
        "fact_text": "Says their lease actually runs to mid-2028",
        "source_entry_id": entry.id,
    }).json()

    _seed_conflict(db_session, client, company)
    pending = client.get(f"/api/contacts/conflicts/{company.id}").json()
    assert len(pending) == 1
    assert pending[0]["reported_value"] == "2028-06-30"
    assert pending[0]["verified_value"] == "2027-06-30"

    resp = client.post(f"/api/contacts/conflicts/{company.id}/lease_expiry/reject")
    assert resp.status_code == 200, resp.text

    db_session.refresh(company)
    assert company.lease_expiry_date == date(2027, 6, 30), "reject must not write"
    assert company.has_data_conflict is True

    # The claim stays on the contact's thread.
    facts = client.get(f"/api/contacts/facts?contact_id={contact.id}").json()
    assert any(f["id"] == fact["id"] and f["is_active"] for f in facts)

    # And it never re-prompts.
    assert client.get(f"/api/contacts/conflicts/{company.id}").json() == []


def test_accepting_a_conflict_writes_the_company_field(db_session, client):
    company = _company(db_session, name="Acme", business_id="CO-901",
                       lease_expiry_date=date(2027, 6, 30))
    _seed_conflict(db_session, client, company)

    resp = client.post(f"/api/contacts/conflicts/{company.id}/lease_expiry/accept")
    assert resp.status_code == 200, resp.text
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2028, 6, 30)
    assert client.get(f"/api/contacts/conflicts/{company.id}").json() == []


def test_reporting_a_claim_never_writes_the_verified_field(db_session, client):
    company = _company(db_session, name="Acme", business_id="CO-902",
                       lease_expiry_date=date(2027, 6, 30),
                       current_sf_occupied=11000, current_rent_psf=42.0)
    for field, value in (("lease_expiry", "2028-06-30"), ("sf", "15000"),
                         ("rent_psf", "51.5")):
        client.post(f"/api/contacts/conflicts/{company.id}/report",
                    json={"field": field, "value": value})
    db_session.refresh(company)
    # Nothing silent: the verified columns are untouched until an accept.
    assert company.lease_expiry_date == date(2027, 6, 30)
    assert company.current_sf_occupied == 11000
    assert company.current_rent_psf == 42.0
    assert len(client.get(f"/api/contacts/conflicts/{company.id}").json()) == 3


def test_conflict_marker_surfaces_in_the_thread_header(db_session, client):
    company = _company(db_session, name="Acme", business_id="CO-903",
                       lease_expiry_date=date(2027, 6, 30))
    contact = _contact(db_session, name="Header Person", company_id=company.id)
    _seed_conflict(db_session, client, company)
    client.post(f"/api/contacts/conflicts/{company.id}/lease_expiry/reject")

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["has_data_conflict"] is True


def test_conflict_endpoints_are_400_not_500_on_bad_input(db_session, client):
    company = _company(db_session, business_id="CO-904")
    assert client.post(f"/api/contacts/conflicts/{company.id}/report",
                       json={"field": "nonsense", "value": "x"}).status_code == 400
    assert client.post(f"/api/contacts/conflicts/{company.id}/report",
                       json={"field": "lease_expiry", "value": "not-a-date"}).status_code == 400
    assert client.post(
        f"/api/contacts/conflicts/{company.id}/lease_expiry/accept"
    ).status_code == 400   # nothing reported to accept
    assert client.get("/api/contacts/conflicts/999999").status_code == 404


# ══ 11. Facts ═════════════════════════════════════════════════════════════════

def test_a_superseded_fact_leaves_the_active_set_but_stays_retrievable(db_session, client):
    contact = _contact(db_session, name="Fact Holder")
    old = client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "Board is split on relocating",
    }).json()

    new = client.post(f"/api/contacts/facts/{old['id']}/supersede", json={
        "fact_text": "Board has agreed to relocate",
    }).json()

    active = client.get(f"/api/contacts/facts?contact_id={contact.id}").json()
    active_ids = [f["id"] for f in active]
    assert old["id"] not in active_ids, "a superseded fact must leave the active set"
    assert new["id"] in active_ids, "newest active fact wins"

    everything = client.get(
        f"/api/contacts/facts?contact_id={contact.id}&include_superseded=true"
    ).json()
    superseded = next(f for f in everything if f["id"] == old["id"])
    assert superseded["is_active"] is False
    assert superseded["superseded_by_id"] == new["id"], "still retrievable, with its successor"


def test_facts_are_clickable_through_to_their_source_entry(db_session, client):
    company = _company(db_session, business_id="CO-950")
    contact = _contact(db_session, name="Sourced", company_id=company.id)
    entry = _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
                   action_taken="Call where they said they prefer Tysons")
    client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "Prefers Tysons",
        "source_entry_id": entry.id,
    })
    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["relationship_lines"][0]["source_entry_id"] == entry.id


def test_deleting_a_fact_removes_it_and_restores_what_it_superseded(db_session, client):
    contact = _contact(db_session, name="Delete Me")
    old = client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "Responds to text not email",
    }).json()
    new = client.post(f"/api/contacts/facts/{old['id']}/supersede", json={
        "fact_text": "Responds to email now",
    }).json()

    resp = client.delete(f"/api/contacts/facts/{new['id']}")
    assert resp.status_code == 200, resp.text

    active = client.get(f"/api/contacts/facts?contact_id={contact.id}").json()
    assert [f["id"] for f in active] == [old["id"]], (
        "deleting a mistaken correction must not bury the fact it replaced"
    )
    assert db_session.query(ContactFact).filter(ContactFact.id == new["id"]).first() is None


def test_empty_fact_text_is_400_not_500(db_session, client):
    contact = _contact(db_session)
    assert client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "   ",
    }).status_code == 400


# ══ 12. Contact creation, search and resolve ══════════════════════════════════

def test_create_contact_and_resolve_by_email(db_session, client):
    resp = client.post("/api/contacts/", json={
        "name": "Dana Lee", "email": "Dana.Lee@Umbrella.com", "title": "COO",
        "contact_type": "counterparty",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "dana.lee@umbrella.com"

    # Email is the identifier and matching is case-insensitive.
    found = client.post("/api/contacts/resolve",
                        json={"email": "DANA.LEE@UMBRELLA.COM", "name": "Whoever"}).json()
    assert found["found"] is True
    assert found["contact"]["name"] == "Dana Lee"

    missing = client.post("/api/contacts/resolve",
                          json={"email": "nobody@nowhere.com", "name": "Dana Lee"}).json()
    assert missing["found"] is False, "a name must never be enough to match"
    assert missing["contact"] is None


def test_contacts_are_created_when_a_name_is_known_not_when_they_reply(db_session, client):
    resp = client.post("/api/contacts/", json={"name": "Never Replies"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["responded"] is False
    # They persist, and stay listed.
    rows = client.get("/api/contacts/").json()
    assert "Never Replies" in [r["name"] for r in rows]


def test_duplicate_email_is_409_not_500(db_session, client):
    client.post("/api/contacts/", json={"name": "First", "email": "dup@acme-corp.com"})
    second = client.post("/api/contacts/", json={"name": "Second", "email": "dup@acme-corp.com"})
    assert second.status_code == 409


def test_contacts_with_no_email_do_not_collide(db_session, client):
    assert client.post("/api/contacts/", json={"name": "No Email One"}).status_code == 200
    assert client.post("/api/contacts/", json={"name": "No Email Two"}).status_code == 200


def test_list_sorts_overdue_next_touch_first(db_session, client):
    _contact(db_session, name="No Date", triaged=True)
    _contact(db_session, name="Future", triaged=True,
             next_touch_date=date.today() + timedelta(days=30))
    _contact(db_session, name="Overdue", triaged=True,
             next_touch_date=date.today() - timedelta(days=10))
    _contact(db_session, name="Due Today", triaged=True, next_touch_date=date.today())

    names = [r["name"] for r in client.get("/api/contacts/").json()]
    assert names[:2] == ["Overdue", "Due Today"], f"overdue first, soonest first; got {names}"
    rows = {r["name"]: r for r in client.get("/api/contacts/").json()}
    assert rows["Overdue"]["overdue"] is True
    assert rows["Future"]["overdue"] is False


def test_list_filters_on_contact_type_and_stage(db_session, client):
    _contact(db_session, name="A Tenant", contact_type="tenant", stage="Replied", triaged=True)
    _contact(db_session, name="A Broker", contact_type="counterparty", stage="Sent", triaged=True)

    tenants = client.get("/api/contacts/?contact_type=tenant").json()
    assert [r["name"] for r in tenants] == ["A Tenant"]
    replied = client.get("/api/contacts/?stage=Replied").json()
    assert [r["name"] for r in replied] == ["A Tenant"]


def test_list_does_not_run_a_query_per_contact(db_session, client):
    """The list endpoint must not degrade as contacts accumulate."""
    company = _company(db_session, business_id="CO-990")
    for i in range(40):
        c = _contact(db_session, name=f"Person {i}", company_id=company.id, triaged=True)
        _entry(db_session, contact_id=c.id, company_stamp_id=company.id,
               action_taken=f"Touched Person {i}")

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

    assert len(rows) == 40
    assert len(seen) <= 5, (
        f"expected a small constant number of queries for 40 contacts, ran {len(seen)}"
    )


def test_missing_contact_is_404_not_500(client):
    assert client.get("/api/contacts/999999").status_code == 404
    assert client.get("/api/contacts/999999/timeline").status_code == 404
    assert client.patch("/api/contacts/999999", json={"name": "x"}).status_code == 404


def test_search_with_an_empty_query_returns_nothing_rather_than_everything(db_session, client):
    _contact(db_session, name="Someone")
    assert client.get("/api/contacts/search?q=").json() == []


# ══ 13. Existing activity callers keep working unchanged ══════════════════════

def test_existing_create_payload_still_works_with_no_contact_fields(client):
    """The Outreach Draft modal and the CLI post exactly this shape."""
    resp = client.post("/api/activity/", json={
        "action_type": "EMAIL",
        "action_taken": "Emailed John Smith re Acme Corp",
        "outreach_type": "tenant_match",
        "target_type": "tenant",
        "contact_method": "email",
        "subject": "Your Tysons lease",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contact_id"] is None
    assert body["direction"] == "outbound"       # sensible default
    assert body["channel"] == "email"            # inferred from action_type
    assert body["outreach_type"] == "tenant_match"
    # The regex fallback still names the contact on an unbackfilled entry.
    assert body["contact_name"] == "John Smith"


def test_discovery_fields_round_trip_but_stay_inert(db_session, client):
    company = _company(db_session, business_id="CO-995", current_sf_occupied=9000,
                       current_rent_psf=40.0, lease_expiry_date=date(2027, 1, 1))
    contact = _contact(db_session, name="Discovery Person", company_id=company.id)
    resp = client.post("/api/activity/", json={
        "action_type": "CALL",
        "action_taken": "Discovery call",
        "contact_id": contact.id,
        "channel": "call",
        "disc_current_rent_psf": 55.0,
        "disc_current_sf": 20000,
        "disc_lease_expiry": "2029-01-01",
        "disc_decision_timeline": "Board decides in Q1",
        "disc_buildout_needs": "Lab space",
        "disc_decision_maker": "The CFO",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["disc_current_sf"] == 20000
    assert body["disc_decision_maker"] == "The CFO"

    # Nothing was consumed: the company's scoring fields are untouched.
    db_session.refresh(company)
    assert company.current_sf_occupied == 9000
    assert company.current_rent_psf == 40.0
    assert company.lease_expiry_date == date(2027, 1, 1)

    # And they render on the thread.
    timeline = client.get(f"/api/contacts/{contact.id}/timeline").json()
    assert timeline["entries"][0]["disc_buildout_needs"] == "Lab space"


def test_assigning_an_entry_to_a_missing_contact_is_404(db_session, client):
    entry = _entry(db_session, action_taken="Orphan")
    assert client.patch(f"/api/activity/{entry.id}/assign",
                        json={"contact_id": 999999}).status_code == 404


# ══ 14. Counterparty isolation in generated copy ══════════════════════════════

COUNTERPARTY_FACT = "ZZZ Counterparty Secret — the landlord broker will go to 48"


def test_counterparty_thread_text_never_reaches_tenant_facing_copy(db_session, client):
    """Jack's own activity log is unmasked — tenant names, company names and
    property addresses may sit side by side, because he is the only reader.

    Generated output is not. On top of the two existing privacy rules, nothing
    from a counterparty contact's thread may reach a tenant-facing or
    property-facing draft: what a landlord broker tells Jack in confidence must
    never be reflected back at the tenant.
    """
    from app.services import outreach_service as svc

    company = _company(
        db_session, name="Acme Corp", business_id="CO-1000",
        current_submarket="Tysons", current_sf_occupied=11000,
        current_headcount=40, lease_expiry_date=date.today() + timedelta(days=240),
    )
    # A counterparty (landlord broker) whose thread carries a confidential fact.
    broker = _contact(db_session, name="Larry Landlordbroker", company_id=company.id,
                      contact_type="counterparty", triaged=True)
    entry = _entry(db_session, contact_id=broker.id, company_stamp_id=company.id,
                   action_type="CALL", action_taken=COUNTERPARTY_FACT,
                   notes=COUNTERPARTY_FACT, outcome=COUNTERPARTY_FACT)
    client.post("/api/contacts/facts", json={
        "contact_id": broker.id, "fact_text": COUNTERPARTY_FACT,
        "source_entry_id": entry.id,
    })
    client.post(f"/api/contacts/conflicts/{company.id}/report", json={
        "field": "rent_psf", "value": "48.0", "source_entry_id": entry.id,
    })

    captured = {}

    class _FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["messages"] = kwargs.get("messages", [])
            return type("R", (), {"choices": [_FakeChoice(
                "SUBJECT: Your Tysons lease\nEMAIL:\nHi there,\n\nJack Zamer\n"
            )]})()

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.chat = type("C", (), {"completions": _FakeCompletions()})()

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
         patch("openai.OpenAI", _FakeClient):
        client.post(f"/api/companies/{company.company_id}/draft-outreach")

    prompt = " ".join(
        str(m.get("content", "")) for m in captured.get("messages", [])
    ).lower()
    assert prompt, "the generator was never reached — the test proves nothing"
    assert "zzz counterparty secret" not in prompt, (
        "PRIVACY LEAK: a counterparty contact's thread text reached the "
        "tenant-facing outreach prompt"
    )
    assert "larry landlordbroker" not in prompt, (
        "PRIVACY LEAK: a counterparty contact's name reached the tenant-facing prompt"
    )


def test_counterparty_facts_are_not_in_the_generator_input_dict(db_session, client):
    """Structural guard: the tenant-side generator is fed a fixed whitelist of
    Company columns. If someone later widens it to include thread content, this
    fails before any copy is generated."""
    from app.api.routes import companies as companies_routes
    import inspect

    source = inspect.getsource(companies_routes.draft_outreach)
    for forbidden in ("ContactFact", "contact_facts", "activity_logs", "ActivityLog",
                      "contact.name", "Contact"):
        assert forbidden not in source, (
            f"draft_outreach must not read {forbidden}: contact-thread content "
            f"has no path into tenant-facing generation"
        )
    # The conversation-sourced claim columns must not be fed in either — only a
    # value Jack has accepted (which lands on the verified column) may be used.
    assert "contact_reported" not in source


# ══ Phase two ═════════════════════════════════════════════════════════════════
#
# Everything below locks the phase-two behaviours: the manual inbound rule,
# stage changes rendering as collapsible dividers rather than entries, the edit
# and delete surfaces, and the backfill. In-memory inputs only — the backfill's
# model step is mocked, so no test here makes a network call.


def _stage_events(db, contact_id):
    """The divider rows on a contact, oldest first."""
    return (
        db.query(ActivityLog)
        .filter(
            ActivityLog.contact_id == contact_id,
            ActivityLog.action_type == "STAGE_CHANGE",
        )
        .order_by(ActivityLog.id.asc())
        .all()
    )


# ── 1. A manual inbound entry advances the stage ─────────────────────────────

def test_manual_inbound_entry_sets_responded_and_moves_sent_to_replied(db_session, client):
    """Logging a reply by hand left the header reading "They replied — owed a
    response" while the stage pill still said Sent. Same rule as from-email now,
    through the same shared code path."""
    contact = _contact(db_session, "Dana Reply", email="dana@acme.com", stage="Sent")

    r = client.post("/api/activity/", json={
        "action_type": "EMAIL",
        "action_taken": "Dana emailed back about the Reston space",
        "contact_id": contact.id,
        "direction": "inbound",
        "channel": "email",
    })
    assert r.status_code == 200, r.text

    db_session.refresh(contact)
    assert contact.responded is True
    assert contact.stage == "Replied"


def test_manual_inbound_does_not_regress_a_contact_already_in_play(db_session, client):
    """Sent to Replied only. A contact further down the pipeline is never pulled
    backwards by logging an inbound touch."""
    contact = _contact(db_session, "Ed Deep", email="ed@acme.com", stage="In Play")

    client.post("/api/activity/", json={
        "action_type": "CALL",
        "action_taken": "Ed called back",
        "contact_id": contact.id,
        "direction": "inbound",
        "channel": "call",
    })

    db_session.refresh(contact)
    assert contact.stage == "In Play"     # not regressed
    assert contact.responded is True      # but the reply is still recorded


@pytest.mark.parametrize("stage", ["Interested", "In Play", "Not Interested", "Dormant"])
def test_manual_inbound_never_regresses_any_later_stage(db_session, client, stage):
    slug = stage.replace(" ", "").lower()
    contact = _contact(db_session, f"Person {stage}", email=f"{slug}@acme.com", stage=stage)
    client.post("/api/activity/", json={
        "action_type": "EMAIL", "action_taken": "they wrote in",
        "contact_id": contact.id, "direction": "inbound",
    })
    db_session.refresh(contact)
    assert contact.stage == stage


def test_manual_outbound_entry_does_not_set_responded(db_session, client):
    contact = _contact(db_session, "Fay Quiet", email="fay@acme.com", stage="Sent")
    client.post("/api/activity/", json={
        "action_type": "EMAIL", "action_taken": "Emailed Fay",
        "contact_id": contact.id, "direction": "outbound",
    })
    db_session.refresh(contact)
    assert contact.responded is False
    assert contact.stage == "Sent"


# ── 2. Stage changes are dividers, and they collapse ─────────────────────────

def test_a_stage_change_writes_a_divider_not_an_entry(db_session, client):
    """No direction, no channel, not outreach, and never stamped to a company —
    the old rows read as OUT / OTHER outreach cards."""
    contact = _contact(db_session, "Gil Pill", stage="Sent")
    company = _company(db_session, "Gil Corp", "CO-GIL")
    contact.company_id = company.id
    db_session.commit()

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    events = _stage_events(db_session, contact.id)
    assert len(events) == 1
    event = events[0]
    assert event.action_type == "STAGE_CHANGE"
    assert event.stage_from == "Sent"
    assert event.stage_to == "Replied"
    assert event.direction is None
    assert event.channel is None
    assert event.company_stamp_id is None      # stays off the company timeline


def test_two_stage_changes_in_the_window_collapse_to_the_net_move(db_session, client):
    """Six clicks used to produce six cards. Consecutive changes with no real
    entry between them resolve to one event showing where it ended up."""
    contact = _contact(db_session, "Hal Burst", stage="Sent")

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Interested"})

    events = _stage_events(db_session, contact.id)
    assert len(events) == 1, "two changes in the window must be one divider"
    assert events[0].stage_from == "Sent"
    assert events[0].stage_to == "Interested"     # the net move, not the middle


def test_a_long_burst_of_stage_changes_collapses_to_one_divider(db_session, client):
    """The reported case: clicking along the whole pill row leaves one line."""
    contact = _contact(db_session, "Ivy Clicks", stage="Sent")
    for stage in ["Replied", "Interested", "In Play", "Not Interested", "Dormant"]:
        client.patch(f"/api/contacts/{contact.id}", json={"stage": stage})

    events = _stage_events(db_session, contact.id)
    assert len(events) == 1
    assert events[0].stage_from == "Sent"
    assert events[0].stage_to == "Dormant"


def test_a_stage_change_returning_to_the_start_leaves_no_event_at_all(db_session, client):
    """A misclick corrected is not history."""
    contact = _contact(db_session, "Jo Misclick", stage="Sent")

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Sent"})

    assert _stage_events(db_session, contact.id) == []
    db_session.refresh(contact)
    assert contact.stage == "Sent"


def test_a_round_trip_through_several_stages_also_leaves_no_event(db_session, client):
    contact = _contact(db_session, "Kim Roundtrip", stage="Replied")
    for stage in ["Interested", "In Play", "Dormant", "Replied"]:
        client.patch(f"/api/contacts/{contact.id}", json={"stage": stage})
    assert _stage_events(db_session, contact.id) == []


def test_a_real_entry_between_two_stage_changes_stops_the_collapse(db_session, client):
    """A touch means the thread moved on, so the next stage change is a new
    decision rather than a correction of the last one."""
    contact = _contact(db_session, "Lee Between", stage="Sent")

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    client.post("/api/activity/", json={
        "action_type": "CALL", "action_taken": "Spoke to Lee",
        "contact_id": contact.id, "direction": "outbound",
    })
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Interested"})

    events = _stage_events(db_session, contact.id)
    assert len(events) == 2
    assert [e.stage_to for e in events] == ["Replied", "Interested"]


def test_stage_changes_outside_the_window_do_not_collapse(db_session, client):
    """Thirty minutes is the window. Past it, two changes are two decisions."""
    from datetime import datetime as _dt, timedelta as _td

    contact = _contact(db_session, "Mo Later", stage="Sent")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    old = _stage_events(db_session, contact.id)[0]
    old.created_at = _dt.utcnow() - _td(minutes=31)
    db_session.commit()

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Interested"})

    events = _stage_events(db_session, contact.id)
    assert len(events) == 2
    assert [e.stage_to for e in events] == ["Replied", "Interested"]


def test_a_return_to_start_outside_the_window_keeps_both_events(db_session, client):
    """Only a burst is a misclick. A day later, going back is real history."""
    from datetime import datetime as _dt, timedelta as _td

    contact = _contact(db_session, "Ned Slow", stage="Sent")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    old = _stage_events(db_session, contact.id)[0]
    old.created_at = _dt.utcnow() - _td(hours=30)
    db_session.commit()

    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Sent"})
    assert len(_stage_events(db_session, contact.id)) == 2


# ── 3. entry_count means real touches ────────────────────────────────────────

def test_entry_count_excludes_stage_change_events(db_session, client):
    contact = _contact(db_session, "Ola Count", stage="Sent")
    _entry(db_session, contact_id=contact.id, action_taken="Real touch one")
    _entry(db_session, contact_id=contact.id, action_taken="Real touch two")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    assert len(_stage_events(db_session, contact.id)) == 1   # the divider exists

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["entry_count"] == 2, "entry count means real touches"

    row = next(r for r in client.get("/api/contacts/").json() if r["id"] == contact.id)
    assert row["entry_count"] == 2


def test_the_contact_list_summary_is_never_a_stage_change(db_session, client):
    """A pill click must not become the last thing that happened with a person."""
    contact = _contact(db_session, "Pia Summary", stage="Sent")
    _entry(db_session, contact_id=contact.id, action_taken="Left a voicemail")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    row = next(r for r in client.get("/api/contacts/").json() if r["id"] == contact.id)
    assert row["latest_entry_summary"] == "Left a voicemail"


def test_a_stage_change_does_not_become_the_open_loop(db_session, client):
    """The divider has no direction, so letting it into the header produced
    'Awaiting their reply' off the back of a pill click."""
    contact = _contact(db_session, "Quinn Loop", stage="Sent")
    _entry(db_session, contact_id=contact.id, action_taken="They wrote in",
           direction="inbound")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["open_loop"] == "They replied — owed a response"


def test_stage_change_dividers_stay_off_the_company_timeline(db_session, client):
    company = _company(db_session, "Divider Co", "CO-DIV")
    contact = _contact(db_session, "Rae Stamp", company_id=company.id, stage="Sent")
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           action_taken="A real conversation")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    page = client.get(f"/api/companies/{company.company_id}/timeline").json()
    assert page["total"] == 1
    assert page["entries"][0]["action_taken"] == "A real conversation"


def test_stage_change_dividers_still_appear_on_the_contact_timeline(db_session, client):
    """Excluded from counts, not from the thread — the transition is shown."""
    contact = _contact(db_session, "Sam Shown", stage="Sent")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Interested"})

    page = client.get(f"/api/contacts/{contact.id}/timeline").json()
    divider = next(e for e in page["entries"] if e["action_type"] == "STAGE_CHANGE")
    assert divider["stage_from"] == "Sent"
    assert divider["stage_to"] == "Interested"


# ── 4. Editing ───────────────────────────────────────────────────────────────

def test_editing_a_contacts_company_leaves_every_existing_stamp_unchanged(db_session, client):
    """Current employment moves; history does not. This is what keeps a
    departed contact's entries on the old company's page."""
    old_co = _company(db_session, "Old Employer", "CO-OLD")
    new_co = _company(db_session, "New Employer", "CO-NEW")
    contact = _contact(db_session, "Tom Moved", company_id=old_co.id)
    ids = [
        _entry(db_session, contact_id=contact.id, company_stamp_id=old_co.id,
               action_taken=f"Touch {i}").id
        for i in range(3)
    ]

    r = client.patch(f"/api/contacts/{contact.id}", json={"company_id": new_co.id})
    assert r.status_code == 200

    db_session.refresh(contact)
    assert contact.company_id == new_co.id
    for entry_id in ids:
        entry = db_session.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
        assert entry.company_stamp_id == old_co.id


def test_editing_every_contact_field_round_trips(db_session, client):
    company = _company(db_session, "Edit Co", "CO-EDIT")
    contact = _contact(db_session, "Una Before", email="before@x.com")

    r = client.patch(f"/api/contacts/{contact.id}", json={
        "name": "Una After", "email": "after@x.com", "phone": "703-555-0100",
        "title": "Head of Ops", "contact_type": "counterparty",
        "company_id": company.id, "stage": "Interested",
        "next_touch_date": "2026-10-01", "triaged": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Una After"
    assert body["email"] == "after@x.com"
    assert body["phone"] == "703-555-0100"
    assert body["title"] == "Head of Ops"
    assert body["contact_type"] == "counterparty"
    assert body["company_id"] == company.id
    assert body["stage"] == "Interested"
    assert body["next_touch_date"] == "2026-10-01"
    assert body["triaged"] is True


def test_editing_an_entrys_action_taken_does_not_alter_its_links(db_session, client):
    """An edit corrects what an entry says, never who it belongs to."""
    company = _company(db_session, "Link Co", "CO-LINK")
    other = _company(db_session, "Other Co", "CO-OTHER")
    contact = _contact(db_session, "Vic Edit", company_id=other.id)
    entry = _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
                   action_taken="Origianl typo")

    r = client.patch(f"/api/activity/{entry.id}", json={"action_taken": "Original, fixed"})
    assert r.status_code == 200, r.text

    db_session.refresh(entry)
    assert entry.action_taken == "Original, fixed"
    assert entry.contact_id == contact.id
    assert entry.company_stamp_id == company.id


def test_an_entrys_direction_channel_date_and_discovery_are_all_editable(db_session, client):
    contact = _contact(db_session, "Wes Fields")
    entry = _entry(db_session, contact_id=contact.id, direction="outbound",
                   channel="email", action_taken="Something")

    r = client.patch(f"/api/activity/{entry.id}", json={
        "direction": "inbound", "channel": "call", "log_date": "2026-03-04",
        "outcome": "They want to see space",
        "notes": "Call back Tuesday",
        "follow_up_action": "Send comps",
        "disc_current_rent_psf": 42.5, "disc_current_sf": 8200,
        "disc_lease_expiry": "2027-06-30",
        "disc_decision_timeline": "Q1", "disc_buildout_needs": "Open plan",
        "disc_decision_maker": "The two partners",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["direction"] == "inbound"
    assert body["channel"] == "call"
    assert body["log_date"] == "2026-03-04"
    assert body["outcome"] == "They want to see space"
    assert body["notes"] == "Call back Tuesday"
    assert body["follow_up_action"] == "Send comps"
    assert body["disc_current_rent_psf"] == 42.5
    assert body["disc_current_sf"] == 8200
    assert body["disc_lease_expiry"] == "2027-06-30"
    assert body["disc_decision_timeline"] == "Q1"
    assert body["disc_buildout_needs"] == "Open plan"
    assert body["disc_decision_maker"] == "The two partners"


def test_an_edit_can_clear_a_field_it_previously_set(db_session, client):
    contact = _contact(db_session, "Xan Clear")
    entry = _entry(db_session, contact_id=contact.id, action_taken="Something",
                   outcome="A wrong outcome", disc_current_sf=5000)

    r = client.patch(f"/api/activity/{entry.id}", json={
        "clear_fields": ["outcome", "disc_current_sf"],
    })
    assert r.status_code == 200, r.text
    db_session.refresh(entry)
    assert entry.outcome is None
    assert entry.disc_current_sf is None


def test_an_invalid_direction_or_channel_on_an_edit_is_400_not_500(db_session, client):
    entry = _entry(db_session, action_taken="Something")
    assert client.patch(f"/api/activity/{entry.id}",
                        json={"direction": "sideways"}).status_code == 400
    assert client.patch(f"/api/activity/{entry.id}",
                        json={"channel": "telepathy"}).status_code == 400
    assert client.patch(f"/api/activity/{entry.id}",
                        json={"clear_fields": ["contact_id"]}).status_code == 400


def test_a_fact_can_be_edited_in_place(db_session, client):
    contact = _contact(db_session, "Yui Fact")
    fact = client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "brds is split on relocating",
    }).json()

    r = client.patch(f"/api/contacts/facts/{fact['id']}",
                     json={"fact_text": "board is split on relocating"})
    assert r.status_code == 200, r.text
    assert r.json()["fact_text"] == "board is split on relocating"
    assert r.json()["is_active"] is True

    active = client.get("/api/contacts/facts", params={"contact_id": contact.id}).json()
    assert len(active) == 1, "an edit corrects a fact, it does not add one"


def test_editing_a_fact_to_empty_is_400_and_a_missing_fact_is_404(db_session, client):
    contact = _contact(db_session, "Zed Guard")
    fact = client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "something",
    }).json()
    assert client.patch(f"/api/contacts/facts/{fact['id']}",
                        json={"fact_text": "   "}).status_code == 400
    assert client.patch("/api/contacts/facts/999999",
                        json={"fact_text": "x"}).status_code == 404


def test_moving_an_entrys_company_stamp_is_its_own_deliberate_action(db_session, client):
    """company_stamp_id is immutable by ordinary edit; this endpoint is the
    only way it moves."""
    old_co = _company(db_session, "Stamp Old", "CO-SOLD")
    new_co = _company(db_session, "Stamp New", "CO-SNEW")
    contact = _contact(db_session, "Amy Restamp")
    entry = _entry(db_session, contact_id=contact.id, company_stamp_id=old_co.id,
                   action_taken="A conversation")

    r = client.patch(f"/api/activity/{entry.id}/company-stamp",
                     json={"company_id": new_co.id})
    assert r.status_code == 200, r.text
    db_session.refresh(entry)
    assert entry.company_stamp_id == new_co.id
    assert entry.contact_id == contact.id       # who it belongs to is untouched

    assert client.patch(f"/api/activity/{entry.id}/company-stamp",
                        json={"company_id": 999999}).status_code == 404


def test_reassigning_an_entry_to_another_contact_keeps_its_stamp(db_session, client):
    company = _company(db_session, "Keep Co", "CO-KEEP")
    a = _contact(db_session, "Ben From")
    b = _contact(db_session, "Cal To")
    entry = _entry(db_session, contact_id=a.id, company_stamp_id=company.id,
                   action_taken="Misfiled")

    r = client.patch(f"/api/activity/{entry.id}/assign", json={"contact_id": b.id})
    assert r.status_code == 200
    db_session.refresh(entry)
    assert entry.contact_id == b.id
    assert entry.company_stamp_id == company.id


# ── 5. Deleting a contact ────────────────────────────────────────────────────

def test_deleting_a_contact_with_unattach_preserves_the_entries(db_session, client):
    contact = _contact(db_session, "Dee Unattach")
    ids = [_entry(db_session, contact_id=contact.id, action_taken=f"Kept {i}").id
           for i in range(3)]
    client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "a fact",
    })

    r = client.delete(f"/api/contacts/{contact.id}", params={"mode": "unattach"})
    assert r.status_code == 200, r.text
    assert r.json()["entries_unattached"] == 3

    assert db_session.query(Contact).filter(Contact.id == contact.id).first() is None
    for entry_id in ids:
        entry = db_session.query(ActivityLog).filter(ActivityLog.id == entry_id).first()
        assert entry is not None, "the conversation survives the person record"
        assert entry.contact_id is None
    assert db_session.query(ContactFact).filter(
        ContactFact.contact_id == contact.id).count() == 0


def test_deleting_a_contact_with_cascade_removes_the_entries(db_session, client):
    contact = _contact(db_session, "Eli Cascade")
    ids = [_entry(db_session, contact_id=contact.id, action_taken=f"Gone {i}").id
           for i in range(3)]
    client.post("/api/contacts/facts", json={
        "contact_id": contact.id, "fact_text": "a fact",
    })

    r = client.delete(f"/api/contacts/{contact.id}", params={"mode": "cascade"})
    assert r.status_code == 200, r.text
    assert r.json()["entries_deleted"] == 3

    assert db_session.query(Contact).filter(Contact.id == contact.id).first() is None
    for entry_id in ids:
        assert db_session.query(ActivityLog).filter(
            ActivityLog.id == entry_id).first() is None
    assert db_session.query(ContactFact).filter(
        ContactFact.contact_id == contact.id).count() == 0


def test_deleting_a_contact_never_touches_another_contacts_entries(db_session, client):
    keep = _contact(db_session, "Fin Keeper")
    kept = _entry(db_session, contact_id=keep.id, action_taken="Untouched")
    doomed = _contact(db_session, "Gus Doomed")
    _entry(db_session, contact_id=doomed.id, action_taken="Gone")

    client.delete(f"/api/contacts/{doomed.id}", params={"mode": "cascade"})

    survivor = db_session.query(ActivityLog).filter(ActivityLog.id == kept.id).first()
    assert survivor is not None and survivor.contact_id == keep.id


def test_an_unattach_delete_drops_the_dividers_rather_than_orphaning_them(db_session, client):
    """A 'Stage: Sent -> Replied' line detached from the person it described is
    noise in All Activity, not history."""
    contact = _contact(db_session, "Hana Divider", stage="Sent")
    real = _entry(db_session, contact_id=contact.id, action_taken="A real touch")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})
    assert len(_stage_events(db_session, contact.id)) == 1

    client.delete(f"/api/contacts/{contact.id}", params={"mode": "unattach"})

    assert db_session.query(ActivityLog).filter(
        ActivityLog.action_type == "STAGE_CHANGE").count() == 0
    assert db_session.query(ActivityLog).filter(ActivityLog.id == real.id).first() is not None


def test_an_invalid_delete_mode_is_400_and_a_missing_contact_is_404(db_session, client):
    contact = _contact(db_session, "Ida Guard")
    assert client.delete(f"/api/contacts/{contact.id}",
                         params={"mode": "sideways"}).status_code == 400
    assert client.delete("/api/contacts/999999",
                         params={"mode": "cascade"}).status_code == 404


# ── 6. The backfill ──────────────────────────────────────────────────────────
#
# The model step is mocked throughout: _extract_batch is the single seam where
# the script talks to Anthropic, so patching it keeps these tests offline while
# still exercising the real resolution, grouping and write paths.

import sys                                     # noqa: E402
import scripts.backfill_contacts as backfill   # noqa: E402


def _counts(db):
    """A snapshot of everything the backfill could possibly write to."""
    return {
        "contacts": db.query(Contact).count(),
        "companies": db.query(Company).count(),
        "entries": db.query(ActivityLog).count(),
        "linked": db.query(ActivityLog).filter(
            ActivityLog.contact_id.isnot(None)).count(),
        "facts": db.query(ContactFact).count(),
    }


def _no_model(*_args, **_kwargs):
    """Stand-in for a run where the prose step resolves nothing."""
    return []


def _run_report(db, extractor=_no_model, with_key=True):
    """build_report with the model seam patched and the key state controlled."""
    env = {"ANTHROPIC_API_KEY": "test-key"} if with_key else {}
    with patch.dict("os.environ", env, clear=not with_key):
        with patch.object(backfill, "_extract_batch", extractor):
            # The client is only ever handed to _extract_batch, which is mocked.
            with patch.dict("sys.modules", {"anthropic": _FakeAnthropicModule()}):
                return backfill.build_report(db, verbose=False)


class _FakeAnthropicModule:
    """Enough of the anthropic module for the client construction to succeed.
    Nothing on it is ever called — _extract_batch is patched out."""
    @staticmethod
    def Anthropic(**_kwargs):
        return object()


def test_backfill_report_mode_writes_zero_rows(db_session):
    """Report mode reads. It must not create a contact, a company, or touch an
    entry — the whole point is that Jack reviews the mapping first."""
    company = _company(db_session, "Report Co", "CO-REP", email_domain="reportco.com")
    _entry(db_session, action_taken="Emailed Nia Hart (nia@reportco.com) re the Reston space")
    _entry(db_session, action_taken="Called someone, no idea who")
    before = _counts(db_session)

    report = _run_report(db_session)

    assert _counts(db_session) == before, "report mode wrote to the database"
    assert report["total_entries"] == 2
    assert company.id is not None


def test_an_address_in_the_prose_resolves_at_high_confidence(db_session):
    """Email is identity. No model judgment is involved and none is wanted."""
    _entry(db_session, action_taken="Emailed Miriam Miller (miriam@mm-realestate.com), "
                                    "Corcoran McEnearney re 1205 North Pitt Street")

    report = _run_report(db_session)
    proposal = report["proposals"][0]

    assert proposal["source"] == "address"
    assert proposal["confidence"] == "high"
    assert proposal["email"] == "miriam@mm-realestate.com"
    assert proposal["contact_name"] == "Miriam Miller"


def test_an_address_matching_an_existing_contact_reuses_it(db_session):
    existing = _contact(db_session, "Olu Known", email="olu@known.com")
    _entry(db_session, action_taken="Emailed Olu Known (olu@known.com) re the lease")

    report = _run_report(db_session)
    assert report["proposals"][0]["existing_contact_id"] == existing.id
    assert report["contacts"][0]["existing_contact_id"] == existing.id


def test_backfill_write_mode_applies_high_confidence_only(db_session):
    """A low-confidence row is left for Jack. Nothing is created for it."""
    high = _entry(db_session, action_taken="Emailed Pat Sure (pat@sureco.com) re the space")
    low = _entry(db_session, action_taken="Called someone about a thing")

    def extractor(entries, _client):
        return [{
            "entry_id": e.id, "contact_name": "Maybe Person", "company_name": None,
            "email": None, "inbound": False, "confidence": "low",
        } for e in entries]

    report = _run_report(db_session, extractor)
    stats = backfill.apply_report(db_session, report)

    assert stats["entries_linked"] == 1
    assert stats["low_confidence_skipped"] == 1

    db_session.refresh(high)
    db_session.refresh(low)
    assert high.contact_id is not None
    assert low.contact_id is None, "a low-confidence guess must not be written"
    assert db_session.query(Contact).filter(Contact.name == "Maybe Person").count() == 0


def test_backfill_write_mode_is_idempotent(db_session):
    """Run twice, and the second run changes nothing — no duplicate contacts,
    no rewritten rows."""
    _entry(db_session, action_taken="Emailed Rudy Twice (rudy@twiceco.com) re the space")
    _entry(db_session, action_taken="Emailed Rudy Twice (rudy@twiceco.com) again")

    report = _run_report(db_session)
    first = backfill.apply_report(db_session, report)
    after_first = _counts(db_session)

    second = backfill.apply_report(db_session, report)
    after_second = _counts(db_session)

    assert first["entries_linked"] == 2
    assert second["entries_linked"] == 0
    assert second["entries_already_linked"] == 2
    assert second["contacts_created"] == 0
    assert second["companies_created"] == 0
    assert after_second == after_first
    assert db_session.query(Contact).filter(Contact.email == "rudy@twiceco.com").count() == 1


def test_the_backfill_never_rewrites_prose(db_session):
    """It says who an entry was about. It never touches what it says."""
    entry = _entry(
        db_session,
        action_taken="Emailed Sia Prose (sia@proseco.com) re 100 King Street",
        outcome="Outreach sent: Leasing opportunity",
        notes="She mentioned a board meeting in March",
    )
    original = (entry.action_taken, entry.outcome, entry.notes)

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert (entry.action_taken, entry.outcome, entry.notes) == original
    assert entry.contact_id is not None, "it did attach the entry"


def test_an_outlook_marker_migrates_and_is_stripped_without_disturbing_the_note(db_session):
    """The marker sharing a user-editable field is what relogged old emails."""
    entry = _entry(
        db_session,
        action_taken="Emailed Tia Mark (tia@markco.com) re the sublease",
        notes="Wants a March start. [outlook:<AAA111@mail.example.com>] Call Tuesday.",
    )

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert entry.source_message_id == "<AAA111@mail.example.com>"
    assert "outlook:" not in (entry.notes or "")
    assert "Wants a March start." in entry.notes
    assert "Call Tuesday." in entry.notes


def test_a_marker_migrates_even_when_the_entry_resolves_to_nobody(db_session):
    """An unresolved entry still needs its dedup marker out of the note."""
    entry = _entry(db_session, action_taken="Sent something to someone",
                   notes="No idea. [outlook:<BBB222@mail.example.com>]")

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert entry.contact_id is None
    assert entry.source_message_id == "<BBB222@mail.example.com>"
    assert "outlook:" not in (entry.notes or "")


def test_a_duplicate_marker_is_skipped_rather_than_destroying_the_note(db_session):
    """source_message_id is unique. A clash leaves the evidence in place."""
    _entry(db_session, action_taken="First", source_message_id="<DUP@mail.example.com>")
    second = _entry(db_session, action_taken="Second",
                    notes="Keep me. [outlook:<DUP@mail.example.com>]")

    report = _run_report(db_session)
    stats = backfill.apply_report(db_session, report)

    db_session.refresh(second)
    assert stats["markers_skipped"] == 1
    assert second.source_message_id is None
    assert "outlook:" in second.notes


def test_a_free_mail_address_creates_a_contact_with_no_company(db_session):
    """A contact from gmail gets a null company_id — never a company called
    "Gmail"."""
    _entry(db_session, action_taken="Emailed Uma Free (uma.free@gmail.com) re 413 N Lee St")
    companies_before = db_session.query(Company).count()

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    contact = db_session.query(Contact).filter(Contact.email == "uma.free@gmail.com").first()
    assert contact is not None
    assert contact.company_id is None
    assert db_session.query(Company).count() == companies_before, "no company was invented"


@pytest.mark.parametrize("addr", [
    "a@gmail.com", "b@outlook.com", "c@yahoo.com", "d@hotmail.com",
    "e@icloud.com", "f@aol.com", "g@proton.me",
])
def test_no_free_mail_domain_creates_a_company(db_session, addr):
    _entry(db_session, action_taken=f"Emailed Someone Named ({addr}) re a space")
    before = db_session.query(Company).count()
    report = _run_report(db_session)
    backfill.apply_report(db_session, report)
    assert db_session.query(Company).count() == before


def test_a_business_domain_matching_an_existing_company_reuses_it(db_session):
    """Domain match first, exactly as the from-email path resolves it."""
    existing = _company(db_session, "Corcoran McEnearney", "CO-CORC",
                        email_domain="mm-realestate.com")
    _entry(db_session, action_taken="Emailed Vik Match (vik@mm-realestate.com) re a space")
    before = db_session.query(Company).count()

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    assert db_session.query(Company).count() == before, "no duplicate company"
    contact = db_session.query(Contact).filter(Contact.email == "vik@mm-realestate.com").first()
    assert contact.company_id == existing.id
    entry = db_session.query(ActivityLog).filter(
        ActivityLog.contact_id == contact.id).first()
    assert entry.company_stamp_id == existing.id


def test_where_no_company_resolves_the_stamp_stays_null(db_session):
    """None is invented. A null stamp is the honest answer."""
    def extractor(entries, _client):
        return [{
            "entry_id": e.id, "contact_name": "Wen Nocompany", "company_name": None,
            "email": None, "inbound": False, "confidence": "high",
        } for e in entries]

    entry = _entry(db_session, action_taken="Spoke to Wen about their lease")
    report = _run_report(db_session, extractor)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert entry.contact_id is not None
    assert entry.company_stamp_id is None


def test_backfilled_contacts_and_companies_are_auto_created_and_untriaged(db_session):
    _entry(db_session, action_taken="Emailed Xia New (xia@brandnewco.com) re a space")

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    contact = db_session.query(Contact).filter(Contact.email == "xia@brandnewco.com").first()
    assert contact.auto_created is True
    assert contact.triaged is False

    company = db_session.query(Company).filter(
        Company.email_domain == "brandnewco.com").first()
    assert company is not None
    assert company.auto_created is True
    assert company.triaged is False
    assert company.company_type is None, "the backfill does not guess a company type"


def test_an_extracted_first_name_lands_on_the_contact_resolved_by_address(db_session):
    """"Miriam" and "Miriam Miller" are one record, not two — the name is
    matched against what the addresses already pinned down."""
    _entry(db_session, action_taken="Emailed Miriam Miller (miriam@mm-realestate.com) re a space")
    prose_entry = _entry(db_session, action_taken="Followed up with her about the tour")

    def extractor(entries, _client):
        return [{
            "entry_id": e.id, "contact_name": "Miriam Miller", "company_name": None,
            "email": None, "inbound": False, "confidence": "high",
        } for e in entries]

    report = _run_report(db_session, extractor)
    backfill.apply_report(db_session, report)

    assert db_session.query(Contact).filter(
        Contact.name.like("Miriam%")).count() == 1, "one person, one record"
    db_session.refresh(prose_entry)
    assert prose_entry.contact_id is not None


def test_similar_names_are_reported_as_duplicates_and_never_merged(db_session):
    """Two similar names are two people until a human says otherwise."""
    _entry(db_session, action_taken="Emailed Robert Blumel (rob@blumelco.com) re a space")
    _entry(db_session, action_taken="Emailed Bob Blumel (bob@blumelco.com) re a space")

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    assert db_session.query(Contact).count() == 2, "similar names must not merge"
    names = {c.name for c in db_session.query(Contact).all()}
    assert names == {"Robert Blumel", "Bob Blumel"}


def test_an_exact_repeated_name_is_flagged_for_review(db_session):
    def extractor(entries, _client):
        return [{
            "entry_id": e.id,
            "contact_name": "Dana Reed" if "first" in e.action_taken else "Dana Reed Jr",
            "company_name": None, "email": None, "inbound": False,
            "confidence": "high",
        } for e in entries]

    _entry(db_session, action_taken="Called about the first thing")
    _entry(db_session, action_taken="Called about the second thing")

    report = _run_report(db_session, extractor)
    assert report["suspected_duplicates"], "near-identical names must be surfaced"
    pair = report["suspected_duplicates"][0]
    assert {pair["a"]["name"], pair["b"]["name"]} == {"Dana Reed", "Dana Reed Jr"}


def test_an_inbound_entry_is_marked_and_the_contact_marked_responded(db_session):
    def extractor(entries, _client):
        return [{
            "entry_id": e.id, "contact_name": "Yara Inbound",
            "company_name": None, "email": "yara@inboundco.com",
            "inbound": True, "confidence": "high",
        } for e in entries]

    entry = _entry(db_session, action_taken="Yara replied about the Reston space",
                   direction="outbound")
    report = _run_report(db_session, extractor)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert entry.direction == "inbound"
    contact = db_session.query(Contact).filter(Contact.email == "yara@inboundco.com").first()
    assert contact.responded is True


def test_the_backfill_never_flips_an_entry_to_outbound(db_session):
    """Only ever set inbound. A hand-marked inbound entry is not overwritten by
    a prose heuristic that disagrees."""
    entry = _entry(db_session, action_taken="Emailed Zoe Fixed (zoe@fixedco.com) re a space",
                   direction="inbound")

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    db_session.refresh(entry)
    assert entry.direction == "inbound"


def test_a_contacts_stage_comes_from_their_most_recent_entry(db_session):
    _entry(db_session, action_taken="Emailed Ana Stage (ana@stageco.com) re a space",
           log_date=date.today() - timedelta(days=30), stage="Sent")
    _entry(db_session, action_taken="Emailed Ana Stage (ana@stageco.com) again",
           log_date=date.today(), stage="Interested")

    report = _run_report(db_session)
    backfill.apply_report(db_session, report)

    contact = db_session.query(Contact).filter(Contact.email == "ana@stageco.com").first()
    assert contact.stage == "Interested"


def test_a_contact_with_no_stage_on_any_entry_defaults_to_sent(db_session):
    _entry(db_session, action_taken="Emailed Bo Default (bo@defaultco.com) re a space")
    report = _run_report(db_session)
    backfill.apply_report(db_session, report)
    contact = db_session.query(Contact).filter(Contact.email == "bo@defaultco.com").first()
    assert contact.stage == "Sent"


def test_without_an_api_key_address_matching_runs_and_the_rest_is_unresolved(db_session):
    """No crash, and no silent skip — the unresolved entries are named as
    unresolved so the gap is visible."""
    _entry(db_session, action_taken="Emailed Cy Keyed (cy@keyedco.com) re a space")
    _entry(db_session, action_taken="Called somebody about something")

    report = _run_report(db_session, with_key=False)

    assert report["model_available"] is False
    by_conf = {p["confidence"] for p in report["proposals"]}
    assert "high" in by_conf
    unresolved = [p for p in report["proposals"] if p["confidence"] == "unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["source"] == "skipped-no-api-key"


def test_a_model_failure_part_way_records_what_resolved_and_marks_it_partial(db_session):
    """It exits cleanly with what it has. It never leaves a partial write."""
    _entry(db_session, action_taken="Emailed Di Safe (di@safeco.com) re a space")
    _entry(db_session, action_taken="Called somebody about something")
    before = _counts(db_session)

    def boom(_entries, _client):
        raise RuntimeError("rate limited")

    report = _run_report(db_session, boom)

    assert report["partial"] is True
    assert "rate limited" in report["model_error"]
    assert any(p["confidence"] == "high" for p in report["proposals"])
    assert _counts(db_session) == before, "a failed run must write nothing"


def test_write_mode_refuses_a_partial_report(db_session, tmp_path):
    """A report that does not cover every entry is not what Jack reviewed."""
    import json as _json

    _entry(db_session, action_taken="Emailed Eve Partial (eve@partialco.com) re a space")

    def boom(_entries, _client):
        raise RuntimeError("connection reset")

    _entry(db_session, action_taken="Called somebody")
    report = _run_report(db_session, boom)
    assert report["partial"] is True

    path = tmp_path / "backfill_report.json"
    path.write_text(_json.dumps(report), encoding="utf-8")

    before = _counts(db_session)
    with patch.object(backfill, "REPORT_PATH", path),             patch.object(sys, "argv", ["backfill_contacts", "--confirm"]),             patch.object(backfill, "make_session", lambda _p: db_session),             patch.object(backfill, "require_contact_schema", lambda _p: None),             patch.object(backfill, "load_env", lambda: None),             patch.object(backfill, "resolve_db_path", lambda _o: "x.db"):
        rc = backfill.main()
    assert rc == 1
    assert _counts(db_session) == before


def test_the_report_covers_every_unattached_entry(db_session):
    """Nothing falls off the list silently."""
    ids = [
        _entry(db_session, action_taken=f"Emailed P{i} (p{i}@coveredco.com) re a space").id
        for i in range(5)
    ]
    ids += [_entry(db_session, action_taken=f"Unclear note {i}").id for i in range(3)]

    report = _run_report(db_session)

    assert report["total_entries"] == len(ids)
    assert report["entries_missing"] == []
    assert {p["entry_id"] for p in report["proposals"]} == set(ids)


def test_an_already_attached_entry_is_never_reconsidered(db_session):
    """The backfill looks only at entries with no contact."""
    contact = _contact(db_session, "Fay Attached", email="fay@attachedco.com")
    _entry(db_session, contact_id=contact.id, action_taken="Already filed")
    _entry(db_session, action_taken="Emailed Gil Loose (gil@looseco.com) re a space")

    report = _run_report(db_session)
    assert report["total_entries"] == 1
    assert report["proposals"][0]["email"] == "gil@looseco.com"
