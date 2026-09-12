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
    AUTO_APPROVE_FIELDS,
    REQUIREMENT_FIELDS,
    auto_approve_existing,
    build_log_text,
    mine_activity_log,
    mine_all_activity_logs,
    remine_activity_log,
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
    # Manual-review fields queue unverified; auto-approve fields clear immediately.
    assert all(
        o.human_verified is (o.field in AUTO_APPROVE_FIELDS) for o in created
    )
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


def test_failed_logs_are_retried_on_the_next_run(db):
    """A transient failure (e.g. exhausted API credits) must not permanently
    skip a log — the next run retries it and clears the failed marker."""
    log = _log(db, action_taken="wants 300-700 sqft")

    def boom(text):
        raise RuntimeError("credit balance too low")

    first = mine_all_activity_logs(db, extractor=boom)
    assert first["failed"] == 1
    assert db.query(IntelActivityExtraction).one().status == "failed"

    # Retry once the outage is over — it is picked up, not skipped.
    second = mine_all_activity_logs(db, extractor=_partial_extractor)
    assert second["processed"] == 1
    assert second["facts"] == 4
    rows = db.query(IntelActivityExtraction).all()
    assert len(rows) == 1 and rows[0].status == "done"  # stale failure cleared
    assert db.query(Observation).count() == 4


def test_mined_note_facts_are_auto_approved(db):
    """Basic facts read out of Jack's own notes clear automatically."""
    log = _log(db)
    created = mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()

    assert created  # sanity
    for obs in created:
        assert obs.human_verified is True
        assert obs.verified_by == "auto"


def test_lease_document_facts_are_never_auto_approved(db):
    """expiration_date is also a lease field — a lease abstract is a legal
    document and must still be reviewed, even though the name overlaps."""
    db.add(Observation(entity_type="company", entity_id=5, field="expiration_date",
                       value="2027-01-01", human_verified=False,
                       source_doc="acme_lease.pdf", source_page=2))
    db.add(Observation(entity_type="company", entity_id=5, field="expiration_date",
                       value="2027-01-01", human_verified=False,
                       source_doc="activity_log:9"))
    db.commit()

    auto_approve_existing(db)

    from_lease = db.query(Observation).filter_by(source_doc="acme_lease.pdf").one()
    from_note = db.query(Observation).filter_by(source_doc="activity_log:9").one()
    assert from_lease.human_verified is False   # still queued for review
    assert from_lease.verified_by is None
    assert from_note.human_verified is True     # note fact cleared
    assert from_note.verified_by == "auto"


def test_auto_approval_is_distinguishable_from_human_approval(db):
    """A machine approval must never look like Jack's own judgement."""
    log = _log(db)
    mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    auto = db.query(Observation).filter_by(field="req_submarkets").one()
    assert auto.verified_by == "auto"
    assert auto.verified_by != "human"


def test_auto_approve_existing_clears_queue_without_altering_values(db):
    """Backfill flips only the verification flag — the fact itself is untouched."""
    log = _log(db)
    created = mine_activity_log(log, db, extractor=_partial_extractor)
    # Simulate rows queued before the rule existed.
    for obs in created:
        obs.human_verified = False
        obs.verified_by = None
    db.commit()

    result = auto_approve_existing(db)
    assert result["approved"] == len(created)
    db.commit()

    rows = {o.field: o for o in db.query(Observation).all()}
    assert all(o.human_verified for o in rows.values())
    # Value, snippet and confidence survive the flip.
    assert rows["req_submarkets"].value == "Alexandria"
    assert rows["req_sf_min"].value == "300"
    assert rows["req_sf_max"].confidence == 0.9
    assert rows["req_access_needs"].source_snippet == "elevator ... parking"


def test_auto_approve_existing_leaves_human_verified_rows_alone(db):
    db.add(Observation(entity_type="company", entity_id=1, field="req_submarkets",
                       value="Tysons", human_verified=True, verified_by="human",
                       source_doc="activity_log:1"))
    db.commit()
    auto_approve_existing(db)
    row = db.query(Observation).one()
    assert row.verified_by == "human"  # not overwritten by the backfill


def _corrected_extractor(pages_text):
    """What the model returns after the note was edited (Reston, not Alexandria)."""
    out = _blank()
    out["req_submarkets"] = {"value": "Reston", "confidence": 0.95, "snippet": "Reston"}
    return out


def test_editing_a_note_replaces_its_machine_facts(db):
    """Edited text must not leave the old extraction behind as a duplicate."""
    log = _log(db, action_taken="wants 300-700 sqft in Alexandria")
    mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    assert db.query(Observation).count() == 4

    log.action_taken = "actually wants space in Reston"   # user edits the entry
    db.commit()
    result = remine_activity_log(log, db, extractor=_corrected_extractor)

    rows = db.query(Observation).all()
    assert result["replaced"] == 4
    assert len(rows) == 1                       # stale facts gone, not duplicated
    assert rows[0].value == "Reston"
    assert rows[0].source_doc == f"activity_log:{log.id}"


def test_remining_keeps_human_verified_facts(db):
    """Jack's own corrections survive a re-mine; machine facts don't."""
    log = _log(db)
    created = mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    # Jack corrected one fact by hand.
    human = created[0]
    human.human_verified = True
    human.verified_by = "human"
    human_value, human_field = human.value, human.field
    db.commit()

    result = remine_activity_log(log, db, extractor=_corrected_extractor)

    assert result["kept_human_verified"] == 1
    kept = db.query(Observation).filter_by(verified_by="human").one()
    assert kept.field == human_field and kept.value == human_value


def test_remine_is_idempotent_across_repeats(db):
    log = _log(db)
    mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    for _ in range(3):
        remine_activity_log(log, db, extractor=_corrected_extractor)
    assert db.query(Observation).count() == 1
    assert db.query(IntelActivityExtraction).count() == 1  # one tracking row


def test_remine_does_not_alter_the_log_itself(db):
    log = _log(db, action_taken="wants 300-700 sqft in Alexandria", outcome="x", notes="y")
    before = (log.action_taken, log.outcome, log.notes, log.action_type)
    mine_activity_log(log, db, extractor=_partial_extractor)
    db.commit()
    remine_activity_log(log, db, extractor=_corrected_extractor)
    db.refresh(log)
    assert (log.action_taken, log.outcome, log.notes, log.action_type) == before


def test_build_log_text_includes_all_freeform_fields(db):
    log = _log(db, action_taken="A", outcome="B", notes="C",
               follow_up_action="D", subject="S")
    text = build_log_text(log)
    for part in ("A", "B", "C", "D", "S"):
        assert part in text
