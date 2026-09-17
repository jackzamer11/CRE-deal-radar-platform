"""A contact can own several addresses — and two records for the same person
can be merged into one.

Fred Zamer exists as two contact records today, one per address he mails
from, with his history split between them. This file locks the fix:

  - a contact's alias addresses resolve to it exactly like the primary one
  - one address belongs to exactly one contact — a clean 409, never a steal
  - an address Jack owns can never become anyone's alias
  - the primary address can never be removed out from under a contact
  - merging two contacts moves every entry, fact and address in one
    transaction, target wins on conflict, and a failed merge writes nothing
  - the historical name-keyed backfill (null-email contacts) is untouched by
    the alias-mirroring migration, and still matches by name
  - the migration is idempotent
  - /api/companies/ still serves its seven contract fields

In-memory SQLite, a temp directory for files, dependency-overridden get_db. No
live database, no network, no CoStar, no OpenAI or Anthropic calls.
"""
import sqlite3
from datetime import date, timedelta

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
from app.models.company import Company
from app.models.contact import Contact, ContactFact
from app.models.email_ingest import ContactAddressOverride
from app.services import attachment_storage
from app.services.email_ingest_service import resolve_contacts_for_names


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
    """Point DOCUMENTS_FOLDER at a temp directory for the whole file.

    Patched on `attachment_storage.settings` — the binding the storage module
    itself reads — never on a freshly imported `app.config.settings`, which
    test_benchmarks.py's importlib.reload(app.config) can rebind to a
    different object. Asserted, so a patch that fails to bite fails here
    instead of writing into Jack's real documents folder.
    """
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(tmp_path),
        raising=False,
    )
    assert attachment_storage.documents_folder() == str(tmp_path)
    return tmp_path


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _company(db, name="Acme Corp", business_id="CO-001", **kw):
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact_direct(db, name, email=None, **kw):
    """A contact built straight from the ORM — bypasses the alias mirror, the
    way the historical name-keyed backfill and most existing tests do."""
    kw.setdefault("contact_type", "tenant")
    kw.setdefault("stage", "Sent")
    c = Contact(name=name, email=email, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _post_email(client, **payload):
    payload.setdefault("direction", "inbound")
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_contact_via_api(client, name, email=None, **extra):
    resp = client.post("/api/contacts/", json={"name": name, "email": email, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══ 1. Addresses resolve ═══════════════════════════════════════════════════════

def test_primary_address_still_resolves_unchanged(db_session, client):
    fred = _create_contact_via_api(client, "Fred Zamer", "fredzamer@cs.com")

    body = _post_email(
        client,
        from_email="fredzamer@cs.com",
        from_name="Fred Zamer",
        source_message_id="<msg-primary-1@mail>",
        action_taken="Fred wrote in from his usual address.",
    )
    assert body["contact_id"] == fred["id"]


def test_alias_resolves_to_owning_contact_through_from_email(db_session, client):
    fred = _create_contact_via_api(client, "Fred Zamer", "fredzamer@cs.com")

    add_resp = client.post(
        f"/api/contacts/{fred['id']}/addresses",
        json={"email": "FZamer@fzwork.com"},
    )
    assert add_resp.status_code == 200, add_resp.text
    assert add_resp.json()["is_primary"] is False

    body = _post_email(
        client,
        from_email="fzamer@fzwork.com",   # lower-cased on the wire
        from_name="Fred Zamer",
        source_message_id="<msg-alias-1@mail>",
        action_taken="Fred wrote in from his work address this time.",
    )
    assert body["contact_id"] == fred["id"]
    assert body["contact_name"] == "Fred Zamer"


def test_per_deal_contact_email_resolves_through_an_alias(db_session, client):
    """The multi-deal path (deals[].contact_email) checks the same alias table
    as a plain sender address."""
    fred = _create_contact_via_api(client, "Fred Zamer", "fredzamer@cs.com")
    client.post(
        f"/api/contacts/{fred['id']}/addresses",
        json={"email": "fzamer@fzwork.com"},
    )
    co = _company(db_session, "Simpson Development", "CO-500")

    resp = client.post("/api/activity/from-email", json={
        "from_email": "ann.waller@crgnova.com",
        "from_name": "Ann Waller",
        "direction": "inbound",
        "subject": "Notes",
        "source_message_id": "<msg-deal-alias-1@mail>",
        "deals": [{
            "company_override": co.name,
            "contact_email": "fzamer@fzwork.com",
            "action_taken": "Fred's renewal.",
        }],
    })
    assert resp.status_code == 200, resp.text

    entry = db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id.like("<msg-deal-alias-1@mail>%"),
        ActivityLog.contact_id.isnot(None),
    ).first()
    assert entry is not None
    assert entry.contact_id == fred["id"]


# ══ 2. One address, one contact ════════════════════════════════════════════════

def test_adding_an_address_already_claimed_returns_409_and_writes_nothing(db_session, client):
    owner = _create_contact_via_api(client, "Existing Owner", "owner@shared-co.com")
    other = _create_contact_via_api(client, "Someone Else", None)

    before = db_session.query(ContactAddressOverride).count()
    resp = client.post(
        f"/api/contacts/{other['id']}/addresses",
        json={"email": "owner@shared-co.com"},
    )
    assert resp.status_code == 409
    assert "Existing Owner" in resp.json()["detail"]
    assert db_session.query(ContactAddressOverride).count() == before

    # The other contact still has no addresses of its own.
    assert client.get(f"/api/contacts/{other['id']}/addresses").json() == []


def test_an_address_jack_owns_is_refused_as_an_alias(db_session, client):
    contact = _create_contact_via_api(client, "Some Tenant", "tenant@tenant-co.com")
    before = db_session.query(ContactAddressOverride).count()

    resp = client.post(
        f"/api/contacts/{contact['id']}/addresses",
        json={"email": "jzamer@z-reg.com"},
    )
    assert resp.status_code == 400
    assert db_session.query(ContactAddressOverride).count() == before


def test_adding_an_address_the_contact_already_holds_is_a_no_op(db_session, client):
    """Re-adding the primary (or an existing alias) is idempotent, not a clash."""
    contact = _create_contact_via_api(client, "Some Tenant", "tenant@tenant-co.com")
    resp = client.post(
        f"/api/contacts/{contact['id']}/addresses",
        json={"email": "tenant@tenant-co.com"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_primary"] is True


# ══ 3. Removing and promoting addresses ════════════════════════════════════════

def test_removing_primary_is_refused_unless_another_is_promoted_first(db_session, client):
    contact = _create_contact_via_api(client, "Fred Zamer", "fredzamer@cs.com")
    addresses = client.get(f"/api/contacts/{contact['id']}/addresses").json()
    primary_id = next(a["id"] for a in addresses if a["is_primary"])

    resp = client.delete(f"/api/contacts/{contact['id']}/addresses/{primary_id}")
    assert resp.status_code == 409

    alias = client.post(
        f"/api/contacts/{contact['id']}/addresses",
        json={"email": "fzamer@fzwork.com"},
    ).json()

    still_blocked = client.delete(f"/api/contacts/{contact['id']}/addresses/{primary_id}")
    assert still_blocked.status_code == 409

    promoted = client.patch(f"/api/contacts/{contact['id']}/addresses/{alias['id']}/primary")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["email"] == "fzamer@fzwork.com"

    now_removable = client.delete(f"/api/contacts/{contact['id']}/addresses/{primary_id}")
    assert now_removable.status_code == 200, now_removable.text

    remaining = client.get(f"/api/contacts/{contact['id']}/addresses").json()
    assert len(remaining) == 1
    assert remaining[0]["email"] == "fzamer@fzwork.com"
    assert remaining[0]["is_primary"] is True

    updated = db_session.query(Contact).filter(Contact.id == contact["id"]).first()
    assert updated.email == "fzamer@fzwork.com"


# ══ 4. Merging two contacts ═════════════════════════════════════════════════════

def test_merge_moves_entries_facts_and_addresses_and_deletes_source(db_session, client):
    target = _contact_direct(db_session, "Fred Zamer", "fredzamer@cs.com")
    source = _contact_direct(db_session, "Fred Zamer (work)", "fzamer@fzwork.com")

    entry = ActivityLog(
        log_date=date.today(), contact_id=source.id, action_type="EMAIL",
        action_taken="Talked lease terms.", direction="inbound", channel="email",
    )
    db_session.add(entry)
    db_session.add(ContactFact(
        contact_id=source.id, fact_text="Prefers afternoon calls",
        learned_date=date.today(),
    ))
    db_session.add(ContactAddressOverride(
        email="fzamer.alt@fzwork.com", contact_id=source.id, is_primary=False,
    ))
    db_session.commit()

    resp = client.post(
        f"/api/contacts/{target.id}/merge", json={"source_contact_id": source.id},
    )
    assert resp.status_code == 200, resp.text

    assert db_session.query(Contact).filter(Contact.id == source.id).first() is None

    db_session.refresh(entry)
    assert entry.contact_id == target.id

    fact = db_session.query(ContactFact).filter(
        ContactFact.fact_text == "Prefers afternoon calls"
    ).one()
    assert fact.contact_id == target.id

    moved_alias = db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == "fzamer.alt@fzwork.com"
    ).one()
    assert moved_alias.contact_id == target.id
    assert moved_alias.is_primary is False

    # The source's own primary address becomes a (non-primary) alias of target.
    source_email_row = db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == "fzamer@fzwork.com"
    ).one()
    assert source_email_row.contact_id == target.id
    assert source_email_row.is_primary is False

    # The next email from the source's old address now resolves to target.
    body = _post_email(
        client,
        from_email="fzamer@fzwork.com",
        from_name="Fred Zamer",
        source_message_id="<msg-post-merge-1@mail>",
        action_taken="Wrote in from the old work address.",
    )
    assert body["contact_id"] == target.id


def test_merge_keeps_target_fields_and_carries_responded_and_past_client(db_session, client):
    company = _company(db_session, "Target Co", "CO-600")
    target = _contact_direct(
        db_session, "Target Person", "target@x.com",
        contact_type="counterparty", stage="In Play", company_id=company.id,
        responded=False, is_past_client=False,
    )
    source = _contact_direct(
        db_session, "Source Person", "source@x.com",
        contact_type="tenant", stage="Sent",
        responded=True, is_past_client=True,
    )

    resp = client.post(
        f"/api/contacts/{target.id}/merge", json={"source_contact_id": source.id},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(target)
    assert target.name == "Target Person"
    assert target.stage == "In Play"          # never regressed to source's "Sent"
    assert target.contact_type == "counterparty"
    assert target.company_id == company.id
    assert target.responded is True           # carried because true on source
    assert target.is_past_client is True       # carried because true on source


def test_merge_into_self_is_refused_and_writes_nothing(db_session, client):
    contact = _contact_direct(db_session, "Solo Person", "solo@x.com")
    entry = ActivityLog(
        log_date=date.today(), contact_id=contact.id, action_type="EMAIL",
        action_taken="Something.", direction="inbound", channel="email",
    )
    db_session.add(entry)
    db_session.commit()

    before_entries = db_session.query(ActivityLog).count()
    before_contacts = db_session.query(Contact).count()

    resp = client.post(
        f"/api/contacts/{contact.id}/merge", json={"source_contact_id": contact.id},
    )
    assert resp.status_code == 400

    assert db_session.query(ActivityLog).count() == before_entries
    assert db_session.query(Contact).count() == before_contacts
    assert db_session.query(Contact).filter(Contact.id == contact.id).first() is not None


# ══ 5. The backfill migration ═══════════════════════════════════════════════════

def _raw_alias_schema(cur):
    cur.execute("""
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE contact_address_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            contact_id INTEGER NOT NULL,
            is_primary BOOLEAN NOT NULL DEFAULT 0,
            source_entry_id INTEGER,
            created_at DATETIME,
            updated_at DATETIME
        )
    """)


def test_migration_mirrors_primary_emails_and_skips_name_keyed_contacts():
    from migrations.ensure_schema import backfill_contact_primary_aliases

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    _raw_alias_schema(cur)
    cur.execute(
        "INSERT INTO contacts (id, name, email) VALUES (1, 'Fred Zamer', 'fredzamer@cs.com')"
    )
    # The 355-entry historical backfill's output: name-keyed, no email.
    cur.execute(
        "INSERT INTO contacts (id, name, email) VALUES (2, 'Name Keyed Only', NULL)"
    )
    conn.commit()

    changed = backfill_contact_primary_aliases(cur)
    conn.commit()
    assert changed == 1

    cur.execute("SELECT email, contact_id, is_primary FROM contact_address_overrides")
    rows = cur.fetchall()
    assert rows == [("fredzamer@cs.com", 1, 1)]

    cur.execute(
        "SELECT COUNT(*) FROM contact_address_overrides WHERE contact_id = 2"
    )
    assert cur.fetchone()[0] == 0


def test_migration_is_idempotent():
    from migrations.ensure_schema import backfill_contact_primary_aliases

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    _raw_alias_schema(cur)
    cur.execute(
        "INSERT INTO contacts (id, name, email) VALUES (1, 'Fred Zamer', 'fredzamer@cs.com')"
    )
    conn.commit()

    first = backfill_contact_primary_aliases(cur)
    conn.commit()
    assert first == 1

    second = backfill_contact_primary_aliases(cur)
    conn.commit()
    assert second == 0

    cur.execute("SELECT COUNT(*) FROM contact_address_overrides")
    assert cur.fetchone()[0] == 1


def test_name_keyed_contact_still_matches_by_name(db_session):
    """Untouched by the migration (no email → nothing to mirror) and untouched
    by the alias feature generally: name-keyed contacts still match by name."""
    contact = _contact_direct(db_session, "Backfilled Person", email=None)

    result = resolve_contacts_for_names(db_session, [("Backfilled Person", None)])
    assert len(result) == 1
    assert next(iter(result.values())).id == contact.id

    assert db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.contact_id == contact.id
    ).count() == 0


# ══ 6. /api/companies/ contract ═════════════════════════════════════════════════

def test_companies_list_still_returns_all_seven_contract_fields(db_session, client):
    """outreach_agent.py reads these seven. Renaming one breaks it silently."""
    _company(
        db_session, name="Contract Co", business_id="CO-700",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Tysons", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    resp = client.get("/api/companies/")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["company_id"] == "CO-700")

    assert row["company_id"] == "CO-700"
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42
    assert row["headcount_growth_pct"] == 12.5
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"
    assert row["opportunity_score"] == 77.0
