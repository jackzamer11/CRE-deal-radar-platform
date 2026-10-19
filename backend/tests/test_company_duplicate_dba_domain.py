"""One email, one tenant, one company — and a duplicate folds back without loss.

The bug this locks: Ann Waller's email about "Global Printing dba More Vang"
created that company by name, then created a second one, "Morevang", for its
tenant contact jhirschorn@morevang.com — the domain-derived name is one word,
the email's is two, and the whole-word matcher saw no match. The deal's
entries, stated requirements and market facts landed on one record; the
contact and the signed lease on the other.

Two contracts:
  1. A name and the same words run together are the same company, in both
     resolvers and in either order — still never a character substring.
  2. merge_companies moves everything off the duplicate (entries, facts,
     intel, contacts, leases), fills only the target's blanks, and deletes it.

In-memory SQLite only.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers every table on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.intel import IntelOpportunity, IntelSignal
from app.models.lease import Lease
from app.models.observation import Observation
from app.services.company_merge_service import CompanyMergeConflict, merge_companies
from app.services.contact_service import (
    _names_match, _normalize_company_name, resolve_companies_by_names,
    resolve_company_for_email,
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _company(db, name, business_id, **kw):
    c = Company(company_id=business_id, name=name, industry="Unknown", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _match(a, b):
    return _names_match(_normalize_company_name(a), _normalize_company_name(b))


# ── 1. Run-together names match ──────────────────────────────────────────────

def test_a_domain_name_matches_the_same_words_run_together():
    assert _match("Morevang", "Global Printing dba More Vang")
    assert _match("Global Printing dba More Vang", "Morevang")
    assert _match("More Vang", "Morevang Inc")


def test_run_together_matching_is_still_whole_words_never_a_substring():
    # "ics" (ics.com) inside "magnet forensics" stays a non-match.
    assert not _match("Ics", "Magnet Forensics")
    # Part of a word is not a word.
    assert not _match("Orevang", "Global Printing dba More Vang")
    assert not _match("Morevan", "Global Printing dba More Vang")


def test_the_email_that_made_the_duplicate_now_makes_one_company(db_session):
    """Deal named first, tenant contact's domain second — the real order."""
    by_name = resolve_companies_by_names(db_session, ["Global Printing dba More Vang"])
    deal_company, created = by_name["Global Printing dba More Vang"]
    assert created

    contact_company, created = resolve_company_for_email(
        db_session, "jhirschorn@morevang.com",
    )
    assert created is False
    assert contact_company.id == deal_company.id
    # The domain is claimed, so the next email from it is a direct hit.
    assert deal_company.email_domain == "morevang.com"
    assert db_session.query(Company).count() == 1


def test_the_reverse_order_also_makes_one_company(db_session):
    contact_company, _ = resolve_company_for_email(db_session, "jhirschorn@morevang.com")
    assert contact_company.name == "Morevang"

    deal_company, created = resolve_companies_by_names(
        db_session, ["Global Printing dba More Vang"],
    )["Global Printing dba More Vang"]
    assert created is False
    assert deal_company.id == contact_company.id
    assert db_session.query(Company).count() == 1


# ── 2. Merging a duplicate keeps everything ──────────────────────────────────

def _the_duplicate_pair(db):
    """The live shape: deal facts on one record, contact and lease on the other."""
    target = _company(db, "Morevang", "CO-608", email_domain="morevang.com",
                      current_address="3660-C Wheeler Avenue")
    source = _company(db, "Global Printing dba More Vang", "CO-607",
                      current_headcount=12, current_address="somewhere else")

    contact = Contact(name="Josh Hirschorn", email="jhirschorn@morevang.com",
                      company_id=target.id)
    db.add(contact)
    db.add(Lease(company_id=target.id, file_name="signed.pdf", is_current=True))
    db.add(Lease(company_id=source.id, file_name="older.pdf", is_current=True))
    db.flush()
    for text in ("Ann sent the addendum", "Josh returned it signed"):
        db.add(ActivityLog(
            company_id=source.id, company_stamp_id=source.id, contact_id=contact.id,
            action_type="EMAIL", action_taken=text,
        ))
    db.add(Observation(entity_type="company", entity_id=source.id,
                       field="req_space_type", value="Warehouse"))
    db.add(Observation(entity_type="contact", entity_id=source.id,
                       field="unrelated", value="a contact with the same id"))
    db.add(IntelSignal(entity_type="company", entity_id=source.id,
                       signal_type="stated_requirement"))
    db.add(IntelOpportunity(
        title="Stated requirement — Global Printing dba More Vang",
        rationale="Global Printing dba More Vang stated: warehouse",
        entity_type="company", entity_id=source.id, score=77.0, status="open",
        dedup_key=f"company:{source.id}:stated_requirement",
    ))
    db.commit()
    return target, source


def test_merge_moves_every_entry_fact_and_intel_row(db_session):
    target, source = _the_duplicate_pair(db_session)
    source_id = source.id

    result = merge_companies(db_session, target, source)
    db_session.commit()

    assert db_session.query(Company).filter(Company.id == source_id).first() is None
    logs = db_session.query(ActivityLog).all()
    assert len(logs) == 2
    assert all(l.company_id == target.id and l.company_stamp_id == target.id for l in logs)

    fact = db_session.query(Observation).filter(Observation.field == "req_space_type").one()
    assert fact.entity_id == target.id
    # A contact-scoped row that merely shares the id is not the company's.
    other = db_session.query(Observation).filter(Observation.field == "unrelated").one()
    assert other.entity_id == source_id

    assert db_session.query(IntelSignal).one().entity_id == target.id
    opp = db_session.query(IntelOpportunity).one()
    assert opp.entity_id == target.id
    assert opp.dedup_key == f"company:{target.id}:stated_requirement"
    assert opp.title == "Stated requirement — Morevang"
    assert "Global Printing" not in opp.rationale

    assert result["moved"]["activity_logs"] == 4   # company + stamp, two entries
    assert result["moved"]["observations"] == 1


def test_merge_keeps_the_targets_values_and_fills_only_its_blanks(db_session):
    target, source = _the_duplicate_pair(db_session)
    result = merge_companies(db_session, target, source)
    db_session.commit()
    db_session.refresh(target)

    assert target.name == "Morevang"
    assert target.current_address == "3660-C Wheeler Avenue"   # target wins
    assert target.current_headcount == 12                      # blank filled
    assert "current_headcount" in result["filled_fields"]
    assert "current_address" not in result["filled_fields"]


def test_merge_leaves_exactly_one_current_lease(db_session):
    target, source = _the_duplicate_pair(db_session)
    merge_companies(db_session, target, source)
    db_session.commit()

    leases = db_session.query(Lease).filter(Lease.company_id == target.id).all()
    assert len(leases) == 2
    assert sum(1 for l in leases if l.is_current) == 1


def test_merge_keeps_the_contact_on_the_target(db_session):
    target, source = _the_duplicate_pair(db_session)
    merge_companies(db_session, target, source)
    db_session.commit()
    assert db_session.query(Contact).one().company_id == target.id


def test_a_company_cannot_merge_into_itself(db_session):
    target, _ = _the_duplicate_pair(db_session)
    with pytest.raises(CompanyMergeConflict):
        merge_companies(db_session, target, target)


def test_the_merge_endpoint(client, db_session):
    target, source = _the_duplicate_pair(db_session)
    r = client.post("/api/companies/CO-608/merge", json={"source_company_id": "CO-607"})
    assert r.status_code == 200, r.text
    assert r.json()["source_name"] == "Global Printing dba More Vang"
    assert client.get("/api/companies/CO-607").status_code == 404

    assert client.post("/api/companies/CO-608/merge",
                       json={"source_company_id": "CO-999"}).status_code == 404
    assert client.post("/api/companies/CO-608/merge",
                       json={"source_company_id": "CO-608"}).status_code == 400
