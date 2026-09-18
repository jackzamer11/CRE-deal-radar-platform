"""Creating an activity log entry with a broker note.

Regression: `notes` is a real column on ActivityLog and is set correctly by
PATCH /{id}/notes and PATCH /{id}, but it was missing from the ActivityCreate
Pydantic model — so FastAPI stripped it off the request body and POST /activity/
silently persisted NULL. No error, no warning; the note just vanished.

Guards:
  (a) POST with a notes value persists it and echoes it back non-null.
  (b) notes stays optional — POST without it still succeeds, notes is None.
  (c) notes survives the round trip through GET /activity/ (the list read path
      rebuilds each row via _to_out(), so a field can be dropped there too).
  (d) the fields that are DERIVED on read are not accepted as create-time input:
      contact_name is parsed from action_taken, and property_address /
      company_name are joined from property_id / company_id. Letting a caller
      set them directly would create a second, conflicting source of truth.

In-memory SQLite only — no live DB, no network.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.routes.activity import ActivityCreate
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_persists_notes(client, db_session):
    """(a) A note supplied at create time reaches the database."""
    resp = client.post("/api/activity/", json={
        "action_type": "CALL",
        "action_taken": "Called Dana Whitfield re Aperture Labs",
        "notes": "Wants 8-10k SF in Reston, decision by Q1.",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["notes"] == "Wants 8-10k SF in Reston, decision by Q1."

    row = db_session.query(ActivityLog).filter_by(id=body["id"]).one()
    assert row.notes == "Wants 8-10k SF in Reston, decision by Q1."


def test_notes_remains_optional(client, db_session):
    """(b) Omitting notes is still valid — it is not suddenly required."""
    resp = client.post("/api/activity/", json={
        "action_type": "NOTE",
        "action_taken": "Left voicemail",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["notes"] is None

    row = db_session.query(ActivityLog).filter_by(id=resp.json()["id"]).one()
    assert row.notes is None


def test_notes_survives_the_list_read_path(client):
    """(c) The note is still there when the entry is read back via GET."""
    created = client.post("/api/activity/", json={
        "action_type": "MEETING",
        "action_taken": "Met Priya Raghavan re Northgate Systems",
        "notes": "Prefers ground floor with street access.",
    })
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]

    listed = client.get("/api/activity/", params={"limit": 1000})
    assert listed.status_code == 200, listed.text

    match = [r for r in listed.json() if r["id"] == entry_id]
    assert match, f"entry {entry_id} missing from GET /activity/"
    assert match[0]["notes"] == "Prefers ground floor with street access."


@pytest.mark.parametrize("derived_field", ["contact_name", "property_address", "company_name"])
def test_derived_fields_are_not_create_inputs(derived_field):
    """(d) Read-side derived/joined fields must not become writable create fields.

    contact_name is regex-parsed from action_taken; property_address and
    company_name are joined off property_id / company_id in _to_out(). If one of
    these ever appears on ActivityCreate, a caller could set it to something that
    contradicts what the derivation produces.
    """
    assert derived_field not in ActivityCreate.model_fields, (
        f"{derived_field} is derived on read — it must not be a create-time input; "
        "pass property_id / company_id, or let it be parsed from action_taken"
    )


def test_contact_name_is_still_derived_not_stored(client):
    """The complement of (d): contact_name is produced by parsing action_taken."""
    resp = client.post("/api/activity/", json={
        "action_type": "EMAIL",
        "action_taken": "Emailed Marcus Feld re Beacon Ridge Partners",
        "notes": "Sent the Q3 availability summary.",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["contact_name"] == "Marcus Feld"   # derived, never sent by the client
    assert body["notes"] == "Sent the Q3 availability summary."
