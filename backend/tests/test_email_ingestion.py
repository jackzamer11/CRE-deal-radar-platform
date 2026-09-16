"""Email ingestion — the full interpreted record, and the lines it must not cross.

The scheduled mailbox task writes an interpreted email into Deal Radar through
POST /api/activity/from-email. This file locks the behaviours that would be
silently wrong if they broke, in the order they matter:

  - facts and discovery capture write; stated COMPANY values never do
  - a stated value queues with both sides and the sentence it came from
  - accept writes it conversation-sourced; reject keeps the record and marks it
  - a conversation-sourced value survives a CoStar import
  - a brand-new company needs no confirmation — there is nothing to conflict with
  - To is correspondence, Cc is participation, Bcc is nothing
  - a copied recipient who later replies becomes a real relationship
  - a recipient's company comes from THEIR domain, not the entry's stamp
  - next_touch_date is set when sent and never inferred
  - a reassignment teaches the resolver
  - an address Jack owns never becomes a contact
  - an attachment stores a filename and a year, never a path
  - search reaches the linked contact and company names
  - company_override beats the sender's domain
  - a partial payload writes nothing at all
  - /api/companies/ still serves its seven contract fields

In-memory SQLite, a temp directory for files, dependency-overridden get_db. No
live database, no network, no CoStar file off disk, no model calls.
"""
import io
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
from app.models.email_ingest import (
    CONVERSATION_SOURCE, ActivityAttachment, ContactAddressOverride,
    PendingCompanyUpdate,
)
from app.services import attachment_storage
from app.services.email_ingest_service import is_own_address


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
    """Point DOCUMENTS_FOLDER at a temp directory for the whole test.

    Patched on `attachment_storage.settings` — the binding the storage module
    itself holds — rather than on a freshly imported `app.config.settings`.
    They are not always the same object: test_benchmarks.py calls
    importlib.reload(app.config), which rebinds app.config.settings while every
    module that did `from app.config import settings` at import time keeps the
    original. Patching the module's own binding is correct whichever ran first,
    and without it this test writes real files into Jack's OneDrive.
    """
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(tmp_path),
        raising=False,
    )
    # Autouse and asserted: no test in this file may reach the real folder, and
    # a patch that silently fails to bite must fail HERE rather than quietly
    # writing a file into Jack's documents.
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


def _post_email(client, **payload):
    payload.setdefault("direction", "inbound")
    resp = client.post("/api/activity/from-email", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ══ 1. What writes and what waits ═════════════════════════════════════════════

def test_facts_and_discovery_write_but_the_company_record_does_not(db_session, client):
    """The core division of the whole build.

    Facts are prose about a person and discovery fields are inert capture — both
    write straight through. headcount, growth, lease expiry and SF are scoring
    inputs, and a sentence in an email is not verified data, so they wait.
    """
    company = _company(
        db_session, "Collaborative AV", "CO-900",
        current_headcount=18, current_sf_occupied=4200,
        lease_expiry_date=date(2027, 3, 1),
    )

    body = _post_email(
        client,
        from_email="dana@collaborative-av.com",
        from_name="Dana Reyes",
        to_email="jzamer@z-reg.com",
        subject="Space",
        action_taken="Dana walked through what they need.",
        source_message_id="<msg-facts-1@mail>",
        facts=[
            {"text": "Runs facilities for both offices"},
            {"text": "Prefers a call to an email"},
        ],
        disc_current_rent_psf=38.5,
        disc_current_sf=4200,
        disc_decision_timeline="Wants to decide by spring",
        proposed_company_updates=[
            {"field": "headcount", "value": "40",
             "source_sentence": "We're up to 40 people now."},
        ],
    )

    assert body["facts_written"] == 2
    assert body["pending_updates_created"] == 1
    assert body["disc_current_rent_psf"] == 38.5
    assert body["disc_current_sf"] == 4200
    assert body["disc_decision_timeline"] == "Wants to decide by spring"

    facts = db_session.query(ContactFact).all()
    assert {f.fact_text for f in facts} == {
        "Runs facilities for both offices", "Prefers a call to an email",
    }
    # Every fact traces back to the entry it came from.
    assert all(f.source_entry_id == body["id"] for f in facts)

    # Nothing reached the company record.
    db_session.refresh(company)
    assert company.current_headcount == 18
    assert company.current_sf_occupied == 4200
    assert company.lease_expiry_date == date(2027, 3, 1)
    assert company.current_headcount_source is None


def test_a_proposed_update_queues_with_both_values_and_its_sentence(db_session, client):
    company = _company(db_session, "Northline Legal", "CO-901", current_headcount=25)

    _post_email(
        client,
        from_email="paul@northline-legal.com",
        from_name="Paul Ito",
        source_message_id="<msg-pending-1@mail>",
        action_taken="Paul mentioned their growth.",
        proposed_company_updates=[
            {"field": "headcount", "value": "31",
             "source_sentence": "We just crossed 31 attorneys and staff."},
        ],
    )

    row = db_session.query(PendingCompanyUpdate).one()
    assert row.company_id == company.id
    assert row.field == "headcount"
    assert row.proposed_value == "31"
    assert row.current_value == "25"          # both sides, side by side
    assert row.source_sentence == "We just crossed 31 attorneys and staff."
    assert row.source_entry_id is not None
    assert row.status == "pending"

    # And the digest the scheduled task reads can count it.
    digest = client.get("/api/pending-updates/").json()
    assert digest["total"] == 1
    assert digest["updates"][0]["label"] == "headcount"
    assert digest["updates"][0]["current_value"] == "25"


def test_a_stated_value_matching_the_record_does_not_queue(db_session, client):
    """A queue that asks about agreements is a queue Jack stops reading."""
    _company(db_session, "Samefig Co", "CO-902", current_headcount=25)
    body = _post_email(
        client,
        from_email="sam@samefig-co.com",
        source_message_id="<msg-same-1@mail>",
        action_taken="Sam confirmed the headcount.",
        proposed_company_updates=[{"field": "headcount", "value": "25"}],
    )
    assert body["pending_updates_created"] == 0
    assert db_session.query(PendingCompanyUpdate).count() == 0


# ══ 2. Accept and reject ══════════════════════════════════════════════════════

def test_accepting_writes_the_value_with_a_conversation_source_marker(db_session, client):
    company = _company(db_session, "Accepted Co", "CO-903", current_headcount=25)
    _post_email(
        client,
        from_email="lee@accepted-co.com",
        source_message_id="<msg-accept-1@mail>",
        action_taken="Lee gave a number.",
        proposed_company_updates=[
            {"field": "headcount", "value": "31", "source_sentence": "We're 31 now."},
        ],
    )
    row = db_session.query(PendingCompanyUpdate).one()

    resp = client.post(f"/api/pending-updates/{row.id}/accept")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"

    db_session.refresh(company)
    assert company.current_headcount == 31
    # The marker is the point: Jack must be able to see the number came from
    # something someone said in an email.
    assert company.current_headcount_source == CONVERSATION_SOURCE
    assert company.current_headcount_source not in ("costar", "lease_document", "manual")


def test_accepting_a_lease_expiry_keeps_the_months_column_in_step(db_session, client):
    """lease_expiry_months is what scoring reads — it cannot go stale."""
    company = _company(
        db_session, "Expiry Co", "CO-904", lease_expiry_date=date(2027, 1, 1),
    )
    target = date.today() + timedelta(days=210)
    _post_email(
        client,
        from_email="kim@expiry-co.com",
        source_message_id="<msg-expiry-1@mail>",
        action_taken="Kim named a date.",
        proposed_company_updates=[
            {"field": "lease_expiry", "value": target.isoformat()},
        ],
    )
    row = db_session.query(PendingCompanyUpdate).one()
    client.post(f"/api/pending-updates/{row.id}/accept")

    db_session.refresh(company)
    assert company.lease_expiry_date == target
    assert company.lease_expiry_months is not None
    assert company.lease_expiry_source == CONVERSATION_SOURCE


def test_rejecting_leaves_the_field_keeps_the_fact_and_marks_the_conflict(db_session, client):
    """A tenant who disagrees with the record is a lead, not a data-entry error."""
    company = _company(db_session, "Rejected Co", "CO-905", current_headcount=25)
    _post_email(
        client,
        from_email="mo@rejected-co.com",
        source_message_id="<msg-reject-1@mail>",
        action_taken="Mo claimed a bigger team.",
        facts=[{"text": "Says the team is far bigger than our record"}],
        proposed_company_updates=[
            {"field": "headcount", "value": "80",
             "source_sentence": "We're about 80 across the two floors."},
        ],
    )
    row = db_session.query(PendingCompanyUpdate).one()

    resp = client.post(f"/api/pending-updates/{row.id}/reject")
    assert resp.status_code == 200, resp.text

    db_session.refresh(company)
    assert company.current_headcount == 25            # untouched
    assert company.current_headcount_source is None
    assert company.has_data_conflict is True          # surfaced, not discarded

    # The claim stays on the thread where it was said.
    assert db_session.query(ContactFact).filter(
        ContactFact.fact_text == "Says the team is far bigger than our record"
    ).count() == 1
    db_session.refresh(row)
    assert row.status == "rejected"
    assert row.source_sentence == "We're about 80 across the two floors."

    # Resolved either way, it stops nagging.
    assert client.get("/api/pending-updates/").json()["total"] == 0


# ══ 3. A conversation-sourced value survives CoStar ═══════════════════════════

_COSTAR_HEADER = (
    "Address,Tenant Name,Industry,Employees,Website,Submarket,SF Occupied,NAICS,"
    "City,State,Zip,Best Tenant Contact,Best Tenant Phone,Tenant Representative,"
    "Next Break Date,Rent/SF/year,Future Move,Future Move Type"
)


def _costar_csv(name, address, employees, sf):
    return (
        f"{_COSTAR_HEADER}\n"
        f"{address},{name},Tech,{employees},,Tysons,{sf},,Tysons,VA,22102,,,,,,,"
        "\n"
    )


def test_a_conversation_sourced_value_survives_a_costar_import(db_session, client):
    """Jack already made this call. Re-importing must not quietly unmake it."""
    company = _company(
        db_session, "Survivor Co", "CO-906",
        current_address="1750 Tysons Blvd", current_submarket="Tysons",
        current_headcount=25, current_sf_occupied=4000,
    )
    _post_email(
        client,
        from_email="ann@survivor-co.com",
        source_message_id="<msg-survive-1@mail>",
        action_taken="Ann gave both numbers.",
        company_override_id=company.id,
        proposed_company_updates=[
            {"field": "headcount", "value": "31"},
            {"field": "sf", "value": "5200"},
        ],
    )
    for row in db_session.query(PendingCompanyUpdate).all():
        assert client.post(f"/api/pending-updates/{row.id}/accept").status_code == 200

    db_session.refresh(company)
    assert (company.current_headcount, company.current_sf_occupied) == (31, 5200)

    csv = _costar_csv("Survivor Co", "1750 Tysons Blvd", 25, 4000)
    resp = client.post(
        "/api/companies/costar-import",
        files={"file": ("tenants.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] >= 1          # the row WAS matched and processed

    db_session.refresh(company)
    assert company.current_headcount == 31      # CoStar's 25 did not win
    assert company.current_sf_occupied == 5200  # CoStar's 4000 did not win
    assert company.current_headcount_source == CONVERSATION_SOURCE


def test_costar_still_overwrites_an_unconfirmed_value(db_session, client):
    """The guard fires only on a value Jack confirmed — nothing else changes."""
    company = _company(
        db_session, "Plainco", "CO-907",
        current_address="1750 Tysons Blvd", current_submarket="Tysons",
        current_headcount=10, current_sf_occupied=1000,
    )
    csv = _costar_csv("Plainco", "1750 Tysons Blvd", 44, 7000)
    resp = client.post(
        "/api/companies/costar-import",
        files={"file": ("tenants.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(company)
    assert company.current_headcount == 44
    assert company.current_sf_occupied == 7000


# ══ 4. A new company needs no confirmation ════════════════════════════════════

def test_a_new_company_gets_the_stated_values_written_directly(db_session, client):
    """Confirmation resolves conflicts. With no existing record there is none."""
    body = _post_email(
        client,
        from_email="rita@brand-new-tenant.com",
        from_name="Rita Alvarez",
        source_message_id="<msg-new-1@mail>",
        action_taken="Rita introduced the company.",
        proposed_company_updates=[
            {"field": "headcount", "value": "12", "source_sentence": "There are 12 of us."},
            {"field": "sf", "value": "3100"},
        ],
    )
    assert body["pending_updates_created"] == 0
    assert sorted(body["company_values_written"]) == ["headcount", "sf"]
    assert db_session.query(PendingCompanyUpdate).count() == 0

    company = db_session.query(Company).filter(
        Company.email_domain == "brand-new-tenant.com"
    ).one()
    assert company.current_headcount == 12
    assert company.current_sf_occupied == 3100
    assert company.current_headcount_source == CONVERSATION_SOURCE
    # Created untriaged, with no guessed type — Jack sets that.
    assert company.triaged is False
    assert company.auto_created is True
    assert company.company_type is None


# ══ 5. To versus Cc versus Bcc ════════════════════════════════════════════════

def test_a_to_recipient_is_correspondence_and_a_cc_recipient_is_not(db_session, client):
    """Someone copied on an email has no relationship with Jack.

    The To recipient's entry counts toward last touch and moves the stage. The
    Cc recipient's does not, and does not set responded — their header has to
    read "copied, never directly contacted", not "active".
    """
    body = _post_email(
        client,
        direction="inbound",
        from_email="dana@collaborative-av.com",
        from_name="Dana Reyes",
        to_recipients=[{"email": "jzamer@z-reg.com"}],
        cc_recipients=[{"email": "broker@avisonyoung.com", "name": "Ray Poole"}],
        source_message_id="<msg-cc-1@mail>",
        action_taken="Dana looped in her broker.",
    )
    assert len(body["participation_entry_ids"]) == 1

    dana = db_session.query(Contact).filter(
        Contact.email == "dana@collaborative-av.com"
    ).one()
    ray = db_session.query(Contact).filter(
        Contact.email == "broker@avisonyoung.com"
    ).one()

    # The sender owns the entry: inbound, so responded and Sent → Replied.
    assert dana.responded is True
    assert dana.stage == "Replied"

    # The copied broker is untouched by all of it.
    assert ray.responded is False
    assert ray.stage == "Sent"

    dana_header = client.get(f"/api/contacts/{dana.id}").json()
    ray_header = client.get(f"/api/contacts/{ray.id}").json()

    assert dana_header["entry_count"] == 1
    assert dana_header["last_touch_date"] is not None
    assert dana_header["days_of_silence"] is not None
    assert dana_header["copied_only"] is False

    assert ray_header["entry_count"] == 0          # counts toward nothing
    assert ray_header["last_touch_date"] is None
    assert ray_header["days_of_silence"] is None
    assert ray_header["copied_count"] == 1
    assert ray_header["copied_only"] is True       # "copied, never contacted"

    # It is still on his timeline as history, flagged distinctly.
    timeline = client.get(f"/api/contacts/{ray.id}/timeline").json()
    assert timeline["total"] == 1
    assert timeline["entries"][0]["participation"] is True

    # And the list row says the same thing.
    row = next(r for r in client.get("/api/contacts/").json() if r["id"] == ray.id)
    assert row["entry_count"] == 0
    assert row["copied_count"] == 1
    assert row["copied_only"] is True


def test_a_copied_recipient_who_replies_becomes_a_real_relationship(db_session, client):
    """The copies stay as history; the reply is where the relationship starts."""
    for n in range(3):
        _post_email(
            client,
            direction="outbound",
            from_email="jzamer@z-reg.com",
            to_recipients=[{"email": "dana@collaborative-av.com"}],
            cc_recipients=[{"email": "broker@avisonyoung.com", "name": "Ray Poole"}],
            source_message_id=f"<msg-loop-{n}@mail>",
            action_taken=f"Sent the summary, round {n}.",
        )

    ray = db_session.query(Contact).filter(
        Contact.email == "broker@avisonyoung.com"
    ).one()
    before = client.get(f"/api/contacts/{ray.id}").json()
    assert before["entry_count"] == 0
    assert before["copied_count"] == 3
    assert before["copied_only"] is True
    assert ray.stage == "Sent"

    # Now he writes in himself.
    _post_email(
        client,
        direction="inbound",
        from_email="broker@avisonyoung.com",
        from_name="Ray Poole",
        to_recipients=[{"email": "jzamer@z-reg.com"}],
        source_message_id="<msg-ray-reply@mail>",
        action_taken="Ray replied with his client's requirements.",
    )

    db_session.refresh(ray)
    after = client.get(f"/api/contacts/{ray.id}").json()
    assert ray.stage == "Replied"
    assert ray.responded is True
    assert after["entry_count"] == 1
    assert after["last_touch_date"] is not None
    assert after["copied_only"] is False
    # The three copies are still there behind it.
    assert after["copied_count"] == 3
    assert client.get(f"/api/contacts/{ray.id}/timeline").json()["total"] == 4


def test_bcc_recipients_create_no_contact_and_no_entry(db_session, client):
    _post_email(
        client,
        direction="outbound",
        from_email="jzamer@z-reg.com",
        to_recipients=[{"email": "dana@collaborative-av.com"}],
        bcc_recipients=[
            {"email": "hidden@secret-third-party.com", "name": "Silent Sam"},
        ],
        source_message_id="<msg-bcc-1@mail>",
        action_taken="Sent the proposal.",
    )

    assert db_session.query(Contact).filter(
        Contact.email == "hidden@secret-third-party.com"
    ).count() == 0
    assert db_session.query(Company).filter(
        Company.email_domain == "secret-third-party.com"
    ).count() == 0
    # Not recorded anywhere — no entry carries the address either.
    assert db_session.query(ActivityLog).filter(
        ActivityLog.sender_email == "hidden@secret-third-party.com"
    ).count() == 0
    assert db_session.query(ActivityLog).count() == 1


def test_a_recipients_company_comes_from_their_own_domain(db_session, client):
    """A broker at Avison Young copied on a Collaborative AV email lands under
    Avison Young — never under the company the entry is stamped to."""
    body = _post_email(
        client,
        direction="inbound",
        from_email="dana@collaborative-av.com",
        from_name="Dana Reyes",
        cc_recipients=[{"email": "ray@avisonyoung.com", "name": "Ray Poole"}],
        source_message_id="<msg-domain-1@mail>",
        action_taken="Dana looped in her broker.",
    )

    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()
    ray_company = db_session.query(Company).filter(Company.id == ray.company_id).one()
    assert ray_company.email_domain == "avisonyoung.com"
    assert ray_company.name != body["company_name"]

    # The entry is still stamped to the company the conversation was about.
    stamped = db_session.query(ActivityLog).filter(
        ActivityLog.contact_id == ray.id
    ).one()
    entry_company = db_session.query(Company).filter(
        Company.id == stamped.company_stamp_id
    ).one()
    assert entry_company.email_domain == "collaborative-av.com"


def test_a_direct_listing_beats_a_copied_one(db_session, client):
    """On both the To and the Cc line means written to."""
    _post_email(
        client,
        direction="outbound",
        from_email="jzamer@z-reg.com",
        to_recipients=[{"email": "both@example-tenant.com"}],
        cc_recipients=[{"email": "both@example-tenant.com"}],
        source_message_id="<msg-both-1@mail>",
        action_taken="Sent it.",
    )
    entries = db_session.query(ActivityLog).all()
    assert len(entries) == 1
    assert entries[0].participation is False


# ══ 6. next_touch_date ════════════════════════════════════════════════════════

def test_next_touch_date_is_set_when_sent(db_session, client):
    target = date.today() + timedelta(days=9)
    _post_email(
        client,
        from_email="tom@followup-co.com",
        source_message_id="<msg-ntd-1@mail>",
        action_taken="Tom asked for a call on the 24th.",
        next_touch_date=target.isoformat(),
    )
    contact = db_session.query(Contact).filter(
        Contact.email == "tom@followup-co.com"
    ).one()
    assert contact.next_touch_date == target


def test_next_touch_date_is_never_inferred_when_absent(db_session, client):
    """The task only sends it when the email named a specific day. Nothing here
    guesses one from "sometime next week"."""
    _post_email(
        client,
        from_email="vague@followup-co.com",
        source_message_id="<msg-ntd-2@mail>",
        action_taken="Said he would circle back at some point.",
        follow_up_action="Chase in a couple of weeks",
    )
    contact = db_session.query(Contact).filter(
        Contact.email == "vague@followup-co.com"
    ).one()
    assert contact.next_touch_date is None


# ══ 7. A reassignment teaches the resolver ════════════════════════════════════

def test_reassigning_an_entry_maps_that_address_to_the_new_contact(db_session, client):
    """Jack should never have to make the same correction twice."""
    first = _post_email(
        client,
        from_email="info@shared-inbox-co.com",
        from_name="Shared Inbox",
        source_message_id="<msg-assign-1@mail>",
        action_taken="Someone from the shared inbox wrote in.",
    )

    # The real person behind that shared mailbox.
    real = Contact(name="Nadia Farr", email="nadia@shared-inbox-co.com",
                   contact_type="tenant", stage="Sent")
    db_session.add(real)
    db_session.commit()
    db_session.refresh(real)

    resp = client.patch(
        f"/api/activity/{first['id']}/assign", json={"contact_id": real.id},
    )
    assert resp.status_code == 200, resp.text

    mapping = db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == "info@shared-inbox-co.com"
    ).one()
    assert mapping.contact_id == real.id

    # The next email from that address resolves there, before domain matching.
    second = _post_email(
        client,
        from_email="info@shared-inbox-co.com",
        from_name="Shared Inbox",
        source_message_id="<msg-assign-2@mail>",
        action_taken="They wrote in again.",
    )
    assert second["contact_id"] == real.id
    assert second["contact_name"] == "Nadia Farr"


def test_a_later_correction_replaces_an_earlier_one(db_session, client):
    entry = _post_email(
        client,
        from_email="desk@rotating-co.com",
        source_message_id="<msg-assign-3@mail>",
        action_taken="Wrote in from the desk address.",
    )
    first = Contact(name="First Person", email="first@rotating-co.com")
    second = Contact(name="Second Person", email="second@rotating-co.com")
    db_session.add_all([first, second])
    db_session.commit()

    client.patch(f"/api/activity/{entry['id']}/assign", json={"contact_id": first.id})
    client.patch(f"/api/activity/{entry['id']}/assign", json={"contact_id": second.id})

    rows = db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == "desk@rotating-co.com"
    ).all()
    assert len(rows) == 1               # one address, one mapping
    assert rows[0].contact_id == second.id


# ══ 8. Addresses Jack owns ════════════════════════════════════════════════════

@pytest.mark.parametrize("address", [
    "jzamer@z-reg.com",
    "JZamer@Z-Reg.com",              # case is not identity
    "anyone@z-reg.com",              # the whole domain is his
    "someone@simpsondev.com",
    "jackzamer1@gmail.com",          # free-mail: matched as an address, not a domain
])
def test_an_address_jack_owns_is_recognised(address):
    assert is_own_address(address) is True


@pytest.mark.parametrize("address", [
    "dana@collaborative-av.com",
    "someone-else@gmail.com",        # gmail.com as a whole is NOT his
    "jzamer@not-z-reg.com",
])
def test_a_real_contacts_address_is_not(address):
    assert is_own_address(address) is False


def test_an_address_jack_owns_never_resolves_to_a_contact(db_session, client):
    """The entry is still logged — unattached, rather than filed under a contact
    called "Jack Zamer"."""
    body = _post_email(
        client,
        direction="outbound",
        from_email="jzamer@z-reg.com",
        to_email="jzamer@simpsondev.com",
        source_message_id="<msg-own-1@mail>",
        action_taken="Note to self about the Tysons listing.",
    )
    assert body["contact_id"] is None
    assert sorted(body["skipped_own_addresses"]) == [
        "jzamer@simpsondev.com", "jzamer@z-reg.com",
    ]
    assert db_session.query(Contact).count() == 0
    assert db_session.query(Company).count() == 0
    # The entry itself survives.
    assert db_session.query(ActivityLog).count() == 1


def test_jacks_own_address_is_never_mapped_by_a_reassignment(db_session, client):
    """Teaching the resolver to file Jack's own mail under a contact is the exact
    thing the guard exists to prevent."""
    body = _post_email(
        client,
        direction="outbound",
        from_email="jzamer@z-reg.com",
        to_email="dana@collaborative-av.com",
        source_message_id="<msg-own-2@mail>",
        action_taken="Sent the summary.",
    )
    other = Contact(name="Someone Else", email="else@elsewhere-co.com")
    db_session.add(other)
    db_session.commit()

    client.patch(f"/api/activity/{body['id']}/assign", json={"contact_id": other.id})
    assert db_session.query(ContactAddressOverride).filter(
        ContactAddressOverride.email == "jzamer@z-reg.com"
    ).count() == 0


# ══ 9. Attachments ════════════════════════════════════════════════════════════

def test_an_attachment_stores_a_filename_and_a_year_never_a_path(
    db_session, client, docs_folder, tmp_path,
):
    source = tmp_path / "inbox" / "Floor Plan.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.4 fake")

    body = _post_email(
        client,
        from_email="dana@collaborative-av.com",
        source_message_id="<msg-att-1@mail>",
        action_taken="Dana sent the floor plan.",
        sent_at="2026-04-02",
        attachments=[{
            "filename": "Floor Plan.pdf",
            "stored_path": str(source),
            "description": "Suite 400 test fit",
        }],
    )
    assert body["attachments_saved"] == 1
    assert body["attachments_missing"] == []

    row = db_session.query(ActivityAttachment).one()
    assert row.file_name == "Floor Plan.pdf"
    assert row.stored_year == 2026
    assert row.description == "Suite 400 test fit"

    # No absolute path, no machine name, no user-specific value in any column.
    for value in (row.file_name, row.description):
        assert ":" not in str(value)
        assert "\\" not in str(value)
        assert "/" not in str(value)
        assert "Jackz" not in str(value)

    # It resolves through the configured folder, subfoldered by year.
    resolved = attachment_storage.resolve_attachment_path(row.file_name, row.stored_year)
    assert resolved == str(docs_folder / "2026" / "Floor Plan.pdf")
    assert attachment_storage.attachment_file_exists(row.file_name, row.stored_year)
    assert (docs_folder / "2026" / "Floor Plan.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_moving_the_documents_folder_repoints_every_row(
    db_session, client, docs_folder, tmp_path, monkeypatch,
):
    """The folder is a setting read at call time, so this is a one-line change
    and never a migration over stored rows."""
    source = tmp_path / "in" / "Notes.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x")
    _post_email(
        client,
        from_email="dana@collaborative-av.com",
        source_message_id="<msg-att-move@mail>",
        action_taken="Sent notes.",
        sent_at="2026-04-02",
        attachments=[{"filename": "Notes.pdf", "stored_path": str(source)}],
    )
    row = db_session.query(ActivityAttachment).one()

    moved = tmp_path / "moved"
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(moved), raising=False,
    )
    assert attachment_storage.resolve_attachment_path(
        row.file_name, row.stored_year
    ) == str(moved / "2026" / "Notes.pdf")


def test_inline_images_are_excluded(db_session, client, docs_folder):
    body = _post_email(
        client,
        from_email="dana@collaborative-av.com",
        source_message_id="<msg-att-2@mail>",
        action_taken="Dana replied.",
        attachments=[
            {"filename": "signature-logo.png", "inline": True},
            {"filename": "Term Sheet.pdf", "description": "Draft terms"},
        ],
    )
    assert body["attachments_saved"] == 1
    names = [a.file_name for a in db_session.query(ActivityAttachment).all()]
    assert names == ["Term Sheet.pdf"]


def test_a_missing_source_file_still_records_what_arrived(db_session, client, docs_folder):
    """Losing the record of what arrived because the copy failed is the worse
    outcome — the filename and description are what Jack actually reads."""
    body = _post_email(
        client,
        from_email="dana@collaborative-av.com",
        source_message_id="<msg-att-3@mail>",
        action_taken="Dana sent something.",
        attachments=[{
            "filename": "Gone.pdf",
            "stored_path": "/nowhere/at/all/Gone.pdf",
            "description": "Could not be copied",
        }],
    )
    assert body["attachments_saved"] == 1
    assert body["attachments_missing"] == ["Gone.pdf"]
    row = db_session.query(ActivityAttachment).one()
    assert row.file_name == "Gone.pdf"
    assert not attachment_storage.attachment_file_exists(row.file_name, row.stored_year)


def test_an_attachment_never_routes_into_the_lease_flow(db_session, client, docs_folder):
    """The task cannot tell draft eleven from the executed copy, so it never
    decides. Only Jack uploads an executed lease, deliberately."""
    from app.models.lease import Lease
    _post_email(
        client,
        from_email="dana@collaborative-av.com",
        source_message_id="<msg-att-4@mail>",
        action_taken="Dana sent the lease draft.",
        attachments=[{"filename": "Executed Lease FINAL.pdf",
                      "description": "Says final, is draft eleven"}],
    )
    assert db_session.query(Lease).count() == 0
    company = db_session.query(Company).filter(
        Company.email_domain == "collaborative-av.com"
    ).one()
    assert company.lease_expiry_date is None


def test_a_traversing_filename_cannot_escape_the_folder(docs_folder):
    stored, year, _ = attachment_storage.store_attachment(
        "../../../etc/passwd", None, saved_on=date(2026, 5, 1),
    )
    assert stored == "passwd"
    resolved = attachment_storage.resolve_attachment_path(stored, year)
    assert resolved.startswith(str(docs_folder))


# ══ 10. Search reaches the linked names ═══════════════════════════════════════

def test_search_matches_a_linked_company_name_absent_from_the_summary(db_session, client):
    """Summaries are written cleanly now — the person and the company live as
    structured links rather than being repeated in the prose. Searching
    "Corcoran" must still find them."""
    _post_email(
        client,
        from_email="agent@corcoran-mcenearney.com",
        from_name="Beth Vaughan",
        source_message_id="<msg-search-1@mail>",
        action_taken="Confirmed the tour time for Thursday.",   # names neither
    )

    hits = client.get("/api/activity/", params={"q": "corcoran"}).json()
    assert len(hits) == 1
    assert "corcoran" not in hits[0]["action_taken"].lower()

    # And the contact's name, for the same reason.
    by_name = client.get("/api/activity/", params={"q": "Vaughan"}).json()
    assert len(by_name) == 1

    # Entry prose still matches, and an unrelated term still returns nothing.
    assert len(client.get("/api/activity/", params={"q": "tour time"}).json()) == 1
    assert client.get("/api/activity/", params={"q": "zzzznothing"}).json() == []


def test_search_finds_an_entry_by_its_stamped_company(db_session, client):
    company = _company(db_session, "Collaborative AV", "CO-910")
    _post_email(
        client,
        from_email="broker@some-brokerage.com",
        source_message_id="<msg-search-2@mail>",
        action_taken="Discussed the requirement.",
        company_override_id=company.id,
    )
    hits = client.get("/api/activity/", params={"q": "collaborative"}).json()
    assert len(hits) == 1


# ══ 11. company_override ══════════════════════════════════════════════════════

def test_company_override_stamps_the_named_company_not_the_senders_domain(db_session, client):
    """A broker at Avison Young writing about Collaborative AV is a conversation
    about Collaborative AV — while the broker stays under Avison Young."""
    target = _company(db_session, "Collaborative AV", "CO-911")

    body = _post_email(
        client,
        from_email="ray@avisonyoung.com",
        from_name="Ray Poole",
        source_message_id="<msg-override-1@mail>",
        action_taken="Ray is representing them on the search.",
        company_override="Collaborative AV",
    )
    assert body["company_stamp_id"] == target.id
    assert body["company_stamp_name"] == "Collaborative AV"

    ray = db_session.query(Contact).filter(Contact.email == "ray@avisonyoung.com").one()
    ray_company = db_session.query(Company).filter(Company.id == ray.company_id).one()
    assert ray_company.email_domain == "avisonyoung.com"


def test_company_override_matches_loosely_rather_than_creating_a_duplicate(db_session, client):
    target = _company(db_session, "Collaborative AV", "CO-912")
    before = db_session.query(Company).count()
    body = _post_email(
        client,
        from_email="ray@avisonyoung.com",
        source_message_id="<msg-override-2@mail>",
        action_taken="Ray wrote in.",
        company_override="Collaborative AV, LLC",
    )
    assert body["company_stamp_id"] == target.id
    # Avison Young is created for Ray himself; Collaborative AV is not duplicated.
    assert db_session.query(Company).filter(
        Company.name.like("Collaborative%")
    ).count() == 1
    assert db_session.query(Company).count() == before + 1


def test_an_unknown_company_override_id_is_a_404_and_writes_nothing(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "ray@avisonyoung.com",
        "direction": "inbound",
        "source_message_id": "<msg-override-3@mail>",
        "action_taken": "Ray wrote in.",
        "company_override_id": 999999,
    })
    assert resp.status_code == 404
    assert db_session.query(ActivityLog).count() == 0
    assert db_session.query(Contact).count() == 0


# ══ 12. One transaction ═══════════════════════════════════════════════════════

def test_a_partial_payload_failure_writes_nothing(db_session, client):
    """A half-written email is worse than an unwritten one: nothing downstream
    can tell it is half-written, and the message id dedup means it never comes
    back to be completed."""
    company = _company(db_session, "Atomic Co", "CO-913", current_headcount=25)
    before = {
        "entries":  db_session.query(ActivityLog).count(),
        "contacts": db_session.query(Contact).count(),
        "facts":    db_session.query(ContactFact).count(),
        "pending":  db_session.query(PendingCompanyUpdate).count(),
    }

    resp = client.post("/api/activity/from-email", json={
        "from_email": "pat@atomic-co.com",
        "from_name": "Pat Nowak",
        "direction": "inbound",
        "source_message_id": "<msg-atomic-1@mail>",
        "action_taken": "Pat sent an update.",
        "facts": [{"text": "Handles the lease personally"}],
        "disc_current_sf": 5000,
        "proposed_company_updates": [
            {"field": "headcount", "value": "31"},
            {"field": "not_a_real_field", "value": "boom"},   # fails here
        ],
    })
    assert resp.status_code == 400
    assert "not_a_real_field" in resp.text

    assert db_session.query(ActivityLog).count() == before["entries"]
    assert db_session.query(Contact).count() == before["contacts"]
    assert db_session.query(ContactFact).count() == before["facts"]
    assert db_session.query(PendingCompanyUpdate).count() == before["pending"]
    db_session.refresh(company)
    assert company.current_headcount == 25

    # And the message id is free, so the task's next run can log it properly.
    assert db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id == "<msg-atomic-1@mail>"
    ).count() == 0


def test_an_unreadable_stated_value_writes_nothing(db_session, client):
    resp = client.post("/api/activity/from-email", json={
        "from_email": "pat@atomic-co2.com",
        "direction": "inbound",
        "source_message_id": "<msg-atomic-2@mail>",
        "action_taken": "Pat sent an update.",
        "proposed_company_updates": [
            {"field": "lease_expiry", "value": "sometime next spring"},
        ],
    })
    assert resp.status_code == 400
    assert db_session.query(ActivityLog).count() == 0
    assert db_session.query(Contact).count() == 0
    assert db_session.query(Company).count() == 0


def test_the_same_message_is_never_logged_twice(db_session, client):
    payload = {
        "from_email": "dana@collaborative-av.com",
        "direction": "inbound",
        "source_message_id": "<msg-dupe-1@mail>",
        "action_taken": "Dana wrote in.",
        "cc_recipients": [{"email": "ray@avisonyoung.com"}],
    }
    assert client.post("/api/activity/from-email", json=payload).status_code == 200
    second = client.post("/api/activity/from-email", json=payload)
    assert second.status_code == 409
    # Two entries from the first call (Dana direct, Ray participation), and the
    # retry added none.
    assert db_session.query(ActivityLog).count() == 2


# ══ 13. Nothing else moved ════════════════════════════════════════════════════

def test_companies_list_still_returns_all_seven_contract_fields(db_session, client):
    """outreach_agent.py reads these by name. Renaming or dropping one breaks
    the CLI silently."""
    _company(
        db_session, "Contract Co", "CO-914",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Tysons", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    row = next(
        r for r in client.get("/api/companies/").json() if r["company_id"] == "CO-914"
    )
    assert row["company_id"] == "CO-914"
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42          # headcount
    assert row["headcount_growth_pct"] == 12.5     # growth_rate
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"    # submarket
    assert row["opportunity_score"] == 77.0        # score


def test_the_original_from_email_payload_still_works_unchanged(db_session, client):
    """Every new field is optional, so the mailbox task that exists today keeps
    working against this endpoint without being touched."""
    body = _post_email(
        client,
        from_email="Miriam Miller <Miriam@MM-RealEstate.com>",
        from_name="Miriam Miller",
        to_email="jzamer@z-reg.com",
        direction="inbound",
        subject="1205 N Pitt St",
        action_taken="Miriam reported two offers on the unit.",
        source_message_id="<msg-legacy-1@mail>",
    )
    assert body["contact_name"] == "Miriam Miller"
    assert body["company_name"] is not None
    assert body["participation"] is False
    assert body["facts_written"] == 0
    assert body["attachments_saved"] == 0


def test_message_ids_returns_only_bare_provider_ids(db_session, client):
    """One email writes one row per participant, but the task's dedup set must
    still be the provider's own ids — a suffixed id would never match an
    incoming message and the email would relog forever."""
    _post_email(
        client,
        direction="inbound",
        from_email="dana@collaborative-av.com",
        cc_recipients=[{"email": "ray@avisonyoung.com"}],
        source_message_id="<msg-ids-1@mail>",
        action_taken="Dana wrote in.",
    )
    ids = client.get("/api/activity/message-ids").json()
    assert "<msg-ids-1@mail>" in ids
    # The participation row's id is namespaced, never a rival bare id.
    assert [i for i in ids if i.startswith("<msg-ids-1@mail>#")]
    assert len([i for i in ids if i == "<msg-ids-1@mail>"]) == 1
