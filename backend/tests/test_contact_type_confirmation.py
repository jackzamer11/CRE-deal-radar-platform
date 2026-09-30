"""Contact types — unconfirmed until Jack says otherwise.

The email automation used to create every contact as a tenant, because an
address alone cannot tell a tenant from a broker. 192 of 204 "tenants" were made
that way, brokers and landlords among them — and the Intel layer is about to
filter on this field. What this file locks:

  - an automatically created contact starts UNCONFIRMED, never tenant
  - unless Jack marked their firm, in which case they take the firm's type
  - a contact Jack creates by hand still defaults to tenant
  - the suggestion is only a suggestion: reading it never writes a type
  - the email automation's guess is stored, never applied, and a bad one is
    dropped without failing the email
  - confirming a type, and applying it to the firm, which reaches only people
    still unconfirmed
  - a "tenant" filter never returns an unconfirmed contact
  - no tenant outreach is drafted for a firm marked counterparty
  - the one-time migration: auto-created tenants only, exactly once

In-memory SQLite, dependency-overridden get_db. No live DB file, no network.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.company import Company
from app.models.contact import Contact
from app.services.contact_type_service import suggest_type_from


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


def _company(db, name, business_id, **kw):
    company = Company(company_id=business_id, name=name, industry=kw.pop("industry", "Tech"), **kw)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _contact(db, name, **kw):
    kw.setdefault("contact_type", "unconfirmed")
    kw.setdefault("stage", "Sent")
    kw.setdefault("triaged", False)
    kw.setdefault("auto_created", True)
    c = Contact(name=name, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _post_email(client, **payload):
    payload.setdefault("direction", "inbound")
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_email(db, email):
    db.expire_all()
    return db.query(Contact).filter(Contact.email == email).one()


def _list(client, **params):
    params.setdefault("include_closed", True)
    resp = client.get("/api/contacts/", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══ 1. What an automatically created contact starts as ════════════════════════

def test_a_contact_the_email_task_creates_is_unconfirmed(db_session, client):
    _post_email(
        client, from_email="maria@halversondental.com", from_name="Maria Chen",
        subject="Re: your lease", source_message_id="<type-new-1@mail>",
    )
    maria = _by_email(db_session, "maria@halversondental.com")
    assert maria.contact_type == "unconfirmed"
    assert maria.auto_created is True


def test_a_new_contact_at_a_firm_marked_counterparty_takes_the_firms_type(db_session, client):
    _company(
        db_session, "Avison Young", "CO-AY", company_type="counterparty",
        email_domain="avisonyoung.com",
    )
    _post_email(
        client, from_email="new.broker@avisonyoung.com", from_name="New Broker",
        subject="Space at 1101 Wilson", source_message_id="<type-firm-1@mail>",
    )
    assert _by_email(db_session, "new.broker@avisonyoung.com").contact_type == "counterparty"


def test_a_contact_jack_creates_by_hand_still_defaults_to_tenant(db_session, client):
    resp = client.post("/api/contacts/", json={"name": "Dana Reed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["contact_type"] == "tenant"
    # Settled types carry no suggestion.
    assert resp.json()["suggested_type"] is None


# ══ 2. The suggestion — pre-selects, never writes ═════════════════════════════

def _suggest(**kw):
    base = dict(contact_type="unconfirmed", suggested_type=None, email=None,
                company_name=None, company_type=None)
    base.update(kw)
    return suggest_type_from(**base)


def test_broker_words_in_the_firm_name_or_domain_suggest_counterparty():
    assert _suggest(company_name="Stream Realty")["type"] == "counterparty"
    assert _suggest(email="mike@avisonyoung.com")["type"] == "counterparty"
    assert _suggest(company_name="Cushman & Wakefield")["type"] == "counterparty"
    assert _suggest(company_name="Wellbornmanagement")["type"] == "counterparty"


def test_a_tenant_business_that_uses_a_keyword_is_not_suggested_counterparty():
    assert _suggest(company_name="Summit Wealth Management")["type"] == "tenant"


def test_nothing_pointing_the_other_way_suggests_tenant():
    s = _suggest(company_name="Halverson Dental Group", email="maria@halversondental.com")
    assert s["type"] == "tenant"
    assert s["reason"]


def test_a_marked_firm_outranks_the_email_guess_which_outranks_keywords():
    # The email task read a signature; the firm name says realty. The task wins.
    assert _suggest(company_name="Stream Realty", suggested_type="tenant")["type"] == "tenant"
    # Jack marking the firm beats both.
    assert _suggest(company_name="Stream Realty", suggested_type="tenant",
                    company_type="counterparty")["type"] == "counterparty"


def test_a_settled_contact_gets_no_suggestion():
    assert _suggest(contact_type="tenant", company_name="Stream Realty")["type"] is None
    assert _suggest(contact_type="counterparty")["type"] is None


def test_listing_contacts_never_writes_the_suggestion_into_the_type(db_session, client):
    """SolaREIT reads like a landlord and leases space as a tenant. A keyword
    may pre-select counterparty; it must never decide it."""
    sola = _company(db_session, "SolaREIT", "CO-SOLA")
    laura = _contact(db_session, "Laura Pagliarulo", company_id=sola.id)

    row = next(r for r in _list(client) if r["id"] == laura.id)
    assert row["contact_type"] == "unconfirmed"
    assert row["suggested_type"] == "counterparty"
    assert "reit" in row["suggested_type_reason"]

    db_session.expire_all()
    assert db_session.get(Contact, laura.id).contact_type == "unconfirmed"


# ══ 3. The email automation's guess ═══════════════════════════════════════════

def test_the_email_tasks_guess_is_stored_as_a_suggestion_only(db_session, client):
    _post_email(
        client, from_email="mike@shulerco.com", from_name="Mike Shuler",
        subject="Listing", source_message_id="<type-guess-1@mail>",
        primary_contact_type_guess="counterparty",
    )
    mike = _by_email(db_session, "mike@shulerco.com")
    assert mike.contact_type == "unconfirmed"
    assert mike.suggested_type == "counterparty"

    row = next(r for r in _list(client) if r["id"] == mike.id)
    assert row["suggested_type"] == "counterparty"


def test_an_unusable_guess_is_dropped_without_failing_the_email(db_session, client):
    body = _post_email(
        client, from_email="odd@example-co.com", from_name="Odd One",
        subject="Hello", source_message_id="<type-guess-bad@mail>",
        primary_contact_type_guess="landlord broker maybe",
    )
    assert body["id"]
    assert _by_email(db_session, "odd@example-co.com").suggested_type is None


def test_a_guess_never_touches_a_contact_jack_already_classified(db_session, client):
    _contact(db_session, "Dana Reed", email="dana@reedlaw.com",
             contact_type="tenant", auto_created=False, triaged=True)
    _post_email(
        client, from_email="dana@reedlaw.com", from_name="Dana Reed",
        subject="Re: space", source_message_id="<type-guess-settled@mail>",
        primary_contact_type_guess="counterparty",
    )
    dana = _by_email(db_session, "dana@reedlaw.com")
    assert dana.contact_type == "tenant"
    assert dana.suggested_type is None


def test_a_deal_can_carry_a_guess_for_the_contact_it_names(db_session, client):
    _post_email(
        client,
        from_email="ann.waller@crgnova.com", from_name="Ann Waller",
        subject="Leasing notes", source_message_id="<type-deal-1@mail>",
        deals=[{
            "company_override": "Harbor Dental",
            "action_taken": "Harbor Dental signed LOI.",
            "contact_email": "owner@harbordental.com",
            "contact_name": "Harbor Owner",
            "contact_type_guess": "tenant",
        }],
    )
    assert _by_email(db_session, "owner@harbordental.com").suggested_type == "tenant"


# ══ 4. Confirming — one person, or the whole firm ═════════════════════════════

def test_confirming_sets_the_type_clears_the_suggestion_and_does_not_triage(db_session, client):
    c = _contact(db_session, "Cliff Wall", suggested_type="tenant")
    resp = client.post(f"/api/contacts/{c.id}/confirm-type", json={"contact_type": "tenant"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contact"]["contact_type"] == "tenant"
    assert body["contact"]["suggested_type"] is None
    assert body["firm_updated"] == 0

    db_session.expire_all()
    stored = db_session.get(Contact, c.id)
    assert stored.contact_type == "tenant"
    # Classifying someone is housekeeping, not working them.
    assert stored.triaged is False


def test_confirming_reports_who_is_left_unconfirmed_at_the_firm(db_session, client):
    sola = _company(db_session, "SolaREIT", "CO-SOLA")
    beth = _contact(db_session, "Beth", company_id=sola.id)
    _contact(db_session, "Laura", company_id=sola.id)
    _contact(db_session, "Francisco", company_id=sola.id)

    body = client.post(
        f"/api/contacts/{beth.id}/confirm-type", json={"contact_type": "counterparty"},
    ).json()
    assert body["firm_unconfirmed"] == 2
    assert body["firm_name"] == "SolaREIT"
    # Not applied to the firm, so the firm is still unmarked.
    assert body["firm_type"] is None


def test_applying_to_the_firm_reaches_only_people_still_unconfirmed(db_session, client):
    simpson = _company(db_session, "Simpson Properties", "CO-SIMP")
    ann = _contact(db_session, "Ann Waller", company_id=simpson.id)
    crystal = _contact(db_session, "Crystal Briscoe", company_id=simpson.id)
    # Jack already said this one is a tenant; a firm-wide click must not undo it.
    kept = _contact(db_session, "Fred", company_id=simpson.id, contact_type="tenant")
    elsewhere = _contact(db_session, "Someone Else")

    body = client.post(
        f"/api/contacts/{ann.id}/confirm-type",
        json={"contact_type": "counterparty", "apply_to_firm": True},
    ).json()
    assert body["firm_updated"] == 1
    assert body["firm_unconfirmed"] == 0
    assert body["firm_type"] == "counterparty"

    db_session.expire_all()
    assert db_session.get(Contact, crystal.id).contact_type == "counterparty"
    assert db_session.get(Contact, kept.id).contact_type == "tenant"
    assert db_session.get(Contact, elsewhere.id).contact_type == "unconfirmed"
    assert db_session.get(Company, simpson.id).company_type == "counterparty"


def test_applying_to_a_firm_needs_a_firm(db_session, client):
    c = _contact(db_session, "No Company")
    resp = client.post(
        f"/api/contacts/{c.id}/confirm-type",
        json={"contact_type": "counterparty", "apply_to_firm": True},
    )
    assert resp.status_code == 400
    db_session.expire_all()
    assert db_session.get(Contact, c.id).contact_type == "unconfirmed"


@pytest.mark.parametrize("bad", ["unconfirmed", "owner", "broker", ""])
def test_only_tenant_or_counterparty_can_be_confirmed(db_session, client, bad):
    c = _contact(db_session, "Someone")
    resp = client.post(f"/api/contacts/{c.id}/confirm-type", json={"contact_type": bad})
    assert resp.status_code == 400


# ══ 5. Filters mean what they say ═════════════════════════════════════════════

def test_a_tenant_filter_never_returns_an_unconfirmed_contact(db_session, client):
    t = _contact(db_session, "Real Tenant", contact_type="tenant")
    u = _contact(db_session, "Unknown")
    tenant_ids = {r["id"] for r in _list(client, contact_type="tenant")}
    assert t.id in tenant_ids and u.id not in tenant_ids

    unconfirmed_ids = {r["id"] for r in _list(client, contact_type="unconfirmed")}
    assert unconfirmed_ids == {u.id}


def test_company_cards_are_not_returned_for_the_unconfirmed_filter(db_session, client):
    resp = client.get("/api/contacts/company-cards", params={"contact_type": "unconfirmed"})
    assert resp.status_code == 200
    assert resp.json() == []


# ══ 6. No tenant outreach for a counterparty firm ═════════════════════════════

def test_no_tenant_outreach_is_drafted_for_a_firm_marked_counterparty(db_session, client):
    _company(db_session, "Avison Young", "CO-AY", company_type="counterparty",
             current_sf_occupied=5000, lease_expiry_months=7)
    resp = client.post("/api/companies/CO-AY/draft-outreach")
    assert resp.status_code == 422
    assert "counterparty" in resp.json()["detail"]


# ══ 7. The one-time migration ═════════════════════════════════════════════════

def _raw_contacts(cur):
    cur.execute(
        "CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT, "
        "contact_type TEXT NOT NULL DEFAULT 'tenant', auto_created BOOLEAN NOT NULL DEFAULT 0)"
    )
    cur.executemany(
        "INSERT INTO contacts (id, name, contact_type, auto_created) VALUES (?, ?, ?, ?)",
        [
            (1, "Auto tenant", "tenant", 1),        # never classified → unconfirmed
            (2, "Hand-made tenant", "tenant", 0),   # Jack's own → kept
            (3, "Auto counterparty", "counterparty", 1),  # Jack already moved → kept
        ],
    )


def _types(cur):
    cur.execute("SELECT id, contact_type FROM contacts ORDER BY id")
    return dict(cur.fetchall())


def test_migration_moves_only_auto_created_tenants():
    from migrations.ensure_schema import migrate_contact_types_to_unconfirmed

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    _raw_contacts(cur)
    conn.commit()

    assert migrate_contact_types_to_unconfirmed(cur) > 0
    conn.commit()
    assert _types(cur) == {1: "unconfirmed", 2: "tenant", 3: "counterparty"}


def test_migration_runs_once_and_never_resets_a_later_confirmation():
    from migrations.ensure_schema import migrate_contact_types_to_unconfirmed

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    _raw_contacts(cur)
    conn.commit()
    migrate_contact_types_to_unconfirmed(cur)
    conn.commit()

    # Jack confirms the auto-created contact as a tenant...
    cur.execute("UPDATE contacts SET contact_type = 'tenant' WHERE id = 1")
    conn.commit()
    # ...and the next startup must leave that alone.
    assert migrate_contact_types_to_unconfirmed(cur) == 0
    conn.commit()
    assert _types(cur)[1] == "tenant"


def test_a_failed_migration_rolls_back_the_rewrite_with_the_column():
    """The column's absence is the run-once guard, so the two writes must land
    together or not at all — otherwise a failure could leave the guard in place
    with the rewrite never done."""
    from migrations.ensure_schema import _has_column, migrate_contact_types_to_unconfirmed

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    _raw_contacts(cur)
    conn.commit()

    class FailingSecondWrite:
        """Fails whichever write comes SECOND, so the test holds against either
        statement order: with the ALTER first, sqlite3 autocommits it, the
        rollback cannot undo it, and this test fails."""
        def __init__(self, inner):
            self._inner = inner
            self._writes = 0
        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith(("ALTER", "UPDATE")):
                self._writes += 1
                if self._writes == 2:
                    raise sqlite3.OperationalError("simulated failure")
            return self._inner.execute(sql, *args)
        def __getattr__(self, name):
            return getattr(self._inner, name)

    with pytest.raises(sqlite3.OperationalError):
        migrate_contact_types_to_unconfirmed(FailingSecondWrite(cur))
    conn.rollback()

    assert not _has_column(cur, "contacts", "suggested_type")
    assert _types(cur)[1] == "tenant"
