"""Intel cards you can act on without checking them first.

A card is a prompt to call. It must not say "no follow-up recorded" when there
was one, and it must not nag while the deal is already being worked, while the
tenant said when to come back, or right after they said no. What this file locks:

  - the card states the real last touch, read off the company's timeline
  - In Play holds the card; a next-touch date in the future holds it until then
  - Dormant holds for 90 days, Not Interested for a year — unless a next-touch
    date they gave comes due first
  - a past client is never held, is labelled, and ranks a little higher
  - held cards are counted, not silently dropped, and come back on their own

In-memory SQLite. No live DB, no network.
"""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.intel_signal_service import generate_with_stats

TODAY = date(2026, 10, 1)


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


def _setup(db, *, stage="Sent", next_touch=None, stage_changed=None, past=False):
    """Halverson Dental, Maria on it, a note saying the lease ends in ~8 months."""
    co = Company(company_id="CO-001", name="Halverson Dental", industry="Medical")
    db.add(co)
    db.commit()
    maria = Contact(name="Maria Chen", company_id=co.id, contact_type="tenant",
                    stage=stage, next_touch_date=next_touch, triaged=True,
                    stage_changed_at=stage_changed or TODAY - timedelta(days=10),
                    is_past_client=past)
    db.add(maria)
    db.commit()
    note = ActivityLog(log_date=TODAY - timedelta(days=40), action_type="EMAIL",
                       action_taken="Maria: lease is up next summer", channel="email",
                       direction="inbound", contact_id=maria.id, company_stamp_id=co.id)
    db.add(note)
    db.commit()
    db.add(Observation(entity_type="company", entity_id=co.id, field="expiration_date",
                       value=(TODAY + timedelta(days=240)).isoformat(),
                       source_doc=f"activity_log:{note.id}", human_verified=True,
                       verified_by="auto"))
    db.commit()
    return co, maria


def _run(db, today=TODAY):
    return generate_with_stats(db, today=today)[1]


def _open(db):
    return db.query(IntelOpportunity).filter_by(status="open").all()


def _log(db, co, maria, days_ago, channel="call"):
    db.add(ActivityLog(log_date=TODAY - timedelta(days=days_ago), action_type="CALL",
                       action_taken="Talked", channel=channel, direction="outbound",
                       contact_id=maria.id, company_stamp_id=co.id))
    db.commit()


# ══ 1. The card says what actually happened ═══════════════════════════════════

def test_the_card_states_the_real_last_touch(db):
    co, maria = _setup(db)
    _log(db, co, maria, days_ago=5)
    _run(db)
    card = _open(db)[0]
    assert "Last touch Sep 26 (call, Maria Chen), 5 days ago." in card.rationale
    assert "no follow-up" not in card.rationale.lower()


def test_copies_and_dividers_are_not_a_touch(db):
    co, maria = _setup(db)
    db.add(ActivityLog(log_date=TODAY - timedelta(days=1), action_type="EMAIL",
                       action_taken="cc copy", participation=True,
                       contact_id=maria.id, company_stamp_id=co.id))
    db.add(ActivityLog(log_date=TODAY, action_type="STAGE_CHANGE", action_taken="Sent → Replied",
                       contact_id=maria.id, company_stamp_id=co.id))
    db.commit()
    _run(db)
    assert "Last touch Aug 22 (email, Maria Chen), 40 days ago." in _open(db)[0].rationale


# ══ 2. Holding a card while it is being worked ════════════════════════════════

def test_in_play_holds_the_card_and_it_is_counted(db):
    _setup(db, stage="In Play")
    stats = _run(db)
    assert _open(db) == []
    assert stats["held_by_stage"] == 1


def test_a_next_touch_date_holds_the_card_until_that_day(db):
    _setup(db, stage="Interested", next_touch=TODAY + timedelta(days=20))
    _run(db)
    assert _open(db) == []
    _run(db, today=TODAY + timedelta(days=20))
    assert len(_open(db)) == 1


def test_dormant_waits_ninety_days(db):
    _setup(db, stage="Dormant", stage_changed=TODAY - timedelta(days=30))
    _run(db)
    assert _open(db) == []
    _run(db, today=TODAY + timedelta(days=60))
    assert len(_open(db)) == 1


def test_not_interested_waits_a_year_unless_their_date_comes_first(db):
    _setup(db, stage="Not Interested", stage_changed=TODAY - timedelta(days=30))
    _run(db, today=TODAY + timedelta(days=60))
    assert _open(db) == []


def test_not_interested_with_a_date_that_has_come_returns(db):
    _setup(db, stage="Not Interested", stage_changed=TODAY - timedelta(days=30),
           next_touch=TODAY - timedelta(days=1))
    _run(db)
    assert len(_open(db)) == 1


def test_one_person_still_open_keeps_the_company_in_play(db):
    co, maria = _setup(db, stage="Not Interested")
    db.add(Contact(name="Office Manager", company_id=co.id, contact_type="tenant",
                   stage="Sent", triaged=True))
    db.commit()
    _run(db)
    assert len(_open(db)) == 1


def test_a_counterparty_at_the_company_does_not_hold_it(db):
    co, maria = _setup(db)
    db.add(Contact(name="Their Broker", company_id=co.id, contact_type="counterparty",
                   stage="In Play", triaged=True))
    db.commit()
    _run(db)
    assert len(_open(db)) == 1


# ══ 3. Past clients ═══════════════════════════════════════════════════════════

def test_a_past_client_is_never_held_is_labelled_and_ranks_higher(db):
    _setup(db, stage="Closed", past=True)
    _run(db)
    card = _open(db)[0]
    sig = json.loads(card.signals_json)[0]
    assert sig["past_client"] is True
    assert "Past client" in card.rationale
    plain = 100 + 39   # verified, inside the 6-9 month window
    assert card.score == plain + 10


def test_a_past_client_being_worked_is_still_held(db):
    _setup(db, stage="In Play", past=True)
    _run(db)
    assert _open(db) == []
