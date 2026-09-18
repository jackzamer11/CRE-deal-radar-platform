"""Hedged, part-precision lease dates must survive the trip from note to signal.

The live database held twelve extracted expiration dates and the signal engine
could read two of them: everything written the way people actually talk
("~February 2027", "End of 2026") was dropped silently, so Intel produced
nothing at all. These are the exact stored values that were being lost.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all tables on Base.metadata
import app.models.outreach_log  # noqa: F401 — needed for Property relationship mapping
import app.models.outreach_draft  # noqa: F401
from app.database import Base
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.activity_intel_service import _should_auto_approve, requeue_fuzzy_dates
from app.services.intel_signal_service import generate_opportunities, parse_expiry


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# Verbatim values from the live database, with what each one means.
REAL_VALUES = [
    ("December 2026", date(2026, 12, 31), "month"),
    ("~December 2026", date(2026, 12, 31), "month"),
    ("June 2026", date(2026, 6, 30), "month"),
    ("End of 2026", date(2026, 12, 31), "year"),
    ("2026-07-31", date(2026, 7, 31), "exact"),
    ("2030-06-25", date(2030, 6, 25), "exact"),
    ("~2027-01-23 (approx. 6 months from note date)", date(2027, 1, 23), "exact"),
    ("~March 2027 (8 months from note date of 2026-07-23)", date(2027, 3, 31), "month"),
    ("~February 2027 (6 months from note date of 2026-08-17)", date(2027, 2, 28), "month"),
    ("~February 2027 (6 months from mid-August 2026)", date(2027, 2, 28), "month"),
    ("~February 2027", date(2027, 2, 28), "month"),
    ("2 years from note date (approx. 2028-06)", date(2028, 6, 30), "month"),
]


@pytest.mark.parametrize("raw,expected,precision", REAL_VALUES)
def test_real_stored_values_all_parse(raw, expected, precision):
    parsed = parse_expiry(raw)
    assert parsed.date == expected, raw
    assert parsed.precision == precision, raw
    assert parsed.normalized == expected.isoformat()


def test_parenthetical_note_date_never_wins_over_the_stated_month():
    """The value sits outside the parentheses; the parenthetical is the note date
    the model reasoned from. Reading the inside first lands a year early."""
    parsed = parse_expiry("~February 2027 (6 months from note date of 2026-08-17)")
    assert parsed.date == date(2027, 2, 28)
    assert parsed.date.year == 2027


def test_month_precision_resolves_to_month_end():
    # Leases end at month end, not month start.
    assert parse_expiry("February 2027").date == date(2027, 2, 28)
    assert parse_expiry("February 2028").date == date(2028, 2, 29)  # leap year


def test_quarters_and_bare_years():
    assert parse_expiry("Q1 2027").date == date(2027, 3, 31)
    assert parse_expiry("Q4 2027").date == date(2027, 12, 31)
    assert parse_expiry("2027").date == date(2027, 12, 31)
    assert parse_expiry("early 2027").date == date(2027, 3, 31)
    assert parse_expiry("mid 2027").date == date(2027, 6, 30)


def test_text_with_no_date_invents_nothing():
    for junk in ("not a date", "TBD", "when they decide", "", None, "next year"):
        assert parse_expiry(junk).date is None, junk


def test_the_same_fact_stated_twice_is_one_card(db):
    """A note re-mined after an edit can leave several rows saying the same thing
    (the live database holds three "~February 2027" rows for one tenant). The
    upsert already collapses them in the table — the RETURNED list must collapse
    too, or the API hands back the same card three times and miscounts the run."""
    target = date.today() + timedelta(days=200)
    for phrasing in ("~%B %Y", "~%B %Y (6 months from note date)", "%B %Y"):
        db.add(Observation(entity_type="activity_log", entity_id=350,
                           field="expiration_date", value=target.strftime(phrasing),
                           human_verified=False, source_doc="activity_log:350"))
    db.commit()

    touched = generate_opportunities(db)
    assert len(touched) == 1
    assert db.query(IntelOpportunity).count() == 1


def test_a_hedged_month_now_produces_an_opportunity(db):
    """End to end: the shape that used to produce nothing."""
    target = date.today() + timedelta(days=200)
    stored = target.strftime("~%B %Y (6 months from note date)")
    db.add(Observation(entity_type="activity_log", entity_id=350, field="expiration_date",
                       value=stored, confidence=0.8, human_verified=True,
                       source_doc="activity_log:350"))
    db.commit()

    generate_opportunities(db)
    opps = db.query(IntelOpportunity).filter_by(status="open").all()
    assert len(opps) == 1
    assert opps[0].dedup_key == "activity_log:350:lease_expiring"


# ── Auto-approval is value-aware, not just field-aware ───────────────────────

def test_exact_dates_from_notes_still_clear_themselves():
    assert _should_auto_approve("expiration_date", "2027-03-01") is True
    assert _should_auto_approve("req_submarkets", "Alexandria") is True


def test_hedged_dates_queue_for_review():
    """A date decides who gets called and when — a hedge is not a fact."""
    assert _should_auto_approve("expiration_date", "~February 2027") is False
    assert _should_auto_approve("expiration_date", "End of 2026") is False
    assert _should_auto_approve("expiration_date", "sometime soon") is False


def test_requeue_backfill_returns_fuzzy_dates_to_the_queue(db):
    db.add(Observation(entity_type="activity_log", entity_id=1, field="expiration_date",
                       value="~February 2027", confidence=0.7, human_verified=True,
                       verified_by="auto", source_doc="activity_log:1",
                       source_snippet="lease is up around February"))
    db.add(Observation(entity_type="activity_log", entity_id=2, field="expiration_date",
                       value="2027-03-01", human_verified=True, verified_by="auto",
                       source_doc="activity_log:2"))
    db.commit()

    result = requeue_fuzzy_dates(db)
    assert result["requeued"] == 1

    fuzzy = db.query(Observation).filter_by(entity_id=1).one()
    exact = db.query(Observation).filter_by(entity_id=2).one()
    assert fuzzy.human_verified is False and fuzzy.verified_by is None
    assert exact.human_verified is True   # precise dates are left alone
    # Only the flag moves — the fact itself is untouched.
    assert fuzzy.value == "~February 2027"
    assert fuzzy.confidence == 0.7
    assert fuzzy.source_snippet == "lease is up around February"


def test_requeue_backfill_is_idempotent_and_spares_human_verified(db):
    db.add(Observation(entity_type="activity_log", entity_id=3, field="expiration_date",
                       value="End of 2026", human_verified=True, verified_by="human",
                       source_doc="activity_log:3"))
    db.add(Observation(entity_type="activity_log", entity_id=4, field="expiration_date",
                       value="End of 2026", human_verified=True, verified_by="auto",
                       source_doc="activity_log:4"))
    db.commit()

    first = requeue_fuzzy_dates(db)
    second = requeue_fuzzy_dates(db)
    assert first["requeued"] == 1
    assert second["requeued"] == 0   # nothing left to move

    human = db.query(Observation).filter_by(entity_id=3).one()
    assert human.human_verified is True   # Jack's judgement is never undone
    assert human.verified_by == "human"
