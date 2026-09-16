"""One email, several deals — POST /api/activity/from-email with `deals`.

The most valuable mail Jack receives is a weekly leasing-notes email carrying
seven deals: different tenants, buildings, rates, terms and expiries. Logged as
one entry, six of them existed only as prose. This file locks the split:

  - each deal becomes its own entry, stamped to its own company, with its own
    discovery capture, facts and stated values
  - recipients and attachments on the email apply to every deal
  - every entry carries a distinct message id; message-ids still returns the
    bare provider id exactly once, and a re-POST is a clean 409
  - a failure on any deal writes none of them and leaves the id unrecorded
  - source_note stores and is served on every surface that shows the entry
  - ten deals do not cost ten company-resolution passes
  - a payload without deals behaves exactly as before
  - /api/companies/ still serves its seven contract fields

In-memory SQLite, a temp directory for files, dependency-overridden get_db. No
live database, no network, no model calls.
"""
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers every table on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.api.routes import activity as activity_routes
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, ContactFact
from app.models.email_ingest import ActivityAttachment, PendingCompanyUpdate
from app.services import attachment_storage
from migrations import ensure_schema


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


@pytest.fixture(autouse=True)
def docs_folder(tmp_path, monkeypatch):
    """Point DOCUMENTS_FOLDER at a temp directory for every test in this file.

    Patched on `attachment_storage.settings` — the binding the storage module
    itself reads — not on a freshly imported `app.config.settings`, which
    test_benchmarks.py's importlib.reload(app.config) can rebind to a different
    object. Asserted, so a patch that fails to bite fails here instead of
    writing into Jack's real documents folder.
    """
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(tmp_path),
        raising=False,
    )
    assert attachment_storage.documents_folder() == str(tmp_path)
    return tmp_path


def _company(db, name, business_id, **kw):
    company = Company(
        company_id=business_id, name=name,
        industry=kw.pop("industry", "Tech"),
        **kw,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


ROUNDUP_ID = "<leasing-notes-0915@mail>"
NOTE = "From Ann Waller's leasing notes, September 15, 2026."


def _roundup(deals, **overrides):
    payload = {
        "from_email": "ann.waller@crgnova.com",
        "from_name": "Ann Waller",
        "direction": "inbound",
        "subject": "Leasing notes 9/15",
        "source_message_id": ROUNDUP_ID,
        "sent_at": "2026-09-15",
        "source_note": NOTE,
        "deals": deals,
    }
    payload.update(overrides)
    return payload


def _three_deals():
    return [
        {
            "company_override": "Scott Management",
            "action_taken": "Scott Management renewing 4,200 SF at 1101 Wilson.",
            "disc_current_sf": 4200,
            "disc_current_rent_psf": 38.5,
            "disc_lease_expiry": "2027-06-30",
        },
        {
            "company_override": "Harbor Dental",
            "action_taken": "Harbor Dental signed LOI for a 2nd-gen suite.",
            "disc_current_sf": 2100,
            "disc_decision_timeline": "Decision by November",
        },
        {
            "company_override": "Pinecrest Advisors",
            "action_taken": "Pinecrest Advisors expanding; needs ground floor.",
            "disc_buildout_needs": "Ground-floor access, two offices",
            "disc_decision_maker": "Managing partner",
        },
    ]


def _post(client, payload, status=200):
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == status, resp.text
    return resp.json()


def _primary_entries(db):
    """The deal entries (not participant rows), in write order."""
    return (
        db.query(ActivityLog)
        .filter(ActivityLog.participation.isnot(True))
        .filter(ActivityLog.contact_id == _sender(db).id)
        .order_by(ActivityLog.id)
        .all()
    )


def _sender(db):
    return db.query(Contact).filter(Contact.email == "ann.waller@crgnova.com").one()


# ══ 1. One email, several entries ═════════════════════════════════════════════

def test_three_deals_create_three_entries_each_on_its_own_company(db_session, client):
    scott = _company(db_session, "Scott Management", "CO-701")
    harbor = _company(db_session, "Harbor Dental", "CO-702")
    pine = _company(db_session, "Pinecrest Advisors", "CO-703")

    body = _post(client, _roundup(_three_deals()))

    entries = _primary_entries(db_session)
    assert len(entries) == 3
    assert [e.company_stamp_id for e in entries] == [scott.id, harbor.id, pine.id]
    assert [e.company_id for e in entries] == [scott.id, harbor.id, pine.id]

    # Each carries its own discovery capture — nothing bleeds across deals.
    a, b, c = entries
    assert (a.disc_current_sf, a.disc_current_rent_psf, a.disc_lease_expiry) == (
        4200, 38.5, date(2027, 6, 30),
    )
    assert a.disc_decision_timeline is None and a.disc_buildout_needs is None
    assert (b.disc_current_sf, b.disc_decision_timeline) == (2100, "Decision by November")
    assert b.disc_current_rent_psf is None and b.disc_buildout_needs is None
    assert c.disc_buildout_needs == "Ground-floor access, two offices"
    assert c.disc_decision_maker == "Managing partner"
    assert c.disc_current_sf is None

    assert [e.action_taken for e in entries] == [d["action_taken"] for d in _three_deals()]

    # The response lists every entry, with its company.
    assert [e["id"] for e in body["entries"]] == [e.id for e in entries]
    assert [e["company_name"] for e in body["entries"]] == [
        "Scott Management", "Harbor Dental", "Pinecrest Advisors",
    ]
    assert all(e["contact_name"] == "Ann Waller" for e in body["entries"])
    # Top-level stays the first entry, so a caller reading `id` is unaffected.
    assert body["id"] == entries[0].id


def test_email_level_fields_apply_to_every_deal(db_session, client):
    _post(client, _roundup(_three_deals(), outcome="FYI", subject="Leasing notes 9/15"))
    for entry in _primary_entries(db_session):
        assert entry.direction == "inbound"
        assert entry.log_date == date(2026, 9, 15)
        assert entry.subject == "Leasing notes 9/15"
        assert entry.sender_email == "ann.waller@crgnova.com"
        assert entry.outcome == "FYI"
        assert entry.channel == "email"


def test_recipients_and_attachments_apply_to_all_three(db_session, client, docs_folder):
    source = docs_folder / "incoming" / "Leasing Notes.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-notes")

    body = _post(client, _roundup(
        _three_deals(),
        to_recipients=[
            {"email": "jzamer@z-reg.com"},
            {"email": "mike@crgnova.com", "name": "Mike Zamer"},
        ],
        cc_recipients=[{"email": "ray@avisonyoung.com", "name": "Ray Ortiz"}],
        attachments=[
            {"filename": "Leasing Notes.pdf", "stored_path": str(source)},
            {"filename": "logo.png", "inline": True},
        ],
    ))

    entries = _primary_entries(db_session)
    assert len(entries) == 3

    mike = db_session.query(Contact).filter(Contact.email == "mike@crgnova.com").one()
    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()

    for entry in entries:
        # The direct To recipient has a direct row for this deal, stamped to it.
        direct = db_session.query(ActivityLog).filter(
            ActivityLog.contact_id == mike.id,
            ActivityLog.company_stamp_id == entry.company_stamp_id,
        ).one()
        assert direct.participation is False
        # The Cc recipient has a participation row for this deal.
        copied = db_session.query(ActivityLog).filter(
            ActivityLog.contact_id == ray.id,
            ActivityLog.company_stamp_id == entry.company_stamp_id,
        ).one()
        assert copied.participation is True
        # And the attachment is recorded against this deal's entry.
        att = db_session.query(ActivityAttachment).filter(
            ActivityAttachment.activity_log_id == entry.id
        ).one()
        assert att.file_name == "Leasing Notes.pdf"
        assert att.stored_year == 2026

    # Per-deal breakdown in the response matches.
    for item in body["entries"]:
        assert len(item["participant_entry_ids"]) == 1
        assert len(item["participation_entry_ids"]) == 1

    # The file was filed once, not once per deal, and no inline image was filed.
    assert body["attachments_saved"] == 1
    assert sorted(p.name for p in (docs_folder / "2026").iterdir()) == ["Leasing Notes.pdf"]
    # Nothing Jack owns became a contact.
    assert db_session.query(Contact).filter(Contact.email == "jzamer@z-reg.com").count() == 0


# ══ 2. Dedup across split entries ═════════════════════════════════════════════

def test_each_entry_gets_a_distinct_message_id_and_message_ids_returns_the_bare_id_once(
    db_session, client,
):
    _post(client, _roundup(
        _three_deals(), cc_recipients=[{"email": "ray@avisonyoung.com"}],
    ))

    rows = db_session.query(ActivityLog).all()
    ids = [r.source_message_id for r in rows]
    assert len(rows) == 6                       # three deals × (Ann + Ray)
    assert len(set(ids)) == len(ids)            # the unique constraint holds
    assert all(i.startswith(ROUNDUP_ID) for i in ids)

    primary_ids = [e.source_message_id for e in _primary_entries(db_session)]
    assert primary_ids == [ROUNDUP_ID, f"{ROUNDUP_ID}#d2", f"{ROUNDUP_ID}#d3"]

    served = client.get("/api/activity/message-ids").json()
    assert served.count(ROUNDUP_ID) == 1


def test_reposting_a_multi_deal_email_is_a_clean_409(db_session, client):
    payload = _roundup(_three_deals(), cc_recipients=[{"email": "ray@avisonyoung.com"}])
    _post(client, payload)
    counts = {
        "entries": db_session.query(ActivityLog).count(),
        "companies": db_session.query(Company).count(),
        "contacts": db_session.query(Contact).count(),
    }

    second = client.post("/api/activity/from-email", json=payload)
    assert second.status_code == 409
    assert ROUNDUP_ID in second.json()["detail"]

    assert db_session.query(ActivityLog).count() == counts["entries"]
    assert db_session.query(Company).count() == counts["companies"]
    assert db_session.query(Contact).count() == counts["contacts"]


# ══ 3. What each deal says, lands where it belongs ════════════════════════════

def test_per_deal_proposed_updates_queue_against_the_correct_company(db_session, client):
    scott = _company(db_session, "Scott Management", "CO-711", current_headcount=20)
    harbor = _company(db_session, "Harbor Dental", "CO-712", current_sf_occupied=1800)

    deals = [
        {
            "company_override": "Scott Management",
            "action_taken": "Scott is up to 32 people.",
            "proposed_company_updates": [
                {"field": "headcount", "value": 32, "source_sentence": "Scott is up to 32."},
            ],
        },
        {
            "company_override_id": harbor.id,
            "action_taken": "Harbor needs 2,400 SF.",
            "proposed_company_updates": [
                {"field": "sf", "value": "2,400", "source_sentence": "Harbor needs 2,400 SF."},
            ],
        },
    ]
    body = _post(client, _roundup(deals))

    entries = _primary_entries(db_session)
    pending = db_session.query(PendingCompanyUpdate).order_by(PendingCompanyUpdate.id).all()
    assert [(p.company_id, p.field, p.proposed_value) for p in pending] == [
        (scott.id, "headcount", "32"),
        (harbor.id, "sf", "2400"),
    ]
    # Each queued value is sourced to its own deal's entry.
    assert [p.source_entry_id for p in pending] == [entries[0].id, entries[1].id]

    # Nothing wrote through to the company record.
    db_session.refresh(scott)
    db_session.refresh(harbor)
    assert scott.current_headcount == 20
    assert harbor.current_sf_occupied == 1800

    assert [e["pending_updates_created"] for e in body["entries"]] == [1, 1]
    assert body["pending_updates_created"] == 2


def test_per_deal_facts_land_on_the_contact_sourced_to_their_own_entry(db_session, client):
    deals = [
        {"company_override": "Scott Management", "action_taken": "Scott renewal.",
         "facts": ["Tracks the Scott renewal personally"]},
        {"company_override": "Harbor Dental", "action_taken": "Harbor LOI.",
         "facts": [{"text": "Introduced Harbor's owner to Jack"},
                   {"text": "Prefers calls after 4pm"}]},
        {"company_override": "Pinecrest Advisors", "action_taken": "Pinecrest expanding."},
    ]
    body = _post(client, _roundup(deals))

    ann = _sender(db_session)
    entries = _primary_entries(db_session)
    facts = db_session.query(ContactFact).order_by(ContactFact.id).all()
    assert all(f.contact_id == ann.id for f in facts)
    assert [(f.fact_text, f.source_entry_id) for f in facts] == [
        ("Tracks the Scott renewal personally", entries[0].id),
        ("Introduced Harbor's owner to Jack", entries[1].id),
        ("Prefers calls after 4pm", entries[1].id),
    ]
    assert [e["facts_written"] for e in body["entries"]] == [1, 2, 0]
    assert body["facts_written"] == 3


def test_a_new_company_named_by_two_deals_is_created_once(db_session, client):
    deals = [
        {"company_override": "Tidewater Labs", "action_taken": "Tidewater, suite A."},
        {"company_override": "Tidewater Labs, LLC", "action_taken": "Tidewater, suite B."},
    ]
    _post(client, _roundup(deals))
    labs = db_session.query(Company).filter(Company.name.ilike("Tidewater%")).all()
    assert len(labs) == 1
    assert {e.company_stamp_id for e in _primary_entries(db_session)} == {labs[0].id}


# ══ 4. One transaction ════════════════════════════════════════════════════════

def _counts(db):
    return {
        "entries":     db.query(ActivityLog).count(),
        "companies":   db.query(Company).count(),
        "contacts":    db.query(Contact).count(),
        "facts":       db.query(ContactFact).count(),
        "pending":     db.query(PendingCompanyUpdate).count(),
        "attachments": db.query(ActivityAttachment).count(),
    }


def test_a_bad_value_on_the_third_deal_writes_none_of_the_three(db_session, client):
    _company(db_session, "Scott Management", "CO-721", current_headcount=20)
    before = _counts(db_session)

    deals = _three_deals()
    deals[0]["proposed_company_updates"] = [{"field": "headcount", "value": "30"}]
    deals[1]["facts"] = ["Harbor owner is a referral"]
    deals[2]["proposed_company_updates"] = [
        {"field": "lease_expiry", "value": "sometime next spring"},   # fails here
    ]
    resp = client.post("/api/activity/from-email", json=_roundup(
        deals, attachments=[{"filename": "Notes.pdf"}],
    ))
    assert resp.status_code == 400

    assert _counts(db_session) == before
    assert ROUNDUP_ID not in client.get("/api/activity/message-ids").json()

    # The id is free: the next run logs the corrected email in full.
    _post(client, _roundup(_three_deals()))
    assert len(_primary_entries(db_session)) == 3


def test_an_unexpected_failure_on_the_third_deal_writes_none(db_session, client, monkeypatch):
    before = _counts(db_session)

    real_create_fact = activity_routes.create_fact
    calls = {"n": 0}

    def exploding_create_fact(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return real_create_fact(*args, **kwargs)

    monkeypatch.setattr(activity_routes, "create_fact", exploding_create_fact)

    deals = _three_deals()
    for i, deal in enumerate(deals):
        deal["facts"] = [f"fact for deal {i + 1}"]
    resp = client.post("/api/activity/from-email", json=_roundup(deals))
    assert resp.status_code == 500
    assert calls["n"] == 3

    assert _counts(db_session) == before
    assert db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id.like(f"{ROUNDUP_ID}%")
    ).count() == 0
    assert ROUNDUP_ID not in client.get("/api/activity/message-ids").json()


def test_top_level_deal_fields_alongside_deals_are_refused_not_dropped(db_session, client):
    before = _counts(db_session)
    resp = client.post("/api/activity/from-email", json=_roundup(
        _three_deals(),
        facts=["which deal is this about?"],
        disc_current_sf=9000,
        company_override="Somebody",
    ))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "facts" in detail and "disc_current_sf" in detail and "company_override" in detail
    assert _counts(db_session) == before


def test_an_unknown_company_id_on_one_deal_writes_nothing(db_session, client):
    before = _counts(db_session)
    deals = _three_deals()
    deals[1] = {"company_override_id": 99999, "action_taken": "Ghost deal."}
    resp = client.post("/api/activity/from-email", json=_roundup(deals))
    assert resp.status_code == 404
    assert _counts(db_session) == before


# ══ 5. Attribution ════════════════════════════════════════════════════════════

def test_source_note_stores_and_returns_everywhere_the_entry_shows(db_session, client):
    scott = _company(db_session, "Scott Management", "CO-731")
    deals = _three_deals()
    deals[2]["source_note"] = "From Ann Waller's leasing notes — Pinecrest section."
    body = _post(client, _roundup(deals))

    entries = _primary_entries(db_session)
    assert [e.source_note for e in entries] == [NOTE, NOTE, deals[2]["source_note"]]

    # The response.
    assert body["source_note"] == NOTE
    assert [e["source_note"] for e in body["entries"]] == [NOTE, NOTE, deals[2]["source_note"]]

    # The flat activity feed.
    feed = {row["id"]: row for row in client.get("/api/activity/").json()}
    assert feed[entries[0].id]["source_note"] == NOTE

    # Scott Management's own timeline shows it arrived in a roundup.
    company_tl = client.get(f"/api/companies/{scott.company_id}/timeline").json()
    assert [e["source_note"] for e in company_tl["entries"]] == [NOTE]

    # And Ann's thread.
    ann = _sender(db_session)
    thread = client.get(f"/api/contacts/{ann.id}/timeline").json()
    assert {e["source_note"] for e in thread["entries"]} == {NOTE, deals[2]["source_note"]}


def test_source_note_is_optional_and_null_by_default(db_session, client):
    body = _post(client, {
        "from_email": "dana@collaborative-av.com",
        "direction": "inbound",
        "source_message_id": "<plain-1@mail>",
        "action_taken": "Dana wrote in.",
    })
    assert body["source_note"] is None
    assert db_session.query(ActivityLog).one().source_note is None


# ══ 6. Scale ══════════════════════════════════════════════════════════════════

def _company_queries_for(client, db, payload):
    """Every SQL statement that reads the companies table during one POST."""
    engine = db.get_bind()
    seen = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if "FROM companies" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _post(client, payload)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    return seen


def test_ten_deals_resolve_companies_in_one_pass_not_ten(db_session, client):
    names = [f"Tenant {chr(65 + i)} Holdings" for i in range(10)]
    for i, name in enumerate(names):
        _company(db_session, name, f"CO-8{i:02d}")
    _company(db_session, "CRG NoVA", "CO-899", email_domain="crgnova.com")

    two = _company_queries_for(client, db_session, _roundup(
        [{"company_override": n, "action_taken": f"{n} note."} for n in names[:2]],
        source_message_id="<two-deals@mail>",
    ))
    ten = _company_queries_for(client, db_session, _roundup(
        [{"company_override": n, "action_taken": f"{n} note."} for n in names],
        source_message_id="<ten-deals@mail>",
    ))

    # Same number of company reads for ten deals as for two.
    assert len(ten) == len(two)
    # And exactly one full name scan, however many names.
    name_scans = [s for s in ten if "companies.name IS NOT NULL" in s]
    assert len(name_scans) == 1

    stamped = {
        e.company_stamp_id for e in db_session.query(ActivityLog).filter(
            ActivityLog.source_message_id.like("<ten-deals@mail>%")
        )
    }
    assert len(stamped) == 10


def test_company_override_ids_are_fetched_in_one_query(db_session, client):
    ids = [_company(db_session, f"Id Tenant {i}", f"CO-86{i}").id for i in range(6)]
    seen = _company_queries_for(client, db_session, _roundup(
        [{"company_override_id": i, "action_taken": "note"} for i in ids],
    ))
    # Precisely the id fetch — the sender's domain lookup also uses IN (...).
    by_id = [s for s in seen if "WHERE companies.id IN (" in s]
    assert len(by_id) == 1


# ══ 7. Nothing else moved ═════════════════════════════════════════════════════

def test_a_payload_without_deals_behaves_exactly_as_before(db_session, client):
    company = _company(db_session, "Collaborative AV", "CO-741", current_headcount=18)
    body = _post(client, {
        "from_email": "dana@collaborative-av.com",
        "from_name": "Dana Reyes",
        "direction": "inbound",
        "subject": "Space",
        "source_message_id": "<single-1@mail>",
        "action_taken": "Dana walked through what they need.",
        "company_override": "Collaborative AV",
        "disc_current_sf": 5200,
        "facts": ["Signs the lease herself"],
        "proposed_company_updates": [{"field": "headcount", "value": "24"}],
        "cc_recipients": [{"email": "ray@avisonyoung.com"}],
    })

    rows = db_session.query(ActivityLog).order_by(ActivityLog.id).all()
    assert len(rows) == 2
    primary, copied = rows
    assert primary.source_message_id == "<single-1@mail>"
    assert copied.source_message_id.startswith("<single-1@mail>#p")
    assert "#d" not in copied.source_message_id
    assert primary.company_stamp_id == company.id
    assert primary.disc_current_sf == 5200
    assert copied.disc_current_sf is None
    assert primary.source_note is None

    assert body["id"] == primary.id
    assert body["facts_written"] == 1
    assert body["pending_updates_created"] == 1
    assert body["participation_entry_ids"] == [copied.id]
    assert len(body["entries"]) == 1
    assert body["entries"][0]["id"] == primary.id


def test_an_empty_deals_array_is_the_single_entry_path(db_session, client):
    body = _post(client, {
        "from_email": "dana@collaborative-av.com",
        "direction": "inbound",
        "source_message_id": "<empty-deals@mail>",
        "action_taken": "Dana wrote in.",
        "disc_current_sf": 3000,
        "deals": [],
    })
    row = db_session.query(ActivityLog).one()
    assert row.source_message_id == "<empty-deals@mail>"
    assert row.disc_current_sf == 3000
    assert body["id"] == row.id


def test_companies_list_still_returns_all_seven_contract_fields(db_session, client):
    """outreach_agent.py reads these by name."""
    _company(
        db_session, "Contract Co", "CO-914",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Tysons", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    _post(client, _roundup([{"company_override": "Contract Co", "action_taken": "x"}]))
    row = next(
        r for r in client.get("/api/companies/").json() if r["company_id"] == "CO-914"
    )
    assert row["company_id"] == "CO-914"
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42
    assert row["headcount_growth_pct"] == 12.5
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"
    assert row["opportunity_score"] == 77.0


# ══ 8. Migration ══════════════════════════════════════════════════════════════

def test_ensure_schema_adds_source_note_idempotently():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE activity_logs (id INTEGER PRIMARY KEY, log_date DATE)")
    ensure_schema.ensure_activity_logs(cur)
    assert ensure_schema._has_column(cur, "activity_logs", "source_note")
    # A second run is a no-op, not a duplicate-column error.
    assert ensure_schema.ensure_activity_logs(cur) == 0
    cur.execute("INSERT INTO activity_logs (id) VALUES (1)")
    cur.execute("SELECT source_note FROM activity_logs WHERE id = 1")
    assert cur.fetchone() == (None,)
    conn.close()


# ══ 9. A deal names its own contact ═══════════════════════════════════════════
#
# Ann's weekly notes cover seven deals. Without a per-deal contact every fact
# lands on Ann, and her Relationship panel — the two lines Jack reads before
# calling her — fills with facts about other people's tenants.

from app.models.email_ingest import ContactAddressOverride  # noqa: E402


def _existing_contact(db, name, email, company=None, **kw):
    contact = Contact(
        name=name, email=email,
        company_id=company.id if company is not None else None,
        contact_type="tenant", stage=kw.pop("stage", "Sent"),
        triaged=kw.pop("triaged", True), responded=kw.pop("responded", False),
        **kw,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def _facts_of(db, contact):
    return [
        f.fact_text for f in
        db.query(ContactFact).filter(ContactFact.contact_id == contact.id)
        .order_by(ContactFact.id)
    ]


def test_a_deal_contact_gets_the_entry_and_the_facts_not_the_sender(db_session, client):
    scott_co = _company(db_session, "Scott Management", "CO-901", email_domain="scottmgmt.com")
    bill = _existing_contact(db_session, "Bill Scott", "bill@scottmgmt.com", scott_co)

    body = _post(client, _roundup([{
        "company_override": "Scott Management",
        "contact_email": "Bill@ScottMgmt.com",
        "action_taken": "Scott Management renewing 4,200 SF.",
        "disc_current_sf": 4200,
        "facts": ["Bill signs every renewal himself"],
    }]))

    entry = db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id == ROUNDUP_ID
    ).one()
    assert entry.contact_id == bill.id
    assert entry.company_stamp_id == scott_co.id
    assert entry.disc_current_sf == 4200
    assert entry.participation is False
    assert entry.source_note == NOTE

    assert _facts_of(db_session, bill) == ["Bill signs every renewal himself"]
    ann = _sender(db_session)
    assert _facts_of(db_session, ann) == []
    # Ann has no row for a deal that is about Bill.
    assert db_session.query(ActivityLog).filter(ActivityLog.contact_id == ann.id).count() == 0

    assert body["entries"][0]["contact_id"] == bill.id
    assert body["entries"][0]["contact_name"] == "Bill Scott"
    assert body["entries"][0]["facts_written"] == 1
    assert body["entries"][0]["contact_email_ignored"] is None
    # Ann's relationship panel stays about Ann.
    header = client.get(f"/api/contacts/{ann.id}").json()
    assert header["relationship_lines"] == []


def test_a_deal_without_contact_email_still_goes_to_the_sender(db_session, client):
    _company(db_session, "Scott Management", "CO-902", email_domain="scottmgmt.com")
    bill = _existing_contact(
        db_session, "Bill Scott", "bill@scottmgmt.com",
        db_session.query(Company).filter(Company.company_id == "CO-902").one(),
    )
    body = _post(client, _roundup([
        {"company_override": "Scott Management", "contact_email": "bill@scottmgmt.com",
         "action_taken": "Scott renewal.", "facts": ["About Bill"]},
        {"company_override": "Harbor Dental", "action_taken": "Harbor LOI.",
         "facts": ["Ann tracks Harbor closely"]},
    ]))

    ann = _sender(db_session)
    rows = db_session.query(ActivityLog).order_by(ActivityLog.id).all()
    assert [(r.contact_id, r.source_message_id) for r in rows] == [
        (bill.id, ROUNDUP_ID),
        (ann.id, f"{ROUNDUP_ID}#d2"),
    ]
    # The deal without a contact is exactly today's entry: on Ann, from Ann.
    assert rows[1].sender_email == "ann.waller@crgnova.com"
    assert _facts_of(db_session, ann) == ["Ann tracks Harbor closely"]
    assert _facts_of(db_session, bill) == ["About Bill"]
    assert [e["contact_id"] for e in body["entries"]] == [bill.id, ann.id]


def test_three_deals_with_three_contacts_each_keep_their_own_facts(db_session, client):
    deals = _three_deals()
    people = [
        ("bill@scottmgmt.com", "Bill Scott", "Bill signs renewals"),
        ("hana@harbordental.com", "Hana Lee", "Hana owns the practice"),
        ("pete@pinecrestadv.com", "Pete Ruiz", "Pete wants ground floor"),
    ]
    for deal, (email, name, fact) in zip(deals, people):
        deal.update(contact_email=email, contact_name=name, facts=[fact])

    _post(client, _roundup(deals))

    entries = (
        db_session.query(ActivityLog)
        .filter(ActivityLog.source_message_id.like(f"{ROUNDUP_ID}%"))
        .order_by(ActivityLog.id).all()
    )
    assert len(entries) == 3
    contacts = [
        db_session.query(Contact).filter(Contact.email == email).one()
        for email, _, _ in people
    ]
    assert len({c.id for c in contacts}) == 3
    assert [e.contact_id for e in entries] == [c.id for c in contacts]
    for contact, (_, name, fact), entry in zip(contacts, people, entries):
        assert contact.name == name
        facts = db_session.query(ContactFact).filter(ContactFact.contact_id == contact.id).all()
        assert [(f.fact_text, f.source_entry_id) for f in facts] == [(fact, entry.id)]
    assert _facts_of(db_session, _sender(db_session)) == []


def test_an_unknown_deal_contact_is_created_untriaged_under_their_own_domain(db_session, client):
    _post(client, _roundup([{
        "company_override": "Harbor Dental",
        "contact_email": "hana.lee@harbordental.com",
        "action_taken": "Harbor LOI.",
    }]))

    hana = db_session.query(Contact).filter(Contact.email == "hana.lee@harbordental.com").one()
    assert hana.auto_created is True
    assert hana.triaged is False
    assert hana.responded is False
    assert hana.stage == "Sent"
    # No name given → derived from the address, as for any email contact.
    assert hana.name == "Hana Lee"
    own = db_session.query(Company).filter(Company.id == hana.company_id).one()
    assert own.email_domain == "harbordental.com"


def test_a_deal_contact_keeps_their_company_while_the_entry_stamps_the_deals(db_session, client):
    """The edge case, decided: a broker at Avison Young named on a Scott
    Management deal belongs to Avison Young; the entry is about Scott
    Management. Neither is reconciled to the other."""
    scott_co = _company(db_session, "Scott Management", "CO-903")
    _post(client, _roundup([{
        "company_override": "Scott Management",
        "contact_email": "ray@avisonyoung.com",
        "contact_name": "Ray Ortiz",
        "action_taken": "Ray is repping Scott Management.",
    }]))

    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()
    ray_company = db_session.query(Company).filter(Company.id == ray.company_id).one()
    assert ray_company.email_domain == "avisonyoung.com"
    assert ray_company.id != scott_co.id

    entry = db_session.query(ActivityLog).filter(ActivityLog.contact_id == ray.id).one()
    assert entry.company_stamp_id == scott_co.id
    assert entry.company_id == scott_co.id


@pytest.mark.parametrize("own", [
    "jzamer@z-reg.com", "JZamer@SimpsonDev.com", "jackzamer1@gmail.com",
])
def test_a_deal_contact_at_jacks_own_address_falls_back_to_the_sender(db_session, client, own):
    contacts_before = db_session.query(Contact).count()
    body = _post(client, _roundup([{
        "company_override": "Scott Management",
        "contact_email": own,
        "contact_name": "Jack Zamer",
        "action_taken": "Scott renewal.",
        "facts": ["Renewal is on track"],
    }]))

    ann = _sender(db_session)
    # Only Ann was created — never a contact at Jack's address.
    assert db_session.query(Contact).count() == contacts_before + 1
    assert db_session.query(Contact).filter(Contact.email == own.lower()).count() == 0
    entry = db_session.query(ActivityLog).one()
    assert entry.contact_id == ann.id
    assert entry.sender_email == "ann.waller@crgnova.com"
    assert _facts_of(db_session, ann) == ["Renewal is on track"]
    assert body["entries"][0]["contact_email_ignored"] == own.lower()


def test_the_sender_recipients_and_participation_are_unaffected_by_deal_contacts(
    db_session, client,
):
    def run(with_contacts, message_id):
        deals = _three_deals()
        if with_contacts:
            deals[0].update(contact_email="bill@scottmgmt.com")
            deals[2].update(contact_email="pete@pinecrestadv.com")
        return _post(client, _roundup(
            deals,
            source_message_id=message_id,
            to_recipients=[{"email": "jzamer@z-reg.com"},
                           {"email": "mike@crgnova.com", "name": "Mike Zamer"}],
            cc_recipients=[{"email": "ray@avisonyoung.com", "name": "Ray Ortiz"}],
        ))

    run(True, "<with-contacts@mail>")

    ann = _sender(db_session)
    mike = db_session.query(Contact).filter(Contact.email == "mike@crgnova.com").one()
    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()
    bill = db_session.query(Contact).filter(Contact.email == "bill@scottmgmt.com").one()

    # The sender's own contact: an inbound reply, exactly as before.
    assert ann.responded is True
    assert ann.stage == "Replied"
    # A deal contact did not write the email — no reply recorded against them.
    assert bill.responded is False
    assert bill.stage == "Sent"

    def rows(contact, prefix):
        return db_session.query(ActivityLog).filter(
            ActivityLog.contact_id == contact.id,
            ActivityLog.source_message_id.like(f"{prefix}%"),
        ).all()

    # Recipients still get one row per deal, participation preserved.
    assert len(rows(mike, "<with-contacts@mail>")) == 3
    assert all(r.participation is False for r in rows(mike, "<with-contacts@mail>"))
    assert len(rows(ray, "<with-contacts@mail>")) == 3
    assert all(r.participation is True for r in rows(ray, "<with-contacts@mail>"))
    # Ann keeps only the deal that named no contact.
    assert [r.source_message_id for r in rows(ann, "<with-contacts@mail>")] == [
        "<with-contacts@mail>#d2",
    ]

    # And the same email without deal contacts still produces today's shape.
    run(False, "<without-contacts@mail>")
    assert len(rows(ann, "<without-contacts@mail>")) == 3
    assert len(rows(mike, "<without-contacts@mail>")) == 3
    assert len(rows(ray, "<without-contacts@mail>")) == 3

    ids = [r.source_message_id for r in db_session.query(ActivityLog)]
    assert len(ids) == len(set(ids))


def test_a_deal_contact_who_is_also_copied_gets_one_row_for_that_deal(db_session, client):
    _post(client, _roundup(
        [{"company_override": "Scott Management", "contact_email": "ray@avisonyoung.com",
          "action_taken": "Ray on Scott."}],
        cc_recipients=[{"email": "ray@avisonyoung.com"}],
    ))
    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()
    ray_rows = db_session.query(ActivityLog).filter(ActivityLog.contact_id == ray.id).all()
    assert len(ray_rows) == 1
    assert ray_rows[0].participation is False
    assert ray_rows[0].source_message_id == ROUNDUP_ID


def test_a_taught_address_resolves_a_deal_contact_to_the_corrected_person(db_session, client):
    right = _existing_contact(db_session, "Bill Scott", "william@scottmgmt.com")
    db_session.add(ContactAddressOverride(email="bill@scottmgmt.com", contact_id=right.id))
    db_session.commit()

    _post(client, _roundup([{
        "company_override": "Scott Management", "contact_email": "bill@scottmgmt.com",
        "action_taken": "Scott renewal.",
    }]))
    assert db_session.query(ActivityLog).one().contact_id == right.id
    assert db_session.query(Contact).filter(Contact.email == "bill@scottmgmt.com").count() == 0


def test_correcting_a_deal_contact_entry_never_teaches_the_senders_address(db_session, client):
    """A reassignment maps the entry's sender_email to the corrected contact.
    On a deal entry that address must be the deal contact's — mapping Ann's
    would file every future email from Ann under a Scott Management person."""
    _post(client, _roundup([{
        "company_override": "Scott Management", "contact_email": "bill@scottmgmt.com",
        "action_taken": "Scott renewal.",
    }]))
    entry = db_session.query(ActivityLog).one()
    right = _existing_contact(db_session, "William Scott", "william@scottmgmt.com")

    resp = client.patch(f"/api/activity/{entry.id}/assign", json={"contact_id": right.id})
    assert resp.status_code == 200, resp.text

    taught = {o.email: o.contact_id for o in db_session.query(ContactAddressOverride)}
    assert taught == {"bill@scottmgmt.com": right.id}


def test_reposting_an_email_with_deal_contacts_is_a_clean_409(db_session, client):
    deals = _three_deals()
    for deal, email in zip(deals, ["a@scottmgmt.com", "b@harbordental.com", "c@pinecrestadv.com"]):
        deal["contact_email"] = email
    payload = _roundup(deals)
    _post(client, payload)
    before = _counts(db_session)

    assert client.post("/api/activity/from-email", json=payload).status_code == 409
    assert _counts(db_session) == before
    assert client.get("/api/activity/message-ids").json().count(ROUNDUP_ID) == 1


def test_a_failure_on_the_third_deal_with_contacts_writes_none_of_the_three(db_session, client):
    before = _counts(db_session)
    deals = _three_deals()
    for deal, email in zip(deals, ["a@scottmgmt.com", "b@harbordental.com", "c@pinecrestadv.com"]):
        deal["contact_email"] = email
        deal["facts"] = [f"fact about {email}"]
    deals[2]["proposed_company_updates"] = [{"field": "sf", "value": "lots"}]   # fails here

    resp = client.post("/api/activity/from-email", json=_roundup(deals))
    assert resp.status_code == 400
    assert _counts(db_session) == before
    assert ROUNDUP_ID not in client.get("/api/activity/message-ids").json()


def _table_queries_for(client, db, payload, tables):
    """SELECTs reading any of `tables` during one POST."""
    engine = db.get_bind()
    seen = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and any(
            f"FROM {t}" in statement for t in tables
        ):
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _post(client, payload)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    return seen


def _deals_with_contacts(names_and_emails):
    return [
        {"company_override": company, "contact_email": email, "action_taken": f"{company} note."}
        for company, email in names_and_emails
    ]


def test_ten_deals_with_ten_contacts_resolve_in_one_pass(db_session, client):
    crg = _company(db_session, "CRG NoVA", "CO-950", email_domain="crgnova.com")
    _existing_contact(db_session, "Ann Waller", "ann.waller@crgnova.com", crg)
    pairs = []
    for i in range(10):
        co = _company(db_session, f"Tenant {chr(65 + i)} Holdings", f"CO-96{i}",
                      email_domain=f"tenant{i}.com")
        email = f"lead@tenant{i}.com"
        _existing_contact(db_session, f"Lead {i}", email, co)
        pairs.append((co.name, email))

    tables = ("contacts", "contact_address_overrides", "companies")
    two = _table_queries_for(client, db_session, _roundup(
        _deals_with_contacts(pairs[:2]), source_message_id="<two@mail>",
    ), tables)
    ten = _table_queries_for(client, db_session, _roundup(
        _deals_with_contacts(pairs), source_message_id="<ten@mail>",
    ), tables)
    assert len(ten) == len(two)

    stamped = db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id.like("<ten@mail>%")
    ).all()
    assert len({r.contact_id for r in stamped}) == 10


def test_ten_unknown_deal_contacts_are_looked_up_in_one_pass(db_session, client):
    """Creation needs a new row each; LOOKING UP who they are must not."""
    crg = _company(db_session, "CRG NoVA", "CO-970", email_domain="crgnova.com")
    _existing_contact(db_session, "Ann Waller", "ann.waller@crgnova.com", crg)

    tables = ("contacts", "contact_address_overrides")
    two = _table_queries_for(client, db_session, _roundup(
        _deals_with_contacts([(f"New Co {i}", f"p@newtwo{i}.com") for i in range(2)]),
        source_message_id="<two-new@mail>",
    ), tables)
    ten = _table_queries_for(client, db_session, _roundup(
        _deals_with_contacts([(f"New Co {i}", f"p@newten{i}.com") for i in range(10)]),
        source_message_id="<ten-new@mail>",
    ), tables)
    assert len(ten) == len(two)
    assert db_session.query(Contact).filter(Contact.email.like("p@newten%")).count() == 10


def test_companies_contract_holds_after_deals_with_contacts(db_session, client):
    _company(
        db_session, "Contract Co", "CO-915",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Tysons", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    _post(client, _roundup([{
        "company_override": "Contract Co", "contact_email": "cfo@contractco.com",
        "action_taken": "x", "facts": ["CFO decides"],
    }]))
    row = next(
        r for r in client.get("/api/companies/").json() if r["company_id"] == "CO-915"
    )
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42
    assert row["headcount_growth_pct"] == 12.5
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"
    assert row["opportunity_score"] == 77.0
