"""Closed stage, past-client re-entry, and the lease document link.

What this file locks, in the order the build specified it:

  1. The open loop derives from the NEWEST non-stage entry. An older entry's
     follow-up never surfaces when the newest has none — the live defect was a
     23 July "consider another follow-up" line sitting above a signed deal.
  2. Moving a contact to Closed sets closed_at and is_past_client; moving off
     Closed clears closed_at and leaves is_past_client True.
  3. Closed contacts are out of the default list and the active queue, and come
     back under the Closed filter.
  4. A past client whose company expiry lands in the 6-9 month window appears
     in the queue and the briefing with stage Closed and the past-client
     marker — never reset to Sent.
  5. A lease's file_name stores a BARE FILENAME; no absolute path reaches a column.
  6. Changing the configured leases folder re-points existing rows with no data
     change.
  7. A confirmed extraction writes expiry, address and SF, marked lease-sourced;
     unchecked values are not written.
  8. An extracted value with no supporting clause text is returned not-found and
     can never be written.
  9. Extraction failure still stores and links the file.
 10. A missing ANTHROPIC_API_KEY still stores and links the file, extraction
     skipped with a plain message.
 11. /api/companies/ still returns all seven contract fields.
 12. Removing a lease removes its record and deletes the file, works when the
     file is already gone, leaves confirmed company values and their
     lease-sourced markers alone, and 404s cleanly when there is no lease.

In-memory SQLite, dependency-overridden get_db, a tmp_path leases folder. No
live DB file, no network, no real Anthropic call, and no file written outside
the temp directory.
"""
import json
import os
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.config import settings
from app.database import Base, get_db
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact, CLOSED_STAGE, CONTACT_STAGES
from app.models.lease import Lease
from app.main import app
# The route module, not the service: routes/leases.py binds
# extract_lease_from_pdf by name at import, so that is the reference a stub has
# to replace. Patching the service module would leave the route calling the real
# function — and the whole point of these tests is that no network call happens.
from app.api.routes import leases as leases_route
from app.services import lease_extraction_service as lease_svc
from app.services import lease_storage
from app.services.lease_extraction_service import (
    COMPANY_WRITEBACK_FIELDS, LEASE_FIELDS, ExtractionUnavailable,
    MissingAPIKeyError, extract_lease,
)
from app.services.signal_engine import (
    is_in_peak_expiry_window, peak_window_date_bounds,
    sig_lease_expiry_proximity,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


@pytest.fixture()
def leases_dir(tmp_path, monkeypatch):
    """Point the leases folder at a temp directory for the whole test.

    Every test that touches storage uses this: nothing is ever written to Jack's
    real OneDrive folder from the suite.
    """
    folder = tmp_path / "Leases"
    folder.mkdir()
    monkeypatch.setattr(settings, "LEASES_FOLDER", str(folder))
    return folder


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


def _current(db, company):
    """The company's CURRENT lease row, or None.

    The lease link moved off the company record into the leases table, so
    every storage assertion reads the lease row — the same guarantee, checked
    where the data now lives.
    """
    db.expire_all()
    return (
        db.query(Lease)
        .filter(Lease.company_id == company.id, Lease.is_current.is_(True))
        .first()
    )


def _lease_count(db, company) -> int:
    db.expire_all()
    return db.query(Lease).filter(Lease.company_id == company.id).count()


def _in_window_date() -> date:
    """A lease expiry date that lands inside the 6-9 month window."""
    lo, _hi = peak_window_date_bounds()
    return lo + timedelta(days=5)


def _field(value, source_text, page=1):
    return {"value": value, "source_text": source_text, "page": page}


def _full_extraction(**overrides):
    """A believable nine-field model response, every field supported."""
    raw = {
        "lease_commencement_date": _field("March 1, 2022", "Term commences March 1, 2022."),
        "lease_expiration_date":   _field("June 30, 2027", "and expires June 30, 2027."),
        "premises_address":        _field(
            "1750 Tysons Blvd, McLean, VA", "the Premises at 1750 Tysons Blvd, McLean, VA",
        ),
        "suite_or_unit":           _field("Suite 400", "Suite 400 of the Building"),
        "rentable_square_footage": _field(
            "12,500 rentable square feet", "containing 12,500 rentable square feet",
        ),
        "base_rent":               _field("$412,500 per year", "Base Rent of $412,500 per annum"),
        "escalation_terms":        _field("3% annually", "increase by three percent (3%) annually"),
        "renewal_options":         _field(
            "One 5-year option, 9 months notice",
            "one (1) option to extend for five (5) years upon nine (9) months notice",
        ),
        "tenant_legal_entity_name": _field("Acme Corp LLC", "ACME CORP LLC, a Virginia LLC"),
    }
    raw.update(overrides)
    return raw


def _stub_extractor(raw):
    """An injectable extractor returning `raw` — the network boundary, stubbed."""
    return lambda pages: raw


# ══ 1. The open loop reads the newest entry ═══════════════════════════════════

def test_open_loop_comes_from_the_newest_entry_not_an_older_follow_up(db_session, client):
    """The live defect, exactly: Richard Tedrow / Collaborative AV.

    A 23 July entry carried "No response yet - consider another follow-up if
    silence continues." The newest entry, seven weeks later, said the lease was
    signed and the deal closed — and the header showed the July line anyway.
    A stale open loop is worse than an empty one.
    """
    contact = _contact(db_session, "Richard Tedrow", stage="In Play")
    _entry(db_session, contact_id=contact.id, log_date=date(2026, 7, 23),
           direction="outbound", action_taken="Sent a follow-up email",
           follow_up_action="No response yet - consider another follow-up if silence continues.")
    _entry(db_session, contact_id=contact.id, log_date=date(2026, 9, 12),
           direction="inbound", action_taken="Lease signed — deal closed")

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert "consider another follow-up" not in (header["open_loop"] or "")


def test_the_newest_entrys_own_follow_up_is_the_open_loop(db_session, client):
    """The newest entry still wins when it does carry a follow-up."""
    contact = _contact(db_session, "Nora Newest")
    _entry(db_session, contact_id=contact.id, log_date=date(2026, 7, 1),
           follow_up_action="An older, superseded follow-up")
    _entry(db_session, contact_id=contact.id, log_date=date(2026, 8, 1),
           follow_up_action="Send the Reston comps")

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["open_loop"] == "Send the Reston comps"


def test_a_stage_change_after_the_newest_entry_does_not_become_the_open_loop(
    db_session, client,
):
    """A divider has no direction and no follow-up, so it must not be 'newest'."""
    contact = _contact(db_session, "Ola Divider", stage="Sent")
    _entry(db_session, contact_id=contact.id, direction="inbound",
           action_taken="They wrote in")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": "Replied"})

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["open_loop"] == "They replied — owed a response"


def test_a_closed_contact_has_no_open_loop_at_all(db_session, client):
    """Placed means nothing is owed in either direction."""
    contact = _contact(db_session, "Percy Placed", stage="In Play")
    _entry(db_session, contact_id=contact.id, log_date=date(2026, 4, 1),
           follow_up_action="Chase the LOI")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": CLOSED_STAGE})

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["open_loop"] is None


# ══ 2. Closed stage bookkeeping ═══════════════════════════════════════════════

def test_closed_is_the_seventh_stage_and_is_accepted(db_session, client):
    assert CONTACT_STAGES == [
        "Sent", "Replied", "Interested", "In Play", "Not Interested", "Dormant",
        "Closed",
    ]
    contact = _contact(db_session, "Seven Stages")
    r = client.patch(f"/api/contacts/{contact.id}", json={"stage": "Closed"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "Closed"


def test_moving_to_closed_sets_closed_at_and_is_past_client(db_session, client):
    contact = _contact(db_session, "Cleo Closed", stage="In Play")
    body = client.patch(
        f"/api/contacts/{contact.id}", json={"stage": CLOSED_STAGE},
    ).json()

    assert body["stage"] == CLOSED_STAGE
    assert body["closed_at"] == date.today().isoformat()
    assert body["is_past_client"] is True


def test_moving_off_closed_clears_closed_at_but_keeps_past_client(db_session, client):
    """A renewal that falls through does not undo that Jack placed them once."""
    contact = _contact(db_session, "Reo Reopened", stage="In Play")
    client.patch(f"/api/contacts/{contact.id}", json={"stage": CLOSED_STAGE})

    body = client.patch(
        f"/api/contacts/{contact.id}", json={"stage": "In Play"},
    ).json()

    assert body["stage"] == "In Play"
    assert body["closed_at"] is None
    assert body["is_past_client"] is True


def test_nothing_auto_sets_closed(db_session, client):
    """Only Jack sets Closed. No write path here reaches it on its own."""
    contact = _contact(db_session, "Auto Nope", email="auto@nope-corp.com")
    # An inbound email, a logged entry, and a next-touch date: three engagement
    # paths that move state. None may land on Closed.
    client.post("/api/activity/from-email", json={
        "from_email": "auto@nope-corp.com",
        "action_taken": "They replied",
        "direction": "inbound",
        "source_message_id": "msg-auto-1",
    })
    client.patch(f"/api/contacts/{contact.id}", json={"next_touch_date": "2026-12-01"})

    db_session.refresh(contact)
    assert contact.stage != CLOSED_STAGE
    assert contact.is_past_client is False
    assert contact.closed_at is None


def test_an_inbound_email_never_reopens_a_closed_contact(db_session, client):
    """Sent -> Replied is the only inbound promotion; Closed is not regressed."""
    contact = _contact(db_session, "Quinn Closed", email="quinn@closed-corp.com",
                       stage=CLOSED_STAGE, is_past_client=True)
    client.post("/api/activity/from-email", json={
        "from_email": "quinn@closed-corp.com",
        "action_taken": "Happy new year",
        "direction": "inbound",
        "source_message_id": "msg-closed-1",
    })
    db_session.refresh(contact)
    assert contact.stage == CLOSED_STAGE


# ══ 3. Closed drops out of the list and the queue ═════════════════════════════

def test_closed_contacts_are_excluded_from_the_default_list(db_session, client):
    open_one = _contact(db_session, "Still Working", stage="In Play", triaged=True)
    closed = _contact(db_session, "Already Placed", stage=CLOSED_STAGE, triaged=True,
                      is_past_client=True, closed_at=date.today())

    ids = [r["id"] for r in client.get("/api/contacts/").json()]
    assert open_one.id in ids
    assert closed.id not in ids


def test_the_closed_filter_returns_closed_contacts(db_session, client):
    closed = _contact(db_session, "Findable Placed", stage=CLOSED_STAGE,
                      triaged=True, is_past_client=True)
    rows = client.get(f"/api/contacts/?stage={CLOSED_STAGE}").json()
    row = next(r for r in rows if r["id"] == closed.id)
    assert row["stage"] == CLOSED_STAGE
    assert row["is_past_client"] is True


def test_include_closed_returns_everything(db_session, client):
    open_one = _contact(db_session, "Open One", stage="Replied", triaged=True)
    closed = _contact(db_session, "Closed One", stage=CLOSED_STAGE, triaged=True)
    ids = [r["id"] for r in client.get("/api/contacts/?include_closed=true").json()]
    assert open_one.id in ids and closed.id in ids


def test_a_closed_contact_is_still_fully_searchable(db_session, client):
    """Out of the default list is not out of the system."""
    closed = _contact(db_session, "Searchable Sam", email="sam@placed-corp.com",
                      stage=CLOSED_STAGE, triaged=True)
    hits = client.get("/api/contacts/search", params={"q": "Searchable"}).json()
    assert closed.id in [c["id"] for c in hits]


def test_closed_contacts_are_out_of_the_active_outreach_queue(db_session, client):
    """The queue is the default list; a placed deal is not work waiting to be done."""
    company = _company(db_session, "Queue Co", "CO-Q1",
                       lease_expiry_date=date.today() + timedelta(days=400))
    closed = _contact(db_session, "Placed Pat", company_id=company.id,
                      stage=CLOSED_STAGE, triaged=True, is_past_client=True)
    rows = client.get("/api/contacts/?contact_type=tenant").json()
    assert closed.id not in [r["id"] for r in rows]


# ══ 4. Past-client re-entry ═══════════════════════════════════════════════════

def test_the_window_predicate_matches_the_scoring_tier_it_shares(db_session):
    """One definition of "the window", so the queue and the score cannot drift.

    6-9 months is the band sig_lease_expiry_proximity() scores at 100.0.
    """
    for months in (6, 7, 8, 9):
        assert is_in_peak_expiry_window(months) is True
        assert sig_lease_expiry_proximity(months) == 100.0
    for months in (0, 3, 5, 10, 18, 40):
        assert is_in_peak_expiry_window(months) is False
    # Null-safe: unknown months is never "in the window".
    assert is_in_peak_expiry_window(None) is False


def test_a_past_client_back_in_the_window_returns_to_the_queue_marked(db_session, client):
    """Stage stays Closed and the row says past client — never reset to Sent."""
    company = _company(db_session, "Return Co", "CO-RET",
                       lease_expiry_date=_in_window_date(),
                       current_submarket="Tysons")
    contact = _contact(db_session, "Rita Return", company_id=company.id,
                       stage=CLOSED_STAGE, triaged=True, is_past_client=True,
                       closed_at=date.today() - timedelta(days=900))

    rows = client.get("/api/contacts/").json()
    row = next((r for r in rows if r["id"] == contact.id), None)
    assert row is not None, "a past client back in the window belongs in the queue"
    assert row["stage"] == CLOSED_STAGE
    assert row["is_past_client"] is True
    assert row["past_client_reentry"] is True
    assert 6 <= row["lease_expiry_months"] <= 9


def test_a_past_client_outside_the_window_stays_out_of_the_queue(db_session, client):
    company = _company(db_session, "Far Co", "CO-FAR",
                       lease_expiry_date=date.today() + timedelta(days=900))
    contact = _contact(db_session, "Fay Far", company_id=company.id,
                       stage=CLOSED_STAGE, triaged=True, is_past_client=True)
    ids = [r["id"] for r in client.get("/api/contacts/").json()]
    assert contact.id not in ids


def test_the_thread_header_carries_the_past_client_marker(db_session, client):
    company = _company(db_session, "Header Co", "CO-HDR",
                       lease_expiry_date=_in_window_date())
    contact = _contact(db_session, "Hal Header", company_id=company.id,
                       stage=CLOSED_STAGE, is_past_client=True)

    header = client.get(f"/api/contacts/{contact.id}").json()
    assert header["past_client_reentry"] is True
    assert header["contact"]["stage"] == CLOSED_STAGE
    assert header["contact"]["is_past_client"] is True


def test_the_briefing_surfaces_past_client_reentries_with_stage_closed(db_session, client):
    company = _company(db_session, "Briefing Co", "CO-BRF",
                       lease_expiry_date=_in_window_date(),
                       current_submarket="Reston", current_sf_occupied=9000)
    contact = _contact(db_session, "Bree Briefing", company_id=company.id,
                       stage=CLOSED_STAGE, is_past_client=True,
                       closed_at=date.today() - timedelta(days=1000))

    body = client.get("/api/dashboard/briefing").json()
    row = next(
        r for r in body["past_client_reentries"] if r["contact_id"] == contact.id
    )
    assert row["contact_stage"] == CLOSED_STAGE
    assert row["company_name"] == "Briefing Co"
    assert 6 <= row["lease_expiry_months"] <= 9


def test_a_contact_who_was_never_placed_is_not_a_past_client_reentry(db_session, client):
    """Being in the window is not enough — the marker means Jack placed them."""
    company = _company(db_session, "Never Co", "CO-NVR",
                       lease_expiry_date=_in_window_date())
    contact = _contact(db_session, "Nate Never", company_id=company.id,
                       stage="Interested", triaged=True)

    row = next(r for r in client.get("/api/contacts/").json() if r["id"] == contact.id)
    assert row["past_client_reentry"] is False
    body = client.get("/api/dashboard/briefing").json()
    assert contact.id not in [r["contact_id"] for r in body["past_client_reentries"]]


# ══ 5-6. Storage: a bare filename, and a folder that can move ════════════════

def test_only_a_bare_filename_is_stored_never_a_path(db_session, client, leases_dir):
    """No absolute, machine-specific or user-specific path in a data column."""
    company = _company(db_session, "Storage Co", "CO-STO")
    r = client.post(
        f"/api/leases/companies/{company.id}/upload",
        files={"file": ("Acme Lease.pdf", b"%PDF-1.4 not a real pdf", "application/pdf")},
    )
    assert r.status_code == 200, r.text

    stored = _current(db_session, company).file_name
    assert stored == "Acme Lease.pdf"
    assert lease_storage.is_bare_file_name(stored)
    assert not os.path.isabs(stored)
    for token in (os.sep, "/", ":", ".."):
        assert token not in stored
    # And the value really is just a name — the folder is nowhere in it.
    assert str(leases_dir) not in stored


def test_an_uploaded_name_with_a_directory_component_is_reduced_to_a_name(
    db_session, client, leases_dir,
):
    company = _company(db_session, "Traversal Co", "CO-TRV")
    client.post(
        f"/api/leases/companies/{company.id}/upload",
        files={"file": (r"..\..\Windows\System32\evil.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert _current(db_session, company).file_name == "evil.pdf"
    assert (leases_dir / "evil.pdf").is_file()


def test_a_second_upload_of_the_same_name_never_overwrites_the_first(
    db_session, client, leases_dir,
):
    """Two tenants' leases can arrive under the same name out of one mail client."""
    a = _company(db_session, "First Co", "CO-1ST")
    b = _company(db_session, "Second Co", "CO-2ND")
    client.post(f"/api/leases/companies/{a.id}/upload",
                files={"file": ("Lease.pdf", b"%PDF-first", "application/pdf")})
    client.post(f"/api/leases/companies/{b.id}/upload",
                files={"file": ("Lease.pdf", b"%PDF-second", "application/pdf")})

    assert _current(db_session, a).file_name == "Lease.pdf"
    assert _current(db_session, b).file_name == "Lease (2).pdf"
    assert (leases_dir / "Lease.pdf").read_bytes() == b"%PDF-first"
    assert (leases_dir / "Lease (2).pdf").read_bytes() == b"%PDF-second"


def test_moving_the_leases_folder_repoints_existing_rows_with_no_data_change(
    db_session, client, tmp_path, monkeypatch,
):
    """The whole reason the column holds a filename: a move is one setting."""
    old_folder = tmp_path / "OldLeases"
    old_folder.mkdir()
    monkeypatch.setattr(settings, "LEASES_FOLDER", str(old_folder))

    company = _company(db_session, "Mover Co", "CO-MOV")
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("Mover.pdf", b"%PDF-mover", "application/pdf")})
    stored_before = _current(db_session, company).file_name
    assert lease_storage.resolve_lease_path(stored_before).startswith(str(old_folder))

    # Jack moves the folder and changes the ONE setting.
    new_folder = tmp_path / "NewLeases"
    new_folder.mkdir()
    (new_folder / "Mover.pdf").write_bytes((old_folder / "Mover.pdf").read_bytes())
    monkeypatch.setattr(settings, "LEASES_FOLDER", str(new_folder))

    lease = _current(db_session, company)
    # Not one byte of stored data changed...
    assert lease.file_name == stored_before
    # ...and the same row now resolves into the new folder.
    resolved = lease_storage.resolve_lease_path(lease.file_name)
    assert resolved.startswith(str(new_folder))
    assert lease_storage.lease_file_exists(lease.file_name)
    # And the file link serves it from there.
    assert client.get(f"/api/leases/companies/{company.id}/file").status_code == 200


def test_no_column_on_either_table_holds_an_absolute_path(db_session, client, leases_dir):
    """A blanket check: nothing this build writes leaks a filesystem path."""
    company = _company(db_session, "Audit Co", "CO-AUD")
    contact = _contact(db_session, "Ada Audit", company_id=company.id)
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("Audit.pdf", b"%PDF-audit", "application/pdf")})

    db_session.refresh(company)
    lease = _current(db_session, company)
    inspector = inspect(db_session.get_bind())
    for table, row in (("companies", company), ("contacts", contact), ("leases", lease)):
        for col in inspector.get_columns(table):
            value = getattr(row, col["name"], None)
            if isinstance(value, str) and value:
                assert str(leases_dir) not in value, f"{table}.{col['name']}"
                assert not os.path.isabs(value), f"{table}.{col['name']} = {value!r}"


def test_a_missing_file_says_so_plainly_rather_than_failing_silently(
    db_session, client, leases_dir,
):
    company = _company(db_session, "Gone Co", "CO-GON")
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("Gone.pdf", b"%PDF-gone", "application/pdf")})
    os.remove(leases_dir / _current(db_session, company).file_name)

    r = client.get(f"/api/leases/companies/{company.id}/file")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "Gone.pdf" in detail and "leases folder" in detail

    # And the card knows before Jack clicks.
    status = client.get(f"/api/leases/companies/{company.id}").json()
    assert status["file_missing"] is True
    assert status["lease_file_name"] == "Gone.pdf"


# ══ 7-8. Extraction: confirmation, and no unsupported value ══════════════════

def test_extraction_returns_every_field_with_its_source_clause(db_session):
    parsed = extract_lease(["page one"], extractor=_stub_extractor(_full_extraction()))
    assert set(parsed) == set(LEASE_FIELDS)
    for field, entry in parsed.items():
        assert entry["found"] is True, field
        assert entry["source_text"], field


def test_a_value_with_no_supporting_clause_text_is_not_extracted(db_session):
    """Rule: a value we cannot quote the document for is not a value."""
    raw = _full_extraction(
        rentable_square_footage={"value": "40,000", "source_text": None, "page": None},
    )
    parsed = extract_lease(["page one"], extractor=_stub_extractor(raw))
    assert parsed["rentable_square_footage"]["found"] is False
    assert parsed["rentable_square_footage"]["value"] is None


def test_an_unsupported_value_can_never_be_written_even_if_accepted(
    db_session, client, leases_dir, monkeypatch,
):
    """Asking for it explicitly does not get it written either."""
    raw = _full_extraction(
        rentable_square_footage={"value": "40,000", "source_text": None, "page": None},
    )
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(["p"], _stub_extractor(raw)),
    )
    company = _company(db_session, "Unsupported Co", "CO-UNS", current_sf_occupied=5000)
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("U.pdf", b"%PDF", "application/pdf")})

    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS)},
    )
    assert r.status_code == 200, r.text
    assert "rentable_square_footage" in r.json()["skipped"]
    db_session.refresh(company)
    assert company.current_sf_occupied == 5000       # untouched
    assert company.current_sf_occupied_source is None


def test_nothing_is_written_to_the_company_until_confirmation(
    db_session, client, leases_dir, monkeypatch,
):
    """Upload reads the lease; it does not move the record."""
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Patient Co", "CO-PAT",
                       lease_expiry_date=date(2030, 1, 1),
                       current_address="Old address", current_sf_occupied=1234)

    body = client.post(
        f"/api/leases/companies/{company.id}/upload",
        files={"file": ("P.pdf", b"%PDF", "application/pdf")},
    ).json()
    assert body["has_extraction"] is True

    db_session.refresh(company)
    assert company.lease_expiry_date == date(2030, 1, 1)
    assert company.current_address == "Old address"
    assert company.current_sf_occupied == 1234


def test_everything_found_defaults_to_accepted_in_the_review_panel(
    db_session, client, leases_dir, monkeypatch,
):
    """Jack scans and unchecks; he does not approve nine values one at a time."""
    raw = _full_extraction(
        suite_or_unit={"value": None, "source_text": None, "page": None},
    )
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(["p"], _stub_extractor(raw)),
    )
    company = _company(db_session, "Default Co", "CO-DEF")
    body = client.post(f"/api/leases/companies/{company.id}/upload",
                       files={"file": ("D.pdf", b"%PDF", "application/pdf")}).json()

    by_field = {f["field"]: f for f in body["fields"]}
    assert by_field["lease_expiration_date"]["accepted"] is True
    assert by_field["premises_address"]["accepted"] is True
    # A not-found field is never pre-accepted.
    assert by_field["suite_or_unit"]["found"] is False
    assert by_field["suite_or_unit"]["accepted"] is False
    # The three that write to the record are labelled as such.
    for field in COMPANY_WRITEBACK_FIELDS:
        assert by_field[field]["writes_to_company"] is True


def test_confirming_writes_expiry_address_and_sf_marked_lease_sourced(
    db_session, client, leases_dir, monkeypatch,
):
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Confirm Co", "CO-CNF",
                       current_address="CoStar address", current_sf_occupied=1,
                       lease_expiry_source="costar")
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("C.pdf", b"%PDF", "application/pdf")})

    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS)},
    )
    assert r.status_code == 200, r.text

    db_session.refresh(company)
    assert company.lease_expiry_date == date(2027, 6, 30)
    assert company.current_address == "1750 Tysons Blvd, McLean, VA"
    assert company.current_sf_occupied == 12500
    # A lease outranks CoStar, and the record says which each value is.
    assert company.lease_expiry_source == "lease_document"
    assert company.current_address_source == "lease_document"
    assert company.current_sf_occupied_source == "lease_document"
    # Months kept in step with the date, so the queue reads the new expiry.
    assert company.lease_expiry_months is not None


def test_an_unchecked_value_is_not_written(db_session, client, leases_dir, monkeypatch):
    """The uncheck is the whole point of the panel."""
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Uncheck Co", "CO-UNC",
                       current_address="Keep this address", current_sf_occupied=777)
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("U2.pdf", b"%PDF", "application/pdf")})

    # Jack unchecks the address and the SF; he keeps the expiry.
    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": ["lease_expiration_date"]},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert "lease_expiration_date" in result["written"]
    assert "premises_address" in result["skipped"]
    assert "rentable_square_footage" in result["skipped"]

    db_session.refresh(company)
    assert company.lease_expiry_date == date(2027, 6, 30)
    assert company.current_address == "Keep this address"
    assert company.current_sf_occupied == 777
    assert company.current_address_source is None


def test_the_full_extraction_is_stored_so_every_field_traces_to_its_clause(
    db_session, client, leases_dir, monkeypatch,
):
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Trace Co", "CO-TRC")
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("T.pdf", b"%PDF", "application/pdf")})
    client.post(f"/api/leases/companies/{company.id}/confirm",
                json={"accepted_fields": ["lease_expiration_date"]})

    stored = json.loads(_current(db_session, company).extraction_json)
    # Every field, including the ones Jack unchecked, with its clause.
    assert set(stored) == set(LEASE_FIELDS)
    assert "expires June 30, 2027" in stored["lease_expiration_date"]["source_text"]
    assert stored["lease_expiration_date"]["accepted"] is True
    assert stored["premises_address"]["accepted"] is False
    assert stored["premises_address"]["source_text"]      # the clause survives


def test_the_expiration_date_is_not_the_commencement_date(db_session):
    """The reading errors that would move a re-entry date by years.

    Extraction keeps commencement and expiration as separate fields, each with
    its own clause, and only the expiration writes to the record.
    """
    parsed = extract_lease(["p"], extractor=_stub_extractor(_full_extraction()))
    assert parsed["lease_commencement_date"]["value"] == "March 1, 2022"
    assert parsed["lease_expiration_date"]["value"] == "June 30, 2027"
    assert "lease_commencement_date" not in COMPANY_WRITEBACK_FIELDS
    # A renewal option is described, never folded into the expiry.
    assert "nine (9) months notice" in parsed["renewal_options"]["source_text"]


# ══ 9-10. Never lose the document because the reading failed ══════════════════

def test_extraction_failure_still_stores_and_links_the_file(
    db_session, client, leases_dir, monkeypatch,
):
    def _boom(pdf_bytes, extractor=None):
        raise ExtractionUnavailable("This PDF has no readable text (it may be a scan).")

    monkeypatch.setattr(leases_route, "extract_lease_from_pdf", _boom)
    company = _company(db_session, "Scan Co", "CO-SCN")

    r = client.post(f"/api/leases/companies/{company.id}/upload",
                    files={"file": ("Scan.pdf", b"%PDF-scan", "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lease_file_name"] == "Scan.pdf"
    assert body["has_extraction"] is False
    assert "no readable text" in body["extraction_error"]

    assert _current(db_session, company).file_name == "Scan.pdf"
    assert (leases_dir / "Scan.pdf").read_bytes() == b"%PDF-scan"
    assert client.get(f"/api/leases/companies/{company.id}/file").status_code == 200


def test_an_unexpected_reader_error_still_stores_and_links_the_file(
    db_session, client, leases_dir, monkeypatch,
):
    """Not just the expected failure type — any read error keeps the document."""
    def _explode(pdf_bytes, extractor=None):
        raise RuntimeError("something deep in the parser")

    monkeypatch.setattr(leases_route, "extract_lease_from_pdf", _explode)
    company = _company(db_session, "Boom Co", "CO-BOM")

    r = client.post(f"/api/leases/companies/{company.id}/upload",
                    files={"file": ("Boom.pdf", b"%PDF-boom", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["lease_file_name"] == "Boom.pdf"
    assert "could not be read" in r.json()["extraction_error"]
    assert _current(db_session, company).file_name == "Boom.pdf"


def test_a_missing_api_key_still_stores_and_links_the_file(
    db_session, client, leases_dir, monkeypatch,
):
    """No key is a configuration gap, not a lost document — and it says so."""
    def _no_key(pdf_bytes, extractor=None):
        raise MissingAPIKeyError(
            "ANTHROPIC_API_KEY is not set, so the lease could not be read. "
            "The file is stored and linked."
        )

    monkeypatch.setattr(leases_route, "extract_lease_from_pdf", _no_key)
    company = _company(db_session, "NoKey Co", "CO-NOK")

    r = client.post(f"/api/leases/companies/{company.id}/upload",
                    files={"file": ("NoKey.pdf", b"%PDF-nokey", "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lease_file_name"] == "NoKey.pdf"
    assert body["extraction_skipped"] is True
    assert "ANTHROPIC_API_KEY" in body["extraction_error"]
    assert body["has_extraction"] is False

    assert _current(db_session, company).file_name == "NoKey.pdf"
    assert (leases_dir / "NoKey.pdf").is_file()
    assert client.get(f"/api/leases/companies/{company.id}/file").status_code == 200


def test_the_real_extractor_raises_a_clear_error_with_no_key(monkeypatch):
    """The service's own boundary, with the env var and the setting both empty."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with pytest.raises(MissingAPIKeyError):
        lease_svc._extract_via_llm(["some lease text"])


def test_an_empty_upload_is_refused_before_anything_is_stored(
    db_session, client, leases_dir,
):
    company = _company(db_session, "Empty Co", "CO-EMP")
    r = client.post(f"/api/leases/companies/{company.id}/upload",
                    files={"file": ("Empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400
    assert _lease_count(db_session, company) == 0
    assert list(leases_dir.iterdir()) == []


# ══ 11. The loop closes, end to end ══════════════════════════════════════════

def test_a_confirmed_lease_expiry_brings_a_past_client_back_into_the_queue(
    db_session, client, leases_dir, monkeypatch,
):
    """The whole loop, in one test.

    Jack places a tenant (Closed: out of the queue), links their new lease, the
    extraction reads an expiry that lands in the 6-9 month window, he confirms —
    and the contact reappears in the queue and the briefing as a past client,
    stage still Closed, with their thread intact.
    """
    # An expiry inside the window, quoted the way a lease would state it.
    in_window = _in_window_date()
    raw = _full_extraction(
        lease_expiration_date=_field(
            in_window.isoformat(),
            f"the Term shall expire on {in_window.isoformat()}",
        ),
    )
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(["p"], _stub_extractor(raw)),
    )

    company = _company(db_session, "Loop Co", "CO-LOOP",
                       lease_expiry_date=date.today() + timedelta(days=1500),
                       current_submarket="Tysons")
    contact = _contact(db_session, "Lou Loop", company_id=company.id,
                       stage="In Play", triaged=True)
    _entry(db_session, contact_id=contact.id, company_stamp_id=company.id,
           action_taken="Toured 1750 Tysons Blvd with them")

    # 1. Jack places them.
    client.patch(f"/api/contacts/{contact.id}", json={"stage": CLOSED_STAGE})
    assert contact.id not in [r["id"] for r in client.get("/api/contacts/").json()]

    # 2. The lease goes on the record and is read.
    client.post(f"/api/leases/companies/{company.id}/upload",
                files={"file": ("Loop.pdf", b"%PDF-loop", "application/pdf")})
    # 3. He confirms it.
    client.post(f"/api/leases/companies/{company.id}/confirm",
                json={"accepted_fields": list(LEASE_FIELDS)})

    db_session.refresh(company)
    assert company.lease_expiry_date == in_window
    assert company.lease_expiry_source == "lease_document"

    # 4. They are back in the queue, marked, and not reset.
    row = next(r for r in client.get("/api/contacts/").json() if r["id"] == contact.id)
    assert row["stage"] == CLOSED_STAGE
    assert row["past_client_reentry"] is True
    assert 6 <= row["lease_expiry_months"] <= 9

    # 5. And in the briefing.
    briefing = client.get("/api/dashboard/briefing").json()
    entry = next(
        r for r in briefing["past_client_reentries"] if r["contact_id"] == contact.id
    )
    assert entry["contact_stage"] == CLOSED_STAGE
    assert entry["lease_sourced_expiry"] is True

    # 6. Their full thread is one click away, unchanged.
    page = client.get(f"/api/contacts/{contact.id}/timeline").json()
    assert "Toured 1750 Tysons Blvd with them" in [
        e["action_taken"] for e in page["entries"]
    ]


def test_a_costar_import_never_overwrites_a_lease_sourced_value(db_session):
    """A lease outranks CoStar, enforced where the import actually writes."""
    from app.api.routes.companies import LEASE_DOCUMENT_SOURCE, PROTECTED_LEASE_SOURCES
    assert LEASE_DOCUMENT_SOURCE in PROTECTED_LEASE_SOURCES


# ══ 12. Removing a lease ═════════════════════════════════════════════

def _upload(client, company, name="Draft.pdf", body=b"%PDF-draft"):
    return client.post(
        f"/api/leases/companies/{company.id}/upload",
        files={"file": (name, body, "application/pdf")},
    )


def test_removing_a_lease_clears_all_three_fields_and_deletes_the_file(
    db_session, client, leases_dir,
):
    """The way out of the mistake: Jack uploaded a draft and could not clear it."""
    company = _company(db_session, "Remove Co", "CO-RMV")
    _upload(client, company)
    assert _current(db_session, company).file_name == "Draft.pdf"
    assert (leases_dir / "Draft.pdf").is_file()

    r = client.delete(f"/api/companies/{company.company_id}/lease")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_file_name"] == "Draft.pdf"
    assert body["file_outcome"] == "deleted"
    assert body["warning"] is None

    # The lease record itself is gone — file name, upload time and extraction
    # with it.
    assert _current(db_session, company) is None
    assert _lease_count(db_session, company) == 0
    # And the file really is gone from the folder.
    assert not (leases_dir / "Draft.pdf").exists()


def test_removing_a_lease_clears_a_stored_extraction_too(
    db_session, client, leases_dir, monkeypatch,
):
    """The abstract goes with the document — it is that document's reading."""
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Extract Co", "CO-RMX")
    _upload(client, company)
    assert _current(db_session, company).extraction_json is not None

    client.delete(f"/api/companies/{company.company_id}/lease")
    assert _lease_count(db_session, company) == 0
    # The card reads as having no lease at all again.
    status = client.get(f"/api/leases/companies/{company.id}").json()
    assert status["lease_file_name"] is None
    assert status["has_extraction"] is False
    assert status["fields"] == []


def test_removing_works_when_the_file_is_already_gone_from_disk(
    db_session, client, leases_dir,
):
    """A file deleted out from under the app must not strand the link.

    This is the state Jack would land in if he tidied the folder by hand: the
    fields still clear, and the result says the file was already absent rather
    than claiming a deletion that did not happen.
    """
    company = _company(db_session, "Ghost Co", "CO-GHO")
    _upload(client, company, name="Ghost.pdf")
    db_session.refresh(company)
    os.remove(leases_dir / "Ghost.pdf")

    r = client.delete(f"/api/companies/{company.company_id}/lease")
    assert r.status_code == 200, r.text
    assert r.json()["file_outcome"] == "absent"
    assert r.json()["warning"] is None

    assert _current(db_session, company) is None
    assert _lease_count(db_session, company) == 0


def test_removing_leaves_confirmed_company_values_and_their_markers_intact(
    db_session, client, leases_dir, monkeypatch,
):
    """Removing the document does not un-know what Jack already confirmed.

    He read those three values against their clauses and accepted them. Silently
    reverting a confirmed expiry would move a tenant's place in the queue behind
    his back — and dropping the lease-sourced markers would hand the next CoStar
    import permission to overwrite them.
    """
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(
            ["p"], _stub_extractor(_full_extraction()),
        ),
    )
    company = _company(db_session, "Keep Co", "CO-KEP")
    _upload(client, company)
    client.post(f"/api/leases/companies/{company.id}/confirm",
                json={"accepted_fields": list(LEASE_FIELDS)})

    db_session.refresh(company)
    expiry = company.lease_expiry_date
    months = company.lease_expiry_months
    assert expiry == date(2027, 6, 30)
    assert company.current_address == "1750 Tysons Blvd, McLean, VA"
    assert company.current_sf_occupied == 12500

    client.delete(f"/api/companies/{company.company_id}/lease")

    db_session.refresh(company)
    # The values survive...
    assert company.lease_expiry_date == expiry
    assert company.lease_expiry_months == months
    assert company.current_address == "1750 Tysons Blvd, McLean, VA"
    assert company.current_sf_occupied == 12500
    # ...and so do the markers that keep the CoStar import off them.
    assert company.lease_expiry_source == "lease_document"
    assert company.current_address_source == "lease_document"
    assert company.current_sf_occupied_source == "lease_document"
    # Only the document itself is gone.
    assert _current(db_session, company) is None


def test_removing_a_lease_from_a_company_that_has_none_is_a_clean_404(
    db_session, client, leases_dir,
):
    company = _company(db_session, "Bare Co", "CO-BAR")
    r = client.delete(f"/api/companies/{company.company_id}/lease")
    assert r.status_code == 404
    assert "No lease document is linked" in r.json()["detail"]


def test_removing_a_lease_from_a_missing_company_is_a_clean_404(db_session, client):
    r = client.delete("/api/companies/CO-NOPE/lease")
    assert r.status_code == 404
    assert r.json()["detail"] == "Company not found"


def test_removing_one_companys_lease_leaves_another_companys_file_alone(
    db_session, client, leases_dir,
):
    """Collision-safe naming means two rows point at two different files."""
    a = _company(db_session, "Keeper Co", "CO-KPR")
    b = _company(db_session, "Goner Co", "CO-GNR")
    _upload(client, a, name="Lease.pdf", body=b"%PDF-keeper")
    _upload(client, b, name="Lease.pdf", body=b"%PDF-goner")
    assert _current(db_session, a).file_name == "Lease.pdf"
    assert _current(db_session, b).file_name == "Lease (2).pdf"

    client.delete(f"/api/companies/{b.company_id}/lease")

    assert _current(db_session, a).file_name == "Lease.pdf"
    assert (leases_dir / "Lease.pdf").read_bytes() == b"%PDF-keeper"
    assert not (leases_dir / "Lease (2).pdf").exists()


def test_a_file_that_cannot_be_deleted_still_clears_the_link_and_warns(
    db_session, client, leases_dir, monkeypatch,
):
    """A PDF open in a viewer locks the file on Windows.

    Jack must not be stuck with a document he cannot clear, so the link goes
    either way and the result says what happened to the file.
    """
    company = _company(db_session, "Locked Co", "CO-LCK")
    _upload(client, company, name="Locked.pdf")

    def _locked(path):
        raise PermissionError("The process cannot access the file")

    monkeypatch.setattr(os, "remove", _locked)
    r = client.delete(f"/api/companies/{company.company_id}/lease")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_outcome"].startswith("error:")
    assert "Locked.pdf" in body["warning"]
    assert "by hand" in body["warning"]

    assert _current(db_session, company) is None
    assert _lease_count(db_session, company) == 0


def test_delete_lease_file_never_reaches_outside_the_leases_folder(
    db_session, tmp_path, leases_dir,
):
    """The stored value is the only thing that decides what gets deleted.

    resolve_lease_path() refuses anything carrying a path component, so a
    column somehow holding one is refused rather than followed.
    """
    outsider = tmp_path / "not-a-lease.pdf"
    outsider.write_bytes(b"%PDF-outside")

    assert lease_storage.delete_lease_file(str(outsider)) == "refused"
    assert lease_storage.delete_lease_file(r"..\..\not-a-lease.pdf") == "refused"
    assert outsider.exists()
    # And an empty value is simply nothing to do.
    assert lease_storage.delete_lease_file(None) == "absent"
    assert lease_storage.delete_lease_file("") == "absent"


def test_a_removed_lease_can_be_replaced_by_a_new_upload(
    db_session, client, leases_dir,
):
    """The end of the actual story: clear the draft, upload the signed one."""
    company = _company(db_session, "Redo Co", "CO-RDO")
    _upload(client, company, name="Draft.pdf", body=b"%PDF-draft")
    client.delete(f"/api/companies/{company.company_id}/lease")

    r = _upload(client, company, name="Signed.pdf", body=b"%PDF-signed")
    assert r.status_code == 200, r.text
    assert _current(db_session, company).file_name == "Signed.pdf"
    assert (leases_dir / "Signed.pdf").read_bytes() == b"%PDF-signed"
    assert client.get(f"/api/leases/companies/{company.id}/file").status_code == 200


# ══ The contract that must not break ══════════════════════════════════════════

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
    assert row["current_headcount"] == 42            # headcount
    assert row["headcount_growth_pct"] == 12.5       # growth_rate
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Tysons"      # submarket
    assert row["opportunity_score"] == 77.0          # score


def test_re_engage_today_still_surfaces_a_contact_sourced_due_date(db_session, client):
    """Untouched by the Closed mechanic: an explicit date Jack set still calls."""
    contact = _contact(db_session, "Due Today", stage="Interested",
                       next_touch_date=date.today())
    rows = client.get("/api/activity/re-engage").json()
    assert contact.id in [r["contact_id"] for r in rows]


def test_lease_content_never_reaches_generated_tenant_copy(db_session, client, leases_dir):
    """Lease content is private this build — it is not an outreach input.

    Structural: the tenant-side generator does not read any of the three lease
    columns, so a clause cannot reach copy by accident.
    """
    import inspect as _inspect
    from app.services import outreach_service

    source = _inspect.getsource(outreach_service)
    for column in ("lease_file_name", "lease_extraction_json", "lease_uploaded_at"):
        assert column not in source, f"{column} reached the outreach generator"
    # Nor any part of the leases table that replaced those columns.
    for name in ("extraction_json", "app.models.lease", "lease_records",
                 "escalation_terms", "renewal_options", "tenant_legal_entity"):
        assert name not in source, f"{name} reached the outreach generator"


# ══ 13. Editable values in the review panel ═══════════════════════════════════
#
# Collaborative AV, exactly: the lease correctly did not state rentable SF, and
# without an input the company kept a stale CoStar figure. Every row is now
# editable. A typed value is marked "manual" so Jack can always tell what came
# off the page from what he supplied.

def _stub_route(monkeypatch, raw):
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(["p"], _stub_extractor(raw)),
    )


_NOT_FOUND = {"value": None, "source_text": None, "page": None}


def test_a_typed_value_in_a_not_found_row_is_stored_as_manual_not_lease_document(
    db_session, client, leases_dir, monkeypatch,
):
    _stub_route(monkeypatch, _full_extraction(rentable_square_footage=_NOT_FOUND))
    company = _company(db_session, "Collaborative AV", "CO-CAV",
                       current_sf_occupied=4000)
    body = _upload(client, company, name="CAV.pdf").json()
    sf_row = next(f for f in body["fields"] if f["field"] == "rentable_square_footage")
    assert sf_row["found"] is False          # the page really did not state it

    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={
            "accepted_fields": list(LEASE_FIELDS),
            "manual_values": {"rentable_square_footage": "9,850"},
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["written"]["rentable_square_footage"] == "9850"
    assert result["sources"]["rentable_square_footage"] == "manual"
    assert result["sources"]["lease_expiration_date"] == "lease_document"

    db_session.refresh(company)
    assert company.current_sf_occupied == 9850
    assert company.current_sf_occupied_source == "manual"
    assert company.current_sf_occupied_source != "lease_document"
    # Values that did come off the page keep saying so.
    assert company.current_address_source == "lease_document"
    assert company.lease_expiry_source == "lease_document"

    lease = _current(db_session, company)
    assert lease.rentable_sf == 9850
    stored = json.loads(lease.extraction_json)["rentable_square_footage"]
    assert stored["source"] == "manual"
    assert stored["manual_value"] == "9,850"
    assert stored["found"] is False and stored["value"] is None

    # And the panel reopens showing which is which.
    row = next(
        f for f in client.get(f"/api/leases/companies/{company.id}").json()["fields"]
        if f["field"] == "rentable_square_footage"
    )
    assert row["source"] == "manual"
    assert row["manual_value"] == "9,850"


def test_editing_an_extracted_row_keeps_the_original_value_and_clause(
    db_session, client, leases_dir, monkeypatch,
):
    """Replaced on the record, never lost from the audit trail."""
    _stub_route(monkeypatch, _full_extraction())
    company = _company(db_session, "Edit Co", "CO-EDT")
    _upload(client, company, name="Edit.pdf")

    typed = "1760 Tysons Blvd, McLean, VA 22102"
    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS),
              "manual_values": {"premises_address": typed}},
    )
    assert r.status_code == 200, r.text

    db_session.refresh(company)
    assert company.current_address == typed
    assert company.current_address_source == "manual"

    lease = _current(db_session, company)
    assert lease.premises_address == typed
    entry = json.loads(lease.extraction_json)["premises_address"]
    # The page's reading and its clause, untouched...
    assert entry["value"] == "1750 Tysons Blvd, McLean, VA"
    assert entry["source_text"] == "the Premises at 1750 Tysons Blvd, McLean, VA"
    assert entry["found"] is True
    # ...beside what Jack typed.
    assert entry["manual_value"] == typed
    assert entry["source"] == "manual"


def test_an_edited_but_unchecked_row_does_not_write(
    db_session, client, leases_dir, monkeypatch,
):
    _stub_route(monkeypatch, _full_extraction(rentable_square_footage=_NOT_FOUND))
    company = _company(db_session, "Unchecked Edit Co", "CO-UEC",
                       current_sf_occupied=4000)
    _upload(client, company, name="UE.pdf")

    accepted = [f for f in LEASE_FIELDS if f != "rentable_square_footage"]
    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": accepted,
              "manual_values": {"rentable_square_footage": "9,850"}},
    )
    assert r.status_code == 200, r.text
    assert "rentable_square_footage" not in r.json()["written"]
    assert "rentable_square_footage" in r.json()["skipped"]

    db_session.refresh(company)
    assert company.current_sf_occupied == 4000
    assert company.current_sf_occupied_source is None
    lease = _current(db_session, company)
    assert lease.rentable_sf is None
    entry = json.loads(lease.extraction_json)["rentable_square_footage"]
    assert entry["accepted"] is False
    assert "manual_value" not in entry
    assert "source" not in entry


def test_an_edited_extracted_row_that_is_unchecked_does_not_write_either(
    db_session, client, leases_dir, monkeypatch,
):
    _stub_route(monkeypatch, _full_extraction())
    company = _company(db_session, "Unchecked Found Co", "CO-UFC",
                       current_address="Keep me")
    _upload(client, company, name="UF.pdf")
    accepted = [f for f in LEASE_FIELDS if f != "premises_address"]
    client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": accepted,
              "manual_values": {"premises_address": "Somewhere else, Reston, VA"}},
    )
    db_session.refresh(company)
    assert company.current_address == "Keep me"
    assert company.current_address_source is None
    entry = json.loads(_current(db_session, company).extraction_json)["premises_address"]
    assert entry["value"] == "1750 Tysons Blvd, McLean, VA"   # original survives
    assert "manual_value" not in entry


def test_retyping_exactly_what_the_page_says_is_not_an_edit(
    db_session, client, leases_dir, monkeypatch,
):
    _stub_route(monkeypatch, _full_extraction())
    company = _company(db_session, "Same Co", "CO-SAM")
    _upload(client, company, name="Same.pdf")
    client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS),
              "manual_values": {"premises_address": " 1750 Tysons Blvd, McLean, VA "}},
    )
    db_session.refresh(company)
    assert company.current_address_source == "lease_document"


def test_a_typed_value_with_no_clause_is_still_never_invented(
    db_session, client, leases_dir, monkeypatch,
):
    """Accepting a not-found row with nothing typed writes nothing, as before."""
    _stub_route(monkeypatch, _full_extraction(rentable_square_footage=_NOT_FOUND))
    company = _company(db_session, "Blank Co", "CO-BLK", current_sf_occupied=4000)
    _upload(client, company, name="Blank.pdf")
    r = client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS),
              "manual_values": {"rentable_square_footage": "   "}},
    )
    assert "rentable_square_footage" in r.json()["skipped"]
    db_session.refresh(company)
    assert company.current_sf_occupied == 4000


def _costar_csv(rows):
    import io as _io
    import pandas as pd
    from app.api.routes.companies import COSTAR_TENANT_COLS

    df = pd.DataFrame(rows, columns=COSTAR_TENANT_COLS)
    buf = _io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def test_a_manual_value_survives_a_costar_import_that_would_overwrite_it(
    db_session, client, leases_dir, monkeypatch,
):
    """Enforced where the import actually writes, not just in a constant."""
    from app.api.routes.companies import COSTAR_TENANT_COLS, PROTECTED_LEASE_SOURCES
    assert "manual" in PROTECTED_LEASE_SOURCES

    address = "11911 Freedom Dr, Reston, VA 20190"
    _stub_route(monkeypatch, _full_extraction(
        premises_address=_field(address, f"the Premises at {address}"),
        rentable_square_footage=_NOT_FOUND,
    ))
    company = _company(db_session, "Manual Guard Co", "CO-MGC",
                       current_address=address, current_sf_occupied=4000)
    _upload(client, company, name="MG.pdf")
    client.post(
        f"/api/leases/companies/{company.id}/confirm",
        json={"accepted_fields": list(LEASE_FIELDS),
              "manual_values": {
                  "rentable_square_footage": "9850",
                  "lease_expiration_date": "2031-03-31",
              }},
    )
    db_session.refresh(company)
    assert company.current_sf_occupied_source == "manual"
    assert company.lease_expiry_source == "manual"

    # A CoStar row for the same tenant at the same address, carrying its own SF
    # and a different break date — exactly what would overwrite an unprotected
    # record.
    row = {col: "" for col in COSTAR_TENANT_COLS}
    row.update({
        "Tenant Name": "Manual Guard Co", "Address": address, "State": "VA",
        "Submarket": "Reston", "SF Occupied": "2500", "Industry": "Technology",
        "Employees": "20", "Next Break Date": "2026-12-31",
    })
    r = client.post(
        "/api/companies/costar-import",
        files={"file": ("tenants.csv", _costar_csv([row]), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1          # it really did match this company

    db_session.refresh(company)
    assert company.current_sf_occupied == 9850
    assert company.current_sf_occupied_source == "manual"
    assert company.current_address == address
    assert company.lease_expiry_date == date(2031, 3, 31)
    assert company.lease_expiry_source == "manual"


# ══ 14. Leases are a list ═════════════════════════════════════════════════════

_OLDER = dict(
    lease_commencement_date=_field("March 1, 2017", "Term commences March 1, 2017."),
    lease_expiration_date=_field("June 30, 2027", "and expires June 30, 2027."),
    rentable_square_footage=_field("12,500 rentable square feet", "containing 12,500 RSF"),
)
_NEWER = dict(
    lease_commencement_date=_field("July 1, 2027", "Term commences July 1, 2027."),
    lease_expiration_date=_field("June 30, 2034", "and expires June 30, 2034."),
    rentable_square_footage=_field("15,000 rentable square feet", "containing 15,000 RSF"),
    base_rent=_field("$540,000 per year", "Base Rent of $540,000 per annum"),
)


def _upload_and_confirm(client, monkeypatch, company, name, body, **overrides):
    _stub_route(monkeypatch, _full_extraction(**overrides))
    r = _upload(client, company, name=name, body=body)
    assert r.status_code == 200, r.text
    c = client.post(f"/api/leases/companies/{company.id}/confirm",
                    json={"accepted_fields": list(LEASE_FIELDS)})
    assert c.status_code == 200, c.text
    return c.json()


def test_a_second_upload_creates_a_new_record_and_leaves_the_first_intact(
    db_session, client, leases_dir, monkeypatch,
):
    """The prior term's rent, escalations and options are what a renewal runs on."""
    company = _company(db_session, "Renewal Co", "CO-RNW")
    _upload_and_confirm(client, monkeypatch, company, "Original.pdf",
                        b"%PDF-original", **_OLDER)
    first = _current(db_session, company)
    first_id = first.id
    first_extraction = first.extraction_json

    _stub_route(monkeypatch, _full_extraction(**_NEWER))
    body = _upload(client, company, name="Renewal.pdf", body=b"%PDF-renewal").json()

    assert _lease_count(db_session, company) == 2
    first = db_session.get(Lease, first_id)
    second = _current(db_session, company)
    assert second.id != first_id
    assert second.file_name == "Renewal.pdf"
    # Demoted — and nothing about it overwritten.
    assert first.is_current is False
    assert first.file_name == "Original.pdf"
    assert first.expiration_date == date(2027, 6, 30)
    assert first.commencement_date == date(2017, 3, 1)
    assert first.rentable_sf == 12500
    assert first.base_rent == "$412,500 per year"
    assert first.escalation_terms == "3% annually"
    assert "nine (9) months notice" in json.loads(first.extraction_json)["renewal_options"]["source_text"]
    assert first.extraction_json == first_extraction
    assert first.confirmed_at is not None
    assert (leases_dir / "Original.pdf").read_bytes() == b"%PDF-original"

    # The Deal card: the new lease on top, the first readable as a prior term.
    assert body["lease_file_name"] == "Renewal.pdf"
    prior = [p for p in body["prior_leases"] if p["lease_id"] == first_id]
    assert len(prior) == 1
    assert prior[0]["lease_file_name"] == "Original.pdf"
    assert prior[0]["is_current"] is False
    assert prior[0]["has_extraction"] is True
    assert {f["field"] for f in prior[0]["fields"]} == set(LEASE_FIELDS)
    # Its own file opens.
    opened = client.get(f"/api/leases/{first_id}/file")
    assert opened.status_code == 200
    assert opened.content == b"%PDF-original"


def test_exactly_one_lease_is_current(db_session, client, leases_dir, monkeypatch):
    company = _company(db_session, "Three Co", "CO-3LS")
    for i in range(3):
        _stub_route(monkeypatch, _full_extraction())
        _upload(client, company, name=f"L{i}.pdf", body=b"%PDF")
    db_session.expire_all()
    flags = [l.is_current for l in db_session.query(Lease).filter_by(company_id=company.id)]
    assert sorted(flags) == [False, False, True]


def test_the_company_takes_its_values_from_the_latest_commencement_date(
    db_session, client, leases_dir, monkeypatch,
):
    """Upload order does not decide it — the commencement date does.

    The renewal is confirmed FIRST, then the original lease is linked for the
    record. Confirming the original re-ranks it behind the renewal, and it does
    not touch the company.
    """
    company = _company(db_session, "Ranked Co", "CO-RNK")
    _upload_and_confirm(client, monkeypatch, company, "Renewal.pdf", b"%PDF-new", **_NEWER)
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2034, 6, 30)

    result = _upload_and_confirm(client, monkeypatch, company, "Original.pdf",
                                 b"%PDF-old", **_OLDER)
    assert result["is_current"] is False
    assert result["written"] == {}

    current = _current(db_session, company)
    assert current.file_name == "Renewal.pdf"
    assert current.commencement_date == date(2027, 7, 1)
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2034, 6, 30)
    assert company.current_sf_occupied == 15000
    # The original is a complete prior term all the same.
    prior = next(l for l in db_session.query(Lease).filter_by(company_id=company.id)
                 if not l.is_current)
    assert prior.expiration_date == date(2027, 6, 30)
    assert prior.rentable_sf == 12500


def test_deleting_the_current_lease_promotes_the_next_most_recent(
    db_session, client, leases_dir, monkeypatch,
):
    company = _company(db_session, "Promote Co", "CO-PRM")
    _upload_and_confirm(client, monkeypatch, company, "Original.pdf", b"%PDF-old", **_OLDER)
    original_id = _current(db_session, company).id
    _upload_and_confirm(client, monkeypatch, company, "Renewal.pdf", b"%PDF-new", **_NEWER)
    renewal = _current(db_session, company)
    assert renewal.file_name == "Renewal.pdf"
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2034, 6, 30)

    r = client.delete(f"/api/companies/{company.company_id}/lease",
                      params={"lease_id": renewal.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_file_name"] == "Renewal.pdf"
    assert body["promoted_lease_id"] == original_id
    assert body["promoted_file_name"] == "Original.pdf"

    promoted = _current(db_session, company)
    assert promoted.id == original_id
    assert _lease_count(db_session, company) == 1
    # The company now reads the promoted lease, marked as it was confirmed.
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2027, 6, 30)
    assert company.current_sf_occupied == 12500
    assert company.lease_expiry_source == "lease_document"
    assert company.current_sf_occupied_source == "lease_document"
    # Only the removed lease's file went.
    assert not (leases_dir / "Renewal.pdf").exists()
    assert (leases_dir / "Original.pdf").read_bytes() == b"%PDF-old"
    assert client.get(f"/api/leases/companies/{company.id}/file").content == b"%PDF-old"


def test_deleting_without_an_id_removes_the_current_lease_and_promotes(
    db_session, client, leases_dir, monkeypatch,
):
    """The existing call — DELETE /companies/{id}/lease with no id — keeps working."""
    company = _company(db_session, "Default Delete Co", "CO-DDL")
    _upload_and_confirm(client, monkeypatch, company, "Old.pdf", b"%PDF-old", **_OLDER)
    _upload_and_confirm(client, monkeypatch, company, "New.pdf", b"%PDF-new", **_NEWER)
    r = client.delete(f"/api/companies/{company.company_id}/lease")
    assert r.status_code == 200, r.text
    assert r.json()["removed_file_name"] == "New.pdf"
    assert _current(db_session, company).file_name == "Old.pdf"


def test_deleting_a_prior_term_leaves_the_current_lease_and_company_alone(
    db_session, client, leases_dir, monkeypatch,
):
    company = _company(db_session, "Prior Delete Co", "CO-PDL")
    _upload_and_confirm(client, monkeypatch, company, "Old.pdf", b"%PDF-old", **_OLDER)
    old_id = _current(db_session, company).id
    _upload_and_confirm(client, monkeypatch, company, "New.pdf", b"%PDF-new", **_NEWER)

    r = client.delete(f"/api/companies/{company.company_id}/lease",
                      params={"lease_id": old_id})
    assert r.status_code == 200, r.text
    assert r.json()["promoted_lease_id"] is None
    assert _current(db_session, company).file_name == "New.pdf"
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2034, 6, 30)
    assert not (leases_dir / "Old.pdf").exists()
    assert (leases_dir / "New.pdf").exists()


def test_deleting_another_companys_lease_by_id_is_a_clean_404(
    db_session, client, leases_dir,
):
    a = _company(db_session, "Owner Co", "CO-OWN")
    b = _company(db_session, "Other Co", "CO-OTH")
    _upload(client, a, name="Mine.pdf")
    _upload(client, b, name="Theirs.pdf")
    theirs = _current(db_session, b).id
    r = client.delete(f"/api/companies/{a.company_id}/lease", params={"lease_id": theirs})
    assert r.status_code == 404
    assert (leases_dir / "Theirs.pdf").exists()


def test_a_prior_term_can_be_confirmed_by_id_without_touching_the_company(
    db_session, client, leases_dir, monkeypatch,
):
    company = _company(db_session, "Late Confirm Co", "CO-LCF")
    _stub_route(monkeypatch, _full_extraction(**_OLDER))
    _upload(client, company, name="Old.pdf")
    old_id = _current(db_session, company).id
    _upload_and_confirm(client, monkeypatch, company, "New.pdf", b"%PDF-new", **_NEWER)

    r = client.post(f"/api/leases/companies/{company.id}/confirm",
                    json={"lease_id": old_id, "accepted_fields": list(LEASE_FIELDS)})
    assert r.status_code == 200, r.text
    assert r.json()["is_current"] is False
    assert db_session.get(Lease, old_id).expiration_date == date(2027, 6, 30)
    db_session.refresh(company)
    assert company.lease_expiry_date == date(2034, 6, 30)


def test_the_deal_card_does_not_query_once_per_lease(
    db_session, client, leases_dir, monkeypatch,
):
    """A company with many leases renders in the same number of queries as one."""
    from sqlalchemy import event

    def _queries_for(company):
        seen = []

        def _count(conn, cursor, statement, *args):
            seen.append(statement)

        engine = db_session.get_bind()
        # Same starting state for both measurements: nothing cached in the
        # session, so neither call gets a free ride on the other's loads.
        client.get(f"/api/leases/companies/{company.id}")
        db_session.expire_all()
        event.listen(engine, "before_cursor_execute", _count)
        try:
            assert client.get(f"/api/leases/companies/{company.id}").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return len(seen)

    one = _company(db_session, "One Lease Co", "CO-1LS")
    many = _company(db_session, "Many Lease Co", "CO-MLS")
    _stub_route(monkeypatch, _full_extraction())
    _upload(client, one, name="Only.pdf")
    for i in range(8):
        _upload(client, many, name=f"Term {i}.pdf")

    assert _queries_for(many) == _queries_for(one)


def _legacy_companies_db():
    """An in-memory database shaped like one from before leases were a list."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY, name TEXT, current_submarket TEXT,
            last_modified_by_user DATETIME,
            lease_file_name TEXT, lease_uploaded_at DATETIME,
            lease_extraction_json TEXT
        )
    """)
    return conn


def test_the_single_lease_migration_handles_zero_leases_cleanly():
    """Collaborative AV's draft is being removed — the migration may find nothing."""
    from migrations.ensure_schema import migrate_company_leases_to_table

    conn = _legacy_companies_db()
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'No Lease Co')")
    cur = conn.cursor()
    assert migrate_company_leases_to_table(cur) == 0
    assert migrate_company_leases_to_table(cur) == 0          # and again
    assert cur.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0

    # A database with no companies rows at all, and one with no legacy columns.
    empty = _legacy_companies_db()
    assert migrate_company_leases_to_table(empty.cursor()) == 0
    import sqlite3
    modern = sqlite3.connect(":memory:")
    modern.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT)")
    assert migrate_company_leases_to_table(modern.cursor()) == 0


def test_the_single_lease_migration_moves_a_lease_and_clears_the_old_columns():
    from migrations.ensure_schema import migrate_company_leases_to_table

    extraction = extract_lease(["p"], extractor=_stub_extractor(_full_extraction()))
    for field, entry in extraction.items():
        entry["accepted"] = field != "premises_address"     # Jack unchecked one
    conn = _legacy_companies_db()
    conn.execute(
        "INSERT INTO companies (id, name, lease_file_name, lease_uploaded_at, "
        "lease_extraction_json) VALUES (7, 'Legacy Co', 'Legacy.pdf', "
        "'2026-09-01 10:00:00', ?)",
        (json.dumps(extraction),),
    )
    cur = conn.cursor()
    assert migrate_company_leases_to_table(cur) == 1

    row = cur.execute(
        "SELECT company_id, file_name, is_current, expiration_date, rentable_sf, "
        "premises_address, confirmed_at, extraction_json FROM leases"
    ).fetchone()
    assert row[0] == 7 and row[1] == "Legacy.pdf" and row[2] == 1
    assert row[3] == "2027-06-30"
    assert row[4] == 12500
    assert row[5] is None                    # unchecked stays unwritten
    assert row[6] is not None
    assert json.loads(row[7]) == extraction  # the audit trail, byte for byte

    legacy = cur.execute(
        "SELECT lease_file_name, lease_uploaded_at, lease_extraction_json "
        "FROM companies WHERE id = 7"
    ).fetchone()
    assert legacy == (None, None, None)
    # Re-running moves nothing more.
    assert migrate_company_leases_to_table(cur) == 0
    assert cur.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 1


def test_companies_list_still_returns_all_seven_contract_fields_with_leases(
    db_session, client, leases_dir, monkeypatch,
):
    """The contract holds for a company carrying current and prior leases."""
    company = _company(
        db_session, name="Contract Lease Co", business_id="CO-701",
        current_headcount=30, headcount_growth_pct=5.0, current_submarket="Reston",
        opportunity_score=55.0, priority="WORKABLE",
    )
    _upload_and_confirm(client, monkeypatch, company, "A.pdf", b"%PDF-a", **_OLDER)
    _upload_and_confirm(client, monkeypatch, company, "B.pdf", b"%PDF-b", **_NEWER)

    row = next(r for r in client.get("/api/companies/").json()
               if r["company_id"] == "CO-701")
    for key in ("priority", "current_headcount", "headcount_growth_pct",
                "lease_expiry_months", "current_submarket", "opportunity_score",
                "company_id"):
        assert key in row, key
    assert row["lease_expiry_months"] is not None
