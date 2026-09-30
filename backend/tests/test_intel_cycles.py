"""Intel over time — facts filed where their note is, one lease date per
company, and decisions scoped to what was decided.

The Activity Log became per-contact threads, entries now carry the company they
were about (company_stamp_id), and this runs for years across repeating lease
cycles. What this file locks:

  - a note's facts belong to the company its entry is stamped to NOW: one card
    per company however many notes, and moving the entry moves the card
  - facts from an archived entry drive nothing, and their open card retires
  - one lease date per company from the most trustworthy source — confirmed
    lease > Jack's company record > verified note > exact note > hedged note >
    CoStar — with disagreement shown on the card
  - a decision on a lease card covers that lease cycle only
  - a deferral comes back on its date; not before
  - a requirement card Jack decided comes back only when something newer is said
  - "not a tenant" is permanent, and marks the firm a counterparty
  - a firm marked counterparty gets no card
  - a decision made before any of this still holds after the re-filing

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
from app.models.lease import Lease
from app.models.observation import Observation
from app.services.intel_feedback_service import FeedbackError, disposition_opportunity
from app.services.intel_signal_service import generate_opportunities

TODAY = date(2026, 10, 1)


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


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Builders ─────────────────────────────────────────────────────────────────

def _company(db, name, **kw):
    n = db.query(Company).count() + 1
    c = Company(company_id=f"CO-{n:03d}", name=name, industry="Medical", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact(db, name, company=None, contact_type="tenant"):
    c = Contact(name=name, company_id=company.id if company else None,
                contact_type=contact_type, stage="Sent", triaged=True)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _entry(db, *, company=None, contact=None, on=TODAY - timedelta(days=10), archived=False):
    log = ActivityLog(
        log_date=on, action_type="EMAIL", action_taken="Emailed the tenant",
        contact_id=contact.id if contact else None,
        company_stamp_id=company.id if company else None,
        archived=archived,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _fact(db, log, field, value, *, verified=True, by="auto", entity=None):
    """A fact mined from `log`, filed the way the miner filed it at the time:
    under the log itself unless `entity` says otherwise."""
    etype, eid = entity or ("activity_log", log.id)
    o = Observation(
        entity_type=etype, entity_id=eid, field=field, value=value,
        confidence=0.9, source_doc=f"activity_log:{log.id}",
        source_snippet=f"said {value}",
        human_verified=verified, verified_by=by if verified else None,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _open(db):
    return db.query(IntelOpportunity).filter_by(status="open").all()


def _one_open(db):
    rows = _open(db)
    assert len(rows) == 1, [(r.dedup_key, r.title) for r in rows]
    return rows[0]


def _days(n, base=TODAY):
    return (base + timedelta(days=n)).isoformat()


# ══ 1. Facts belong to the company their note is about — now ═════════════════

def test_notes_stamped_to_one_company_make_one_card_for_it(db):
    """Jim Woolwine had four cards, one per note. It is one tenant."""
    at_home = _company(db, "At Home in Alexandria")
    jim = _contact(db, "Jim Woolwine", at_home)
    for days_ago in (70, 50, 20):
        log = _entry(db, company=at_home, contact=jim, on=TODAY - timedelta(days=days_ago))
        _fact(db, log, "req_sf_min", "300")
        _fact(db, log, "req_sf_max", "700")

    generate_opportunities(db, today=TODAY)

    card = _one_open(db)
    assert (card.entity_type, card.entity_id) == ("company", at_home.id)
    assert card.dedup_key == f"company:{at_home.id}:stated_requirement"
    assert card.title == "Stated requirement — At Home in Alexandria"
    assert json.loads(card.signals_json)[0]["contact_name"] == "Jim Woolwine"


def test_moving_the_entry_moves_the_card(db):
    first = _company(db, "Huitt-Zollars")
    right = _company(db, "Cliff Wall Design")
    log = _entry(db, company=first)
    _fact(db, log, "expiration_date", _days(200))

    generate_opportunities(db, today=TODAY)
    old = _one_open(db)
    assert old.entity_id == first.id

    log.company_stamp_id = right.id   # Jack re-stamps the entry
    db.commit()
    generate_opportunities(db, today=TODAY)

    new = _one_open(db)
    assert new.entity_id == right.id
    db.refresh(old)
    assert old.status == "superseded"   # machine tidy-up, not a decision


def test_a_note_with_no_company_keeps_the_entity_it_was_mined_under(db):
    log = _entry(db)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    assert _one_open(db).dedup_key == f"activity_log:{log.id}:lease_expiring"


def test_the_contacts_company_is_the_fallback_when_the_entry_has_no_stamp(db):
    clinic = _company(db, "Vienna Clinic")
    magar = _contact(db, "Dr. Magar", clinic)
    log = _entry(db, contact=magar)   # no stamp, no legacy link
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    assert _one_open(db).entity_id == clinic.id


def test_facts_from_an_archived_entry_drive_nothing_and_their_card_retires(db):
    co = _company(db, "Sabrina's Salon")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(150))
    generate_opportunities(db, today=TODAY)
    card = _one_open(db)

    log.archived = True
    db.commit()
    generate_opportunities(db, today=TODAY)

    assert _open(db) == []
    db.refresh(card)
    assert card.status == "superseded"


# ══ 2. One lease date per company, from the best source ══════════════════════

def _lease_card(db):
    card = _one_open(db)
    return card, json.loads(card.signals_json)[0]


def test_a_confirmed_lease_outranks_everything_a_note_says(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200), by="human")
    db.add(Lease(company_id=co.id, is_current=True, confirmed_at=datetime(2026, 9, 1),
                 expiration_date=TODAY + timedelta(days=260)))
    db.commit()

    generate_opportunities(db, today=TODAY)
    card, sig = _lease_card(db)
    assert sig["expiry_source"] == "your confirmed lease"
    assert sig["expiry_date"] == _days(260)
    # The note disagrees by two months, and the card says so.
    assert sig["conflicts"] == [{"source": f"activity_log:{log.id}", "date": _days(200)}]
    assert "Sources disagree" in card.rationale


def test_an_unconfirmed_lease_upload_does_not_count(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200))
    db.add(Lease(company_id=co.id, is_current=True, confirmed_at=None,
                 expiration_date=TODAY + timedelta(days=260)))
    db.commit()
    generate_opportunities(db, today=TODAY)
    assert _lease_card(db)[1]["expiry_date"] == _days(200)


def test_a_date_jack_entered_on_the_company_outranks_a_note(db):
    co = _company(db, "Momona Tech", lease_expiry_date=TODAY + timedelta(days=240),
                  lease_expiry_source="manual")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(150), by="human")
    generate_opportunities(db, today=TODAY)
    assert _lease_card(db)[1]["expiry_source"] == "company record (entered by you)"


def test_what_the_tenant_said_outranks_costar(db):
    co = _company(db, "Desta Co", lease_expiry_date=TODAY + timedelta(days=900),
                  lease_expiry_source="costar")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(150))
    generate_opportunities(db, today=TODAY)
    card, sig = _lease_card(db)
    assert card.dedup_key.endswith("lease_expiring")
    assert sig["conflicts"] == [{"source": "CoStar", "date": _days(900)}]


def test_a_hedged_note_still_beats_costar_but_only_as_verify_first(db):
    co = _company(db, "Desta Co", lease_expiry_date=TODAY + timedelta(days=100),
                  lease_expiry_source="costar")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", "~June 2027", verified=False)
    generate_opportunities(db, today=TODAY)
    card, sig = _lease_card(db)
    assert card.dedup_key.endswith("expiration_unverified")
    assert sig["expiry_date"] == "2027-06-30"


def test_costar_speaks_only_for_a_company_the_notes_put_on_the_radar(db):
    # A CoStar date alone is the Companies tab's job, not an Intel card.
    _company(db, "CoStar Only", lease_expiry_date=TODAY + timedelta(days=200),
             lease_expiry_source="costar")
    # A company the tenant talked to, with requirements but no stated date,
    # gets its lease card from the CoStar date instead of a requirement card.
    talked = _company(db, "Talked To", lease_expiry_date=TODAY + timedelta(days=200),
                      lease_expiry_source="costar")
    log = _entry(db, company=talked)
    _fact(db, log, "req_sf_min", "3000")
    _fact(db, log, "req_budget_max_psf", "38")

    generate_opportunities(db, today=TODAY)
    card, sig = _lease_card(db)
    assert card.entity_id == talked.id
    assert sig["expiry_source"] == "CoStar"


def test_within_a_rank_the_most_recently_stated_date_wins(db):
    co = _company(db, "Changed Its Mind")
    older = _entry(db, company=co, on=TODAY - timedelta(days=60))
    newer = _entry(db, company=co, on=TODAY - timedelta(days=5))
    _fact(db, older, "expiration_date", _days(150))
    _fact(db, newer, "expiration_date", _days(250))
    generate_opportunities(db, today=TODAY)
    assert _lease_card(db)[1]["expiry_date"] == _days(250)


def test_a_stronger_past_date_means_no_card_even_if_a_note_says_soon(db):
    # Jack's record says the lease already rolled; a stale note does not
    # resurrect it.
    co = _company(db, "Renewed Already", lease_expiry_date=TODAY + timedelta(days=1400),
                  lease_expiry_source="manual")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(150))
    generate_opportunities(db, today=TODAY)
    assert _open(db) == []


# ══ 3. Decisions are scoped to what was decided ══════════════════════════════

def _decide(db, card, disposition, reason=None, **kw):
    return disposition_opportunity(db, card.id, disposition, reason, **kw)[0]


def test_an_accepted_lease_card_stays_closed_for_that_cycle(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "accepted")

    generate_opportunities(db, today=TODAY)
    generate_opportunities(db, today=TODAY + timedelta(days=60))
    assert _open(db) == []


def test_pinning_down_the_same_lease_is_still_the_same_cycle(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", "~August 2027", verified=False)
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "accepted")   # accepted the verify-first card

    # Later the exact day is said, and it verifies itself. Same lease.
    later = _entry(db, company=co, on=TODAY + timedelta(days=5))
    _fact(db, later, "expiration_date", "2027-08-15")
    generate_opportunities(db, today=TODAY + timedelta(days=10))
    assert _open(db) == []


def test_the_next_lease_cycle_is_a_new_card(db):
    """Rejecting Desta's 2027 card must not hide her 2032 renewal."""
    co = _company(db, "Desta Co")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", "2027-03-31")
    generate_opportunities(db, today=TODAY)
    first = _one_open(db)
    assert first.cycle == "2027-03"
    _decide(db, first, "rejected", "timing")

    # Five years on, she renews and the next expiry is said.
    later_today = date(2031, 10, 1)
    renewal = _entry(db, company=co, on=later_today - timedelta(days=3))
    _fact(db, renewal, "expiration_date", "2032-05-31")
    generate_opportunities(db, today=later_today)

    card = _one_open(db)
    assert card.cycle == "2032-05"
    assert card.id != first.id
    db.refresh(first)
    assert first.status == "rejected"   # History keeps the decision as made


def test_a_deferred_card_comes_back_on_its_date_and_not_before(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(250))
    generate_opportunities(db, today=TODAY)
    card = _one_open(db)
    _decide(db, card, "deferred", "timing", resurface_at=TODAY + timedelta(days=21),
            today=TODAY)

    generate_opportunities(db, today=TODAY + timedelta(days=20))
    assert _open(db) == []

    generate_opportunities(db, today=TODAY + timedelta(days=21))
    back = _one_open(db)
    assert back.id != card.id
    db.refresh(card)
    assert card.status == "deferred"
    assert card.resurface_at == TODAY + timedelta(days=21)


def test_a_deferral_with_no_date_comes_back_in_thirty_days(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(250))
    generate_opportunities(db, today=TODAY)
    card = _decide(db, _one_open(db), "deferred", "timing", today=TODAY)
    assert card.resurface_at == TODAY + timedelta(days=30)


def test_a_deferral_must_come_back_in_the_future(db):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(250))
    generate_opportunities(db, today=TODAY)
    with pytest.raises(FeedbackError):
        _decide(db, _one_open(db), "deferred", "timing", resurface_at=TODAY, today=TODAY)


def test_a_decided_requirement_card_comes_back_only_when_something_new_is_said(db):
    co = _company(db, "At Home in Alexandria")
    log = _entry(db, company=co, on=TODAY - timedelta(days=30))
    _fact(db, log, "req_sf_min", "300")
    _fact(db, log, "req_sf_max", "700")
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "rejected", "timing")

    generate_opportunities(db, today=TODAY + timedelta(days=90))
    assert _open(db) == []

    # A month after the decision, the tenant says something new.
    fresh = _entry(db, company=co, on=date.today() + timedelta(days=30))
    _fact(db, fresh, "req_budget_max_psf", "38")
    generate_opportunities(db, today=date.today() + timedelta(days=31))
    assert len(_open(db)) == 1


# ══ 4. Not a tenant — permanent, and the firm is marked ══════════════════════

def test_not_a_tenant_is_permanent_and_marks_the_firm(db):
    broker = _company(db, "Avison Young")
    unknown = _contact(db, "New Broker", broker, contact_type="unconfirmed")
    log = _entry(db, company=broker)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "rejected", "not_a_tenant")

    db.refresh(broker)
    db.refresh(unknown)
    assert broker.company_type == "counterparty"
    assert unknown.contact_type == "counterparty"

    # A new cycle and a new requirement: still nothing, ever.
    later = _entry(db, company=broker, on=TODAY + timedelta(days=700))
    _fact(db, later, "expiration_date", _days(900))
    _fact(db, later, "req_sf_min", "5000")
    _fact(db, later, "req_budget_max_psf", "40")
    generate_opportunities(db, today=TODAY + timedelta(days=720))
    assert _open(db) == []


def test_not_a_tenant_leaves_a_firm_jack_marked_himself_alone(db):
    co = _company(db, "SolaREIT", company_type="tenant")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "rejected", "not_a_tenant")
    db.refresh(co)
    assert co.company_type == "tenant"
    # The firm is not marked, so the rejection itself is what must hold —
    # across a new cycle too.
    later = _entry(db, company=co, on=TODAY + timedelta(days=700))
    _fact(db, later, "expiration_date", _days(900))
    generate_opportunities(db, today=TODAY + timedelta(days=720))
    assert _open(db) == []


def test_not_a_tenant_holds_for_a_note_with_no_company(db):
    # No firm to mark, so only the rejection stands between this entity and a
    # card of a different kind.
    log = _entry(db)
    _fact(db, log, "req_sf_min", "3000")
    _fact(db, log, "req_budget_max_psf", "40")
    generate_opportunities(db, today=TODAY)
    _decide(db, _one_open(db), "rejected", "not_a_tenant")
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    assert _open(db) == []


def test_not_a_tenant_cannot_be_a_deferral(db):
    co = _company(db, "Someone")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    with pytest.raises(FeedbackError):
        _decide(db, _one_open(db), "deferred", "not_a_tenant", today=TODAY)


def test_a_firm_marked_counterparty_gets_no_card(db):
    co = _company(db, "Simpson Properties", company_type="counterparty")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", _days(200))
    _fact(db, log, "req_sf_min", "3000")
    _fact(db, log, "req_budget_max_psf", "38")
    generate_opportunities(db, today=TODAY)
    assert _open(db) == []


def test_a_firm_whose_people_are_all_counterparties_gets_no_card(db):
    """Mike Shuler confirmed as a broker makes Avison Young a brokerage, even
    though "whole firm" was never clicked."""
    firm = _company(db, "Avison Young")
    mike = _contact(db, "Mike Shuler", firm, contact_type="counterparty")
    log = _entry(db, company=firm, contact=mike)
    _fact(db, log, "req_sf_min", "3000")
    _fact(db, log, "req_budget_max_psf", "38")
    generate_opportunities(db, today=TODAY)
    assert _open(db) == []


def test_one_unconfirmed_person_keeps_the_firm_in_play(db):
    firm = _company(db, "Mixed Firm")
    _contact(db, "Broker", firm, contact_type="counterparty")
    other = _contact(db, "Unknown", firm, contact_type="unconfirmed")
    log = _entry(db, company=firm, contact=other)
    _fact(db, log, "req_sf_min", "3000")
    _fact(db, log, "req_budget_max_psf", "38")
    generate_opportunities(db, today=TODAY)
    assert len(_open(db)) == 1


def test_a_firm_jack_marked_tenant_stays_one_whoever_works_there(db):
    firm = _company(db, "SolaREIT", company_type="tenant")
    laura = _contact(db, "Laura", firm, contact_type="counterparty")
    log = _entry(db, company=firm, contact=laura)
    _fact(db, log, "expiration_date", _days(200))
    generate_opportunities(db, today=TODAY)
    assert len(_open(db)) == 1


def test_a_card_dated_from_the_record_names_the_person_on_the_newest_note(db):
    co = _company(db, "Momona Tech", lease_expiry_date=TODAY + timedelta(days=200),
                  lease_expiry_source="manual")
    old = _contact(db, "Old Contact", co)
    desta = _contact(db, "Desta", co)
    first = _entry(db, company=co, contact=old, on=TODAY - timedelta(days=90))
    latest = _entry(db, company=co, contact=desta, on=TODAY - timedelta(days=5))
    _fact(db, first, "req_sf_min", "1500")
    _fact(db, latest, "req_budget_max_psf", "34")
    generate_opportunities(db, today=TODAY)
    card, sig = _lease_card(db)
    assert sig["expiry_source"] == "company record (entered by you)"
    assert sig["contact_name"] == "Desta"
    assert "Contact: Desta." in card.rationale


# ══ 5. Decisions made before any of this ═════════════════════════════════════

def test_a_decision_under_the_old_key_still_holds_after_re_filing(db):
    """Hoda's card was accepted as "activity_log:188:lease_expiring" in July.
    Re-filing its fact under her company changes the key; the decision about
    that lease must survive the move."""
    co = _company(db, "Hoda Home Health")
    log = _entry(db)                     # no company when the card was accepted
    fact = _fact(db, log, "expiration_date", "2027-03-31")
    legacy = IntelOpportunity(
        title="Lease expiring — Hoda Murad", entity_type="activity_log",
        entity_id=log.id, score=120, rationale="…", status="accepted",
        dedup_key=f"activity_log:{log.id}:lease_expiring",
        signals_json=json.dumps([{
            "signal_type": "lease_expiring", "value": "2027-03-31",
            "evidence_observation_id": fact.id,
        }]),
    )
    db.add(legacy)
    db.commit()
    db.add(IntelFeedback(opportunity_id=legacy.id, disposition="accepted"))
    log.company_stamp_id = co.id         # the entry now sits on her company
    db.commit()

    generate_opportunities(db, today=TODAY)
    assert _open(db) == []


# ══ 6. Through the API ═══════════════════════════════════════════════════════

def test_deferring_through_the_api_records_the_return_date(db, client):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", (date.today() + timedelta(days=250)).isoformat())
    generate_opportunities(db)
    card = _one_open(db)

    back_on = (date.today() + timedelta(days=45)).isoformat()
    resp = client.post(
        f"/api/intel/opportunities/{card.id}/disposition",
        json={"disposition": "deferred", "reason_category": "timing", "resurface_at": back_on},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["opportunity"]["resurface_at"] == back_on

    history = client.get("/api/intel/history").json()
    assert history[0]["resurface_at"] == back_on
    assert history[0]["cycle"] == card.cycle


def test_the_api_refuses_an_unknown_reason(db, client):
    co = _company(db, "Halverson Dental")
    log = _entry(db, company=co)
    _fact(db, log, "expiration_date", (date.today() + timedelta(days=250)).isoformat())
    generate_opportunities(db)
    resp = client.post(
        f"/api/intel/opportunities/{_one_open(db).id}/disposition",
        json={"disposition": "rejected", "reason_category": "vibes"},
    )
    assert resp.status_code == 400
