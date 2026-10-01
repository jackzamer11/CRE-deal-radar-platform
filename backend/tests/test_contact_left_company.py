"""A contact leaves their company.

Maria worked at Halverson Dental until March 2030, then moved to Bright Smiles.
Her thread stays whole; Halverson keeps exactly the years she worked there, and
everything she said after she left belongs to wherever she went. What this file
locks:

  - entries on or after the day she left move off the old company — to the new
    one when given, otherwise to none — and earlier entries stay put
  - roundup entries about a deal are not moved: they are about the deal's company
  - the contact records where she was and when she left, and now works at the
    new company
  - Intel follows: her old notes still count for Halverson, her new ones do not
  - leaving needs a company to leave, and cannot "leave" to the same one

In-memory SQLite. No live DB, no network.
"""
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
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelOpportunity
from app.models.observation import Observation
from app.services.intel_signal_service import generate_opportunities

LEFT = date(2030, 3, 1)


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
def maria(db):
    old = Company(company_id="CO-001", name="Halverson Dental", industry="Medical")
    new = Company(company_id="CO-002", name="Bright Smiles", industry="Medical")
    db.add_all([old, new])
    db.commit()
    person = Contact(name="Maria Chen", company_id=old.id, contact_type="tenant",
                     stage="Sent", triaged=True)
    db.add(person)
    db.commit()

    def entry(on, **kw):
        log = ActivityLog(log_date=on, action_type="EMAIL", action_taken="note",
                          contact_id=person.id, company_stamp_id=old.id, **kw)
        db.add(log)
        db.commit()
        return log

    before = entry(date(2029, 6, 1))
    after = entry(date(2030, 5, 1))
    roundup = entry(date(2030, 6, 1), deal_sourced=True)
    return person, old, new, before, after, roundup


def test_entries_after_she_left_move_to_where_she_went(db, client, maria):
    person, old, new, before, after, roundup = maria
    resp = client.post(f"/api/contacts/{person.id}/left-company",
                       json={"left_on": LEFT.isoformat(), "new_company_id": new.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entries_moved"] == 1
    assert body["contact"]["former_company_name"] == "Halverson Dental"
    assert body["contact"]["left_company_on"] == LEFT.isoformat()
    assert body["contact"]["company_id"] == new.id

    db.expire_all()
    assert db.get(ActivityLog, before.id).company_stamp_id == old.id
    assert db.get(ActivityLog, after.id).company_stamp_id == new.id
    assert db.get(ActivityLog, roundup.id).company_stamp_id == old.id   # about the deal
    # Her thread is whole: nothing left her.
    assert {l.contact_id for l in db.query(ActivityLog).all()} == {person.id}


def test_with_nowhere_known_the_later_entries_belong_to_no_company(db, client, maria):
    person, old, new, before, after, roundup = maria
    client.post(f"/api/contacts/{person.id}/left-company", json={"left_on": LEFT.isoformat()})
    db.expire_all()
    assert db.get(ActivityLog, after.id).company_stamp_id is None
    assert db.get(Contact, person.id).company_id is None


def test_intel_follows_the_move(db, client, maria):
    person, old, new, before, after, roundup = maria
    for log, value in ((before, "3000"), (after, "1200")):
        for field, v in (("req_sf_min", value), ("req_budget_max_psf", "38")):
            db.add(Observation(entity_type="company", entity_id=old.id, field=field, value=v,
                               source_doc=f"activity_log:{log.id}", human_verified=True,
                               verified_by="auto"))
    db.commit()
    client.post(f"/api/contacts/{person.id}/left-company",
                json={"left_on": LEFT.isoformat(), "new_company_id": new.id})
    generate_opportunities(db, today=date(2030, 6, 15))
    cards = {o.entity_id: o for o in db.query(IntelOpportunity).filter_by(status="open").all()}
    assert "min SF 1200" in cards[new.id].rationale
    assert old.id not in cards or "min SF 1200" not in cards[old.id].rationale


def test_leaving_needs_a_company_and_a_different_one(db, client, maria):
    person, old, new, *_ = maria
    same = client.post(f"/api/contacts/{person.id}/left-company",
                       json={"left_on": LEFT.isoformat(), "new_company_id": old.id})
    assert same.status_code == 400
    client.post(f"/api/contacts/{person.id}/left-company", json={"left_on": LEFT.isoformat()})
    again = client.post(f"/api/contacts/{person.id}/left-company",
                        json={"left_on": LEFT.isoformat()})
    assert again.status_code == 400   # no company left to leave
