"""New entries are read as they arrive.

Jack should not have to press Mine for every email the evening check logs. A
new entry is read right after it is saved, in the background, so logging never
waits on the model; anything that fails is left for the Mine button. What this
file locks:

  - logging an entry (by hand or from an email) schedules it to be read
  - nothing is scheduled when switched off or when there is no API key
  - a background read mines the entry, skips copies and anything already read,
    and marks a failure so the button retries it
  - the test suite itself never reaches the API

In-memory SQLite, a stand-in extractor. No live DB, no network.
"""
import os
from datetime import date

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
from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.models.observation import Observation
from app.services import activity_intel_service as miner


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db(factory):
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _extractor(_text):
    out = {f: {"value": None, "confidence": None, "snippet": None} for f in miner.EXTRACTED_FIELDS}
    out["req_sf_min"] = {"value": "3000", "confidence": 0.9, "snippet": "3,000 SF"}
    return out


def _entry(db, **kw):
    log = ActivityLog(log_date=date(2026, 10, 1), action_type="CALL",
                      action_taken="Maria needs 3,000 SF", **kw)
    db.add(log)
    db.commit()
    return log


def test_the_suite_never_reaches_the_api():
    assert os.environ["DEAL_RADAR_AUTO_MINE"] == "0"
    assert miner.auto_mine_enabled() is False


def test_switched_on_with_a_key_it_is_enabled(monkeypatch):
    monkeypatch.setenv("DEAL_RADAR_AUTO_MINE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert miner.auto_mine_enabled() is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert miner.auto_mine_enabled() is False


def test_logging_an_entry_schedules_it_to_be_read(client, monkeypatch):
    monkeypatch.setenv("DEAL_RADAR_AUTO_MINE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = []
    monkeypatch.setattr(miner, "mine_new_entries", lambda ids: seen.append(ids))
    resp = client.post("/api/activity/", json={
        "action_type": "CALL", "action_taken": "Maria needs 3,000 SF",
    })
    assert resp.status_code == 200, resp.text
    assert seen == [[resp.json()["id"]]]


def test_an_emailed_entry_is_scheduled_too(client, monkeypatch):
    monkeypatch.setenv("DEAL_RADAR_AUTO_MINE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = []
    monkeypatch.setattr(miner, "mine_new_entries", lambda ids: seen.append(ids))
    resp = client.post("/api/activity/from-email", json={
        "from_email": "maria@halversondental.com", "from_name": "Maria Chen",
        "direction": "inbound", "subject": "lease", "source_message_id": "<auto-1@mail>",
    })
    assert resp.status_code == 200, resp.text
    assert seen == [[resp.json()["id"]]]


def test_nothing_is_scheduled_when_switched_off(client, monkeypatch):
    seen = []
    monkeypatch.setattr(miner, "mine_new_entries", lambda ids: seen.append(ids))
    client.post("/api/activity/", json={"action_type": "CALL", "action_taken": "x"})
    assert seen == []


def test_a_background_read_mines_new_entries_and_skips_the_rest(db, factory):
    fresh = _entry(db)
    copy = _entry(db, participation=True)
    already = _entry(db)
    db.add(IntelActivityExtraction(activity_log_id=already.id, status="done", fields_found=1))
    db.commit()

    result = miner.mine_new_entries([fresh.id, copy.id, already.id],
                                    extractor=_extractor, session_factory=factory)
    assert result == {"mined": 1, "failed": 0}
    db.expire_all()
    sources = {o.source_doc for o in db.query(Observation).all()}
    assert sources == {f"activity_log:{fresh.id}"}


def test_a_failed_background_read_is_left_for_the_button(db, factory):
    log = _entry(db)

    def broken(_text):
        raise RuntimeError("no credits")

    result = miner.mine_new_entries([log.id], extractor=broken, session_factory=factory)
    assert result == {"mined": 0, "failed": 1}
    db.expire_all()
    marker = db.query(IntelActivityExtraction).filter_by(activity_log_id=log.id).one()
    assert marker.status == "failed"   # the next Mine run retries it
