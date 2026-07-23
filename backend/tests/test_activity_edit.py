"""Editing an activity log entry — and keeping the intelligence layer in sync."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.models.observation import Observation
from app.services import activity_intel_service as svc


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


def _log(db, **kw):
    defaults = dict(log_date=date(2026, 5, 1), action_type="EMAIL",
                    action_taken="original text", outcome=None, notes=None)
    defaults.update(kw)
    log = ActivityLog(**defaults)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _fake_facts(text):
    return {
        f: {"value": "Reston" if f == "req_submarkets" else None,
            "confidence": 0.9 if f == "req_submarkets" else None, "snippet": None}
        for f in svc.REQUIREMENT_FIELDS
    }


def test_every_freeform_field_is_editable(client, db_session, monkeypatch):
    monkeypatch.setattr(svc, "_extract_facts_via_llm", lambda text: _fake_facts(text))
    log = _log(db_session)

    resp = client.patch(f"/api/activity/{log.id}", json={
        "action_type": "CALL",
        "action_taken": "corrected action",
        "outcome": "corrected outcome",
        "notes": "corrected notes",
        "follow_up_action": "call back",
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_type"] == "CALL"
    assert body["action_taken"] == "corrected action"
    assert body["outcome"] == "corrected outcome"
    assert body["notes"] == "corrected notes"
    assert body["follow_up_action"] == "call back"


def test_edit_resyncs_the_intelligence_layer(client, db_session, monkeypatch):
    monkeypatch.setattr(svc, "_extract_facts_via_llm", lambda text: _fake_facts(text))
    log = _log(db_session)
    db_session.add(Observation(entity_type="activity_log", entity_id=log.id,
                               field="req_submarkets", value="Alexandria",
                               source_doc=f"activity_log:{log.id}",
                               human_verified=True, verified_by="auto"))
    db_session.commit()

    client.patch(f"/api/activity/{log.id}", json={"action_taken": "now wants Reston"})

    rows = db_session.query(Observation).filter_by(
        source_doc=f"activity_log:{log.id}").all()
    assert len(rows) == 1                 # stale Alexandria fact replaced
    assert rows[0].value == "Reston"      # reflects the edited text


def test_edit_is_saved_even_if_remining_fails(client, db_session, monkeypatch):
    """An extraction outage must never cost the user their edit."""
    def boom(text):
        raise RuntimeError("credit balance too low")
    monkeypatch.setattr(svc, "_extract_facts_via_llm", boom)

    log = _log(db_session)
    db_session.add(IntelActivityExtraction(activity_log_id=log.id, status="done",
                                           fields_found=1))
    db_session.commit()

    resp = client.patch(f"/api/activity/{log.id}", json={"action_taken": "edited anyway"})

    assert resp.status_code == 200
    assert resp.json()["action_taken"] == "edited anyway"
    db_session.expire_all()
    assert db_session.query(ActivityLog).get(log.id).action_taken == "edited anyway"
    # Queued for the next mining run rather than left stale forever.
    assert db_session.query(IntelActivityExtraction).filter_by(
        activity_log_id=log.id).count() == 0


def test_no_op_edit_leaves_everything_alone(client, db_session, monkeypatch):
    called = []
    monkeypatch.setattr(svc, "_extract_facts_via_llm",
                        lambda text: called.append(1) or _fake_facts(text))
    log = _log(db_session, action_taken="original text")

    resp = client.patch(f"/api/activity/{log.id}", json={"action_taken": "original text"})

    assert resp.status_code == 200
    assert called == []  # unchanged text must not burn an extraction call


def test_editing_a_missing_entry_404s(client):
    assert client.patch("/api/activity/9999", json={"notes": "x"}).status_code == 404
