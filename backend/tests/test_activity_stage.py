"""
Activity Log stage-tracking + re-engage tests.

Covered:
  (a) stage defaults to 'Sent' for new entries (ORM default + create endpoint).
  (b) next_touch_date saves and reads back correctly via the stage endpoint.
  (c) Re-engage Today surfaces only entries with a due next_touch_date and a
      non-Sent stage; entries with no next_touch_date never appear; moving an
      entry back to 'Sent' clears its date and removes it from Re-engage.
  (d) contact-name parsing extracts [Name] from the standard
      "Emailed/Called <Name> re <Company>" pattern and falls back to None on
      non-standard lines.
  (e) empty / null inputs never 500.

In-memory SQLite, dependency-overridden get_db, no live DB or network.
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
from app.api.routes.activity import parse_contact_name
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


def _seed(db_session, *, action_taken="Logged a note", stage="Sent", next_touch_date=None):
    log = ActivityLog(
        action_type="EMAIL",
        action_taken=action_taken,
        stage=stage,
        next_touch_date=next_touch_date,
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


# ── (a) stage defaults to Sent ─────────────────────────────────────────────────

def test_stage_defaults_to_sent_orm(db_session):
    log = ActivityLog(action_type="NOTE", action_taken="Just a note")
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    assert log.stage == "Sent"
    assert log.next_touch_date is None


def test_stage_defaults_to_sent_via_create_endpoint(client):
    resp = client.post("/api/activity/", json={"action_type": "CALL", "action_taken": "Rang owner"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage"] == "Sent"
    assert body["next_touch_date"] is None


# ── (b) next_touch_date saves and returns ──────────────────────────────────────

def test_next_touch_date_saves_and_returns(client):
    created = client.post("/api/activity/", json={"action_type": "EMAIL", "action_taken": "Emailed Bob re X"}).json()
    revisit = (date.today() + timedelta(days=14)).isoformat()
    resp = client.patch(f"/api/activity/{created['id']}/stage",
                        json={"stage": "Dormant", "next_touch_date": revisit})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage"] == "Dormant"
    assert body["next_touch_date"] == revisit
    # And it persists on subsequent reads.
    listed = client.get("/api/activity/").json()
    match = next(r for r in listed if r["id"] == created["id"])
    assert match["next_touch_date"] == revisit


def test_invalid_stage_is_400_not_500(client):
    created = client.post("/api/activity/", json={"action_type": "NOTE", "action_taken": "x"}).json()
    resp = client.patch(f"/api/activity/{created['id']}/stage", json={"stage": "Bogus"})
    assert resp.status_code == 400


# ── (c) Re-engage Today filtering ──────────────────────────────────────────────

def test_re_engage_empty_is_200(client):
    resp = client.get("/api/activity/re-engage")
    assert resp.status_code == 200
    assert resp.json() == []


def test_re_engage_excludes_entries_without_next_touch_date(db_session, client):
    # Due + non-Sent → should appear.
    _seed(db_session, stage="Dormant", next_touch_date=date.today())
    # No next_touch_date → must never appear, even if non-Sent.
    _seed(db_session, stage="Interested", next_touch_date=None)
    # Future date → not due yet.
    _seed(db_session, stage="In Play", next_touch_date=date.today() + timedelta(days=5))

    rows = client.get("/api/activity/re-engage").json()
    stages = [r["stage"] for r in rows]
    assert stages == ["Dormant"], f"only the due, dated entry should surface; got {stages}"


def test_re_engage_includes_past_due(db_session, client):
    _seed(db_session, stage="Not Interested", next_touch_date=date.today() - timedelta(days=3))
    rows = client.get("/api/activity/re-engage").json()
    assert len(rows) == 1
    assert rows[0]["stage"] == "Not Interested"


def test_moving_back_to_sent_removes_from_re_engage(db_session, client):
    log = _seed(db_session, action_taken="Emailed Carol re Initech",
                stage="Dormant", next_touch_date=date.today())
    # Initially surfaced.
    assert len(client.get("/api/activity/re-engage").json()) == 1

    resp = client.patch(f"/api/activity/{log.id}/stage", json={"stage": "Sent"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["next_touch_date"] is None, "moving to Sent must clear the revisit date"

    # Disappears from Re-engage Today automatically.
    assert client.get("/api/activity/re-engage").json() == []


# ── (d) contact-name parsing ───────────────────────────────────────────────────

@pytest.mark.parametrize("action_taken,expected", [
    ("Emailed John Smith re Acme Corp", "John Smith"),
    ("Called Jane Doe re Globex Inc",   "Jane Doe"),
    ("emailed maria garcia re Initech", "maria garcia"),   # case-insensitive verb
    ("Emailed John Smith",              "John Smith"),       # no "re" tail
])
def test_parse_contact_name_standard(action_taken, expected):
    assert parse_contact_name(action_taken) == expected


@pytest.mark.parametrize("action_taken", [
    None,
    "",
    "Logged a research note about the building",
    "Snoozed until 2026-01-01",
    "Stage updated: Sent -> Replied",
    "Emailed contact re Acme",   # generic placeholder name → no real contact
])
def test_parse_contact_name_falls_back_gracefully(action_taken):
    assert parse_contact_name(action_taken) is None


def test_contact_name_exposed_in_api(client):
    standard = client.post("/api/activity/",
                          json={"action_type": "EMAIL", "action_taken": "Emailed Dana Lee re Umbrella"}).json()
    assert standard["contact_name"] == "Dana Lee"

    nonstd = client.post("/api/activity/",
                        json={"action_type": "NOTE", "action_taken": "Reviewed the rent roll"}).json()
    assert nonstd["contact_name"] is None
    # Non-standard entry data is unchanged.
    assert nonstd["action_taken"] == "Reviewed the rent roll"
