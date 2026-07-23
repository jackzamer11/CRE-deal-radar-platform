"""Activity-log mining contract.

Locks the two hard rules: activity logs are never modified, and only STATED
facts are recorded (a null field writes nothing — no fabrication, no noise).
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.models.outreach_log  # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.models.observation import Observation
from app.services.activity_intel_service import (
    REQUIREMENT_FIELDS,
    build_log_text,
    mine_activity_log,
    mine_all_activity_logs,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _log(db, **kw):
    defaults = dict(log_date=date(2026, 5, 1), action_type="CALL",
                    action_taken="Called tenant", outcome=None, notes=None)
    defaults.update(kw)
    log = ActivityLog(**defaults)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _blank():
    return {f: {"value": None, "confidence": None, "snippet": None} for f in REQUIREMENT_FIELDS}


# A realistic extraction from a messy note: some fields stated, most not.
def _partial_extractor(pages_text):
    out = _blank()
    out["req_sf_min"] = {"value": "300", "confidence": 0.9, "snippet": "between 300 and 700 sqft"}
    out["req_sf_max"] = {"value": "700", "confidence": 0.9, "snippet": "between 300 and 700 sqft"}
    out["req_submarkets"] = {"value": "Alexandria", "confidence": 0.95, "snippet": "Alexandria"}
    out["req_access_needs"] = {"value": "elevator, convenient parking",
                               "confidence": 0.85, "snippet": "elevator ... parking"}
    return out


def test_activity_log_is_never_modified(db):
    log = _log(db, action_taken="wants 300-700 sqft in Alexandria",
               outcome="elevator important", notes="follow up")
    before = (log.action_taken, log.outcome, log.notes, log.stage, log.action_type)

    mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    db.refresh(log)

    assert (log.action_taken, log.outcome, log.notes, log.stage, log.action_type) == before
    assert db.query(ActivityLog).count() == 1  # nothing deleted either


def test_only_stated_fields_become_observations(db):
    log = _log(db, company_id=None)
    created = mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()

    fields = {o.field for o in created}
    assert fields == {"req_sf_min", "req_sf_max", "req_submarkets", "req_access_needs"}
    # Unstated fields write nothing at all (no null-row noise).
    assert db.query(Observation).count() == 4
    assert all(o.value is not None for o in created)


def test_facts_carry_provenance_back_to_the_log(db):
    log = _log(db)
    created = mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    assert all(o.source_doc == f"activity_log:{log.id}" for o in created)
    assert all(o.human_verified is False for o in created)  # goes to Review queue
    snippets = [o.source_snippet for o in created]
    assert all(s for s in snippets)


def test_company_linked_log_attaches_facts_to_company(db):
    linked = _log(db, company_id=42)
    unlinked = _log(db, company_id=None)

    a = mine_activity_log(linked, db, extractor=_partial_extractor)
    b = mine_activity_log(unlinked, db, extractor=_partial_extractor)
    db.commit()

    assert all(o.entity_type == "company" and o.entity_id == 42 for o in a)
    assert all(o.entity_type == "activity_log" and o.entity_id == unlinked.id for o in b)


def test_mine_all_is_idempotent(db):
    _log(db); _log(db)

    first = mine_all_activity_logs(db, extractor=_partial_extractor)
    assert first["processed"] == 2
    assert first["facts"] == 8  # 4 fields x 2 logs

    second = mine_all_activity_logs(db, extractor=_partial_extractor)
    assert second["processed"] == 0      # already mined -> skipped
    assert db.query(Observation).count() == 8  # no duplicates


def test_empty_note_records_no_facts_but_is_marked_processed(db):
    _log(db, action_taken="left voicemail")
    result = mine_all_activity_logs(db, extractor=lambda t: _blank())
    assert result["processed"] == 1
    assert result["facts"] == 0
    assert db.query(Observation).count() == 0
    row = db.query(IntelActivityExtraction).one()
    assert row.status == "empty"  # won't be re-processed next run


def test_one_bad_note_does_not_abort_the_batch(db):
    good = _log(db, action_taken="wants 300-700 sqft")
    bad = _log(db, action_taken="explodes")

    def flaky(text):
        if "explodes" in text:
            raise RuntimeError("model error")
        return _partial_extractor(text)

    result = mine_all_activity_logs(db, extractor=flaky)
    assert result["processed"] == 1
    assert result["failed"] == 1
    assert db.query(ActivityLog).count() == 2  # both logs still intact
    statuses = {r.activity_log_id: r.status for r in db.query(IntelActivityExtraction).all()}
    assert statuses[good.id] == "done"
    assert statuses[bad.id] == "failed"


def test_build_log_text_includes_all_freeform_fields(db):
    log = _log(db, action_taken="A", outcome="B", notes="C",
               follow_up_action="D", subject="S")
    text = build_log_text(log)
    for part in ("A", "B", "C", "D", "S"):
        assert part in text
