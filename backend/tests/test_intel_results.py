"""What happened after a card was accepted.

Decisions alone say what Jack thought; what followed says whether the card was
worth it. Over time that shows which kinds of card turn into conversations and
deals. What this file locks:

  - an accepted card's outcome is read off the timeline: the first real touch
    after accepting, how many, and the furthest stage reached
  - copies, and anything before the decision, do not count
  - History lists decisions only (not cards the machine retired), each
    accepted one with its outcome
  - the results summary counts, per kind of card, acted-on / reached
    Interested / closed

In-memory SQLite. No live DB, no network.
"""
import json
from datetime import date, datetime, timedelta

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
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelFeedback, IntelOpportunity
from app.services.intel_results_service import outcome_for, results_summary

ACCEPTED = datetime(2026, 12, 1, 9, 0)


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


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def halverson(db):
    co = Company(company_id="CO-001", name="Halverson Dental", industry="Medical")
    db.add(co)
    db.commit()
    maria = Contact(name="Maria Chen", company_id=co.id, contact_type="tenant", stage="Sent")
    db.add(maria)
    db.commit()
    return co, maria


def _card(db, co, status="accepted", kind="lease_expiring", when=ACCEPTED):
    opp = IntelOpportunity(title="Lease expiring — Halverson Dental", entity_type="company",
                           entity_id=co.id, score=139, status=status, signals_json="[]",
                           dedup_key=f"company:{co.id}:{kind}", surfaced_at=when)
    db.add(opp)
    db.commit()
    db.add(IntelFeedback(opportunity_id=opp.id, disposition=status, created_at=when,
                         reason_category=None if status == "accepted" else "timing"))
    db.commit()
    return opp


def _log(db, co, maria, on, **kw):
    kw.setdefault("action_type", "CALL")
    db.add(ActivityLog(log_date=on, action_taken="x", contact_id=maria.id,
                       company_stamp_id=co.id, channel=kw.pop("channel", "call"), **kw))
    db.commit()


def test_the_outcome_is_read_off_the_timeline(db, halverson):
    co, maria = halverson
    opp = _card(db, co)
    _log(db, co, maria, date(2026, 11, 20))                        # before: ignored
    _log(db, co, maria, date(2026, 12, 2), participation=True)     # a copy: ignored
    _log(db, co, maria, date(2026, 12, 3))
    _log(db, co, maria, date(2026, 12, 10), action_type="STAGE_CHANGE",
         stage_from="Sent", stage_to="In Play")
    _log(db, co, maria, date(2026, 12, 12), channel="meeting")

    result = outcome_for(db, opp)
    assert result["first_touch"] == date(2026, 12, 3)
    assert result["first_touch_channel"] == "call"
    assert result["touches"] == 2
    assert result["best_stage"] == "In Play"
    assert result["closed"] is False


def test_history_lists_decisions_with_outcomes_and_not_machine_retirements(db, client, halverson):
    co, maria = halverson
    _card(db, co)
    retired = IntelOpportunity(title="old", entity_type="company", entity_id=co.id, score=1,
                               status="superseded", dedup_key="x:1:lease_expiring")
    db.add(retired)
    db.commit()
    _log(db, co, maria, date(2026, 12, 3))

    history = client.get("/api/intel/history").json()
    assert [h["status"] for h in history] == ["accepted"]
    assert history[0]["outcome"]["first_touch"] == "2026-12-03"


def test_the_summary_counts_what_accepted_cards_led_to(db, halverson):
    co, maria = halverson
    _card(db, co)                                    # acted on, reached Closed
    _log(db, co, maria, date(2026, 12, 5))
    _log(db, co, maria, date(2027, 3, 1), action_type="STAGE_CHANGE",
         stage_from="In Play", stage_to="Closed")
    other = Company(company_id="CO-002", name="Quiet Co", industry="Office")
    db.add(other)
    db.commit()
    _card(db, other)                                 # accepted, nothing happened
    _card(db, other, status="rejected", kind="stated_requirement")

    rows = {r["family"]: r for r in results_summary(db)}
    assert rows["lease"]["accepted"] == 2
    assert rows["lease"]["acted"] == 1
    assert rows["lease"]["interested"] == 1
    assert rows["lease"]["closed"] == 1
    assert rows["stated_requirement"]["rejected"] == 1
