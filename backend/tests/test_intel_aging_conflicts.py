"""Facts that age, and facts that contradict each other.

A requirement stated in 2026 is not what the tenant wants in 2028, and "need
space by spring" means nothing a year later. When a newer note says something
different, Jack decides — the system never silently picks. What this file locks:

  - requirements count as current for a year, timing for a quarter; older ones
    are listed "re-confirm" and never drive the card on their own
  - a note giving a different SF, budget, term or lease date than the one on
    file waits in Review beside it; the old value stays in use meanwhile
  - restating the same value, or pinning down a date, is not a contradiction
  - "use new" and "keep old" both supersede the loser — nothing is deleted
  - confirming a fact in Review keeps who it is about (it used to drop that)

In-memory SQLite, a stand-in extractor. No live DB, no network.
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
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.activity_intel_service import EXTRACTED_FIELDS, mine_activity_log
from app.services.intel_signal_service import generate_opportunities

TODAY = date.today()


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
def tenant(db):
    co = Company(company_id="CO-001", name="Halverson Dental", industry="Medical")
    db.add(co)
    db.commit()
    maria = Contact(name="Maria Chen", company_id=co.id, contact_type="tenant",
                    stage="Sent", triaged=True)
    db.add(maria)
    db.commit()
    return co, maria


def _note(db, tenant, days_ago, **values):
    co, maria = tenant
    log = ActivityLog(log_date=TODAY - timedelta(days=days_ago), action_type="CALL",
                      action_taken="Call with Maria", channel="call",
                      contact_id=maria.id, company_stamp_id=co.id)
    db.add(log)
    db.commit()

    def extractor(_text):
        out = {f: {"value": None, "confidence": None, "snippet": None} for f in EXTRACTED_FIELDS}
        for field, value in values.items():
            out[field] = {"value": value, "confidence": 0.9, "snippet": f"said {value}"}
        out["requirement_for"] = {"kind": "entry_company", "tenant_name": None}
        return out

    facts = mine_activity_log(log, db, extractor=extractor)
    db.commit()
    return {o.field: o for o in facts}


def _card(db):
    generate_opportunities(db)
    rows = db.query(IntelOpportunity).filter_by(status="open").all()
    return rows[0] if rows else None


# ══ 1. Facts age ══════════════════════════════════════════════════════════════

def test_a_requirement_over_a_year_old_does_not_make_a_card(db, tenant):
    _note(db, tenant, days_ago=400, req_sf_min="3000", req_budget_max_psf="38")
    assert _card(db) is None


def test_older_requirements_are_listed_to_re_confirm(db, tenant):
    _note(db, tenant, days_ago=400, req_must_haves="ground floor")
    _note(db, tenant, days_ago=20, req_sf_min="3000", req_budget_max_psf="38")
    card = _card(db)
    assert "min SF 3000" in card.rationale
    assert "re-confirm: must-haves ground floor" in card.rationale


def test_timing_goes_stale_in_a_quarter(db, tenant):
    _note(db, tenant, days_ago=120, req_timing="by spring")
    _note(db, tenant, days_ago=20, req_sf_min="3000", req_budget_max_psf="38")
    card = _card(db)
    assert "timing by spring" not in card.rationale.split("re-confirm")[0]
    assert "re-confirm: timing by spring" in card.rationale


# ══ 2. Contradictions go to Review ════════════════════════════════════════════

def test_a_different_value_waits_beside_the_old_one(db, tenant, client):
    first = _note(db, tenant, days_ago=60, req_sf_min="3000", req_budget_max_psf="38")
    second = _note(db, tenant, days_ago=5, req_sf_min="4000")
    newer = second["req_sf_min"]
    assert newer.human_verified is False
    assert newer.conflicts_with_id == first["req_sf_min"].id

    # The old value stays in use until Jack picks.
    assert "min SF 3000" in _card(db).rationale

    queue = client.get("/api/observations/", params={"human_verified": False}).json()
    row = next(r for r in queue if r["id"] == newer.id)
    assert row["conflicts_with"]["value"] == "3000"


def test_use_new_supersedes_the_old(db, tenant, client):
    first = _note(db, tenant, days_ago=60, req_sf_min="3000", req_budget_max_psf="38")
    second = _note(db, tenant, days_ago=5, req_sf_min="4000")
    resp = client.post(f"/api/observations/{second['req_sf_min'].id}/resolve-conflict",
                       json={"keep": "new"})
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.get(Observation, first["req_sf_min"].id).superseded_by_id is not None
    assert "min SF 4000" in _card(db).rationale


def test_keep_old_supersedes_the_new(db, tenant, client):
    first = _note(db, tenant, days_ago=60, req_sf_min="3000", req_budget_max_psf="38")
    second = _note(db, tenant, days_ago=5, req_sf_min="4000")
    client.post(f"/api/observations/{second['req_sf_min'].id}/resolve-conflict",
                json={"keep": "old"})
    db.expire_all()
    assert db.get(Observation, second["req_sf_min"].id).superseded_by_id == first["req_sf_min"].id
    assert "min SF 3000" in _card(db).rationale


def test_saying_the_same_thing_again_is_not_a_contradiction(db, tenant):
    _note(db, tenant, days_ago=60, req_sf_min="3000", req_budget_max_psf="$38/SF")
    second = _note(db, tenant, days_ago=5, req_sf_min="3,000 SF", req_budget_max_psf="38")
    assert all(o.conflicts_with_id is None for o in second.values())


def test_pinning_down_a_date_is_not_a_contradiction_but_moving_it_is(db, tenant):
    _note(db, tenant, days_ago=60, expiration_date="2027-08-31")
    pinned = _note(db, tenant, days_ago=30, expiration_date="2027-08-15")
    moved = _note(db, tenant, days_ago=5, expiration_date="2027-11-30")
    assert pinned["expiration_date"].conflicts_with_id is None
    assert moved["expiration_date"].conflicts_with_id is not None


def test_lists_only_ever_add(db, tenant):
    _note(db, tenant, days_ago=60, req_must_haves="ground floor")
    second = _note(db, tenant, days_ago=5, req_must_haves="two exam rooms")
    assert second["req_must_haves"].conflicts_with_id is None


# ══ 3. Confirming keeps who a fact is about ═══════════════════════════════════

def test_confirming_a_fact_keeps_who_it_is_about(db, client):
    obs = Observation(entity_type="company", entity_id=1, field="req_sf_min", value="500",
                      source_doc="activity_log:1", human_verified=False,
                      about="unassigned", assigned_contact_id=None)
    db.add(obs)
    db.commit()
    resp = client.post(f"/api/observations/{obs.id}/verify", json={})
    assert resp.status_code == 200, resp.text
    confirmed = db.get(Observation, resp.json()["id"])
    assert confirmed.about == "unassigned"
