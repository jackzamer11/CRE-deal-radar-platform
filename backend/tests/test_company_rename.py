"""A company can be renamed, and the next email does not fork it.

The bug this locks: the Companies page edit form let Jack type a new name, but
its edit-mode save never sent one — there was no endpoint that changed
Company.name — so the scheduled email task's "Development.org" could not
become IDEA.

Contracts:
  1. PATCH /api/companies/{id}/name renames; company_id never changes; every
     surface reads the new name. 422 on blank, 409 on a name another company
     holds (any casing), and a 409 changes nothing.
  2. Generated text that baked the old name in (Intel card title/rationale,
     tenant opportunity thesis/next action/call script) follows the rename.
     Prose a person or an email wrote — contact facts — is left alone.
  3. The old name is remembered: an email that still names the tenant by it
     resolves to the renamed company in both email resolvers and in Intel's.
  4. A company the email resolver created from a domain stays found by that
     domain after a rename. A company created from a NAME claims its contacts'
     work domain on rename, taking it from a holder that has no contact there
     (a name-match guess), never from one that does.

In-memory SQLite only.
"""
import json

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
from app.models.contact import Contact, ContactFact
from app.models.intel import IntelOpportunity
from app.models.opportunity import Opportunity
from app.services.company_rename_service import (
    CompanyRenameConflict, CompanyRenameError, rename_company,
)
from app.services.contact_service import (
    resolve_companies_by_names, resolve_company_for_email,
)
from app.services.requirement_subject import NameResolver


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
    kw.setdefault("industry", "Unknown")
    c = Company(company_id=business_id, name=name, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _contact(db, name, email, company):
    c = Contact(
        name=name, email=email, company_id=company.id if company else None,
        contact_type="tenant", stage="Sent", triaged=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _company_count(db):
    return db.query(Company).count()


# ── 1. The endpoint ──────────────────────────────────────────────────────────

def test_rename_shows_everywhere_the_name_appears(client, db_session):
    company = _company(
        db_session, "Development.org", "CO-652", triaged=True, company_type="tenant",
    )
    zeyad = _contact(db_session, "Zeyad Elmenayer", "zelmenayer@development.org", company)
    db_session.add(ActivityLog(
        company_id=company.id, company_stamp_id=company.id, contact_id=zeyad.id,
        action_type="EMAIL", action_taken="Asked about warehouse space",
    ))
    db_session.commit()

    r = client.patch("/api/companies/CO-652/name", json={"name": "  IDEA "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "IDEA"
    assert body["company_id"] == "CO-652"   # identity never moves

    assert client.get("/api/companies/CO-652").json()["name"] == "IDEA"
    listed = {c["company_id"]: c["name"] for c in client.get("/api/companies/").json()}
    assert listed.get("CO-652") == "IDEA"

    header = client.get(f"/api/contacts/{zeyad.id}").json()
    assert header["company_name"] == "IDEA"

    entries = client.get("/api/activity/").json()
    assert entries and all(e["company_name"] == "IDEA" for e in entries)


def test_blank_name_is_422_and_changes_nothing(client, db_session):
    _company(db_session, "Development.org", "CO-652")
    for blank in ("", "   "):
        r = client.patch("/api/companies/CO-652/name", json={"name": blank})
        assert r.status_code == 422
    assert db_session.query(Company).one().name == "Development.org"


def test_a_name_another_company_holds_is_409_and_changes_nothing(client, db_session):
    _company(db_session, "Development.org", "CO-652")
    _company(db_session, "IDEA", "CO-100")
    r = client.patch("/api/companies/CO-652/name", json={"name": "idea"})
    assert r.status_code == 409
    # The message names the company that holds it.
    assert "CO-100" in r.json()["detail"]
    db_session.expire_all()
    renamed = db_session.query(Company).filter(Company.company_id == "CO-652").one()
    assert renamed.name == "Development.org"
    assert renamed.former_names is None
    assert _company_count(db_session) == 2   # never a merge


def test_unknown_company_is_404(client):
    r = client.patch("/api/companies/CO-999/name", json={"name": "IDEA"})
    assert r.status_code == 404


def test_a_case_only_change_to_its_own_name_is_allowed(db_session):
    c = _company(db_session, "Idea", "CO-652")
    rename_company(db_session, c, "IDEA")
    assert c.name == "IDEA"
    assert c.former_names is None   # same name, nothing to remember


def test_the_service_refuses_blank_and_taken_names(db_session):
    c = _company(db_session, "Development.org", "CO-652")
    _company(db_session, "IDEA", "CO-100")
    with pytest.raises(CompanyRenameError):
        rename_company(db_session, c, " ")
    with pytest.raises(CompanyRenameConflict):
        rename_company(db_session, c, "Idea")


# ── 2. Stored copies of the name ─────────────────────────────────────────────

def test_generated_text_follows_the_rename_but_written_prose_does_not(db_session):
    company = _company(db_session, "Development.org", "CO-652")
    zeyad = _contact(db_session, "Zeyad Elmenayer", "zelmenayer@development.org", company)
    db_session.add_all([
        IntelOpportunity(
            title="Development.org — stated requirement", entity_type="company",
            entity_id=company.id, score=80,
            rationale="Development.org asked for warehouse space.",
        ),
        Opportunity(
            opportunity_id="OPP-001", deal_type="TENANT_DRIVEN",
            opportunity_category="TENANT_REP", company_id=company.id, score=70,
            confidence_level="LOW", priority="HIGH",
            thesis="Development.org lease rolls soon.",
            next_action="Call Development.org", call_script="TENANT CALL — Development.org",
        ),
        ContactFact(
            contact_id=zeyad.id,
            fact_text="Zeyad is Assistant Director at Development.org.",
        ),
    ])
    db_session.commit()

    rename_company(db_session, company, "IDEA")
    db_session.commit()

    card = db_session.query(IntelOpportunity).one()
    assert card.title == "IDEA — stated requirement"
    assert card.rationale == "IDEA asked for warehouse space."
    opp = db_session.query(Opportunity).one()
    assert (opp.thesis, opp.next_action, opp.call_script) == (
        "IDEA lease rolls soon.", "Call IDEA", "TENANT CALL — IDEA",
    )
    # What someone actually said stays as they said it.
    fact = db_session.query(ContactFact).one()
    assert fact.fact_text == "Zeyad is Assistant Director at Development.org."


def test_the_rewrite_never_touches_part_of_a_longer_word(db_session):
    company = _company(db_session, "Acme", "CO-001")
    db_session.add(IntelOpportunity(
        title="Acme card", entity_type="company", entity_id=company.id, score=1,
        rationale="Acme is moving to Acmeville.",
    ))
    db_session.commit()
    rename_company(db_session, company, "Zeta")
    card = db_session.query(IntelOpportunity).one()
    assert card.rationale == "Zeta is moving to Acmeville."


# ── 3. The old name still finds the company ─────────────────────────────────

def test_an_email_naming_the_old_name_lands_on_the_renamed_company(db_session):
    company = _company(db_session, "Development.org", "CO-652")
    rename_company(db_session, company, "IDEA")
    db_session.commit()
    assert json.loads(company.former_names) == ["Development.org"]

    before = _company_count(db_session)
    resolved = resolve_companies_by_names(db_session, ["Development.org", "development.org"])
    assert resolved["Development.org"] == (company, False)
    assert resolved["development.org"] == (company, False)
    assert _company_count(db_session) == before


def test_a_company_that_holds_a_name_now_wins_over_one_that_used_to(db_session):
    old = _company(db_session, "Foo", "CO-001")
    rename_company(db_session, old, "Bar")
    db_session.commit()
    new = _company(db_session, "Foo", "CO-002")

    assert resolve_companies_by_names(db_session, ["Foo"])["Foo"][0].id == new.id
    assert NameResolver(db_session).resolve("Foo") == new.id


def test_renaming_back_drops_the_name_from_the_former_list(db_session):
    c = _company(db_session, "Development.org", "CO-652")
    rename_company(db_session, c, "IDEA")
    rename_company(db_session, c, "Development.org")
    assert json.loads(c.former_names) == ["IDEA"]


def test_intel_resolves_a_fact_that_names_the_old_name(db_session):
    company = _company(db_session, "Development.org", "CO-652")
    rename_company(db_session, company, "IDEA")
    db_session.commit()
    resolver = NameResolver(db_session)
    assert resolver.resolve("Development.org") == company.id
    assert resolver.resolve("IDEA") == company.id


# ── 4. Domains ───────────────────────────────────────────────────────────────

def test_a_resolver_created_company_keeps_its_domain_through_a_rename(db_session):
    company, created = resolve_company_for_email(db_session, "zelmenayer@ideausa.org")
    db_session.commit()
    assert created and company.name == "Ideausa"

    rename_company(db_session, company, "IDEA")
    db_session.commit()

    before = _company_count(db_session)
    again, created_again = resolve_company_for_email(db_session, "someone.else@ideausa.org")
    assert again.id == company.id and not created_again
    assert again.name == "IDEA"
    assert _company_count(db_session) == before


def test_a_name_created_company_claims_its_contacts_domain_from_a_guess(db_session):
    # The live case: "Research & Development Solutions" was handed
    # development.org by the name matcher ("development") with nobody there;
    # the scheduled task then created "Development.org" from the email's
    # wording, with no domain, and filed Zeyad under it.
    guess = _company(
        db_session, "Research & Development Solutions, Inc.", "CO-256",
        email_domain="development.org", website="http://rdsi.com/",
    )
    (named, created), = resolve_companies_by_names(db_session, ["Development.org"]).values()
    db_session.commit()
    assert created and named.email_domain is None
    _contact(db_session, "Zeyad Elmenayer", "zelmenayer@development.org", named)

    rename_company(db_session, named, "IDEA")
    db_session.commit()
    db_session.refresh(guess)

    assert named.email_domain == "development.org"
    assert guess.email_domain is None

    before = _company_count(db_session)
    by_email, made = resolve_company_for_email(db_session, "colleague@development.org")
    assert by_email.id == named.id and not made
    by_name = resolve_companies_by_names(db_session, ["Development.org"])["Development.org"]
    assert by_name == (named, False)
    assert _company_count(db_session) == before


def test_a_domain_holder_with_people_there_keeps_it(db_session):
    holder = _company(db_session, "Real Owner", "CO-001", email_domain="shared.org")
    _contact(db_session, "Pat", "pat@shared.org", holder)
    renamed = _company(db_session, "Shared.org", "CO-002")
    _contact(db_session, "Lee", "lee@shared.org", renamed)

    rename_company(db_session, renamed, "Something Else")
    db_session.commit()
    db_session.refresh(holder)
    assert holder.email_domain == "shared.org"
    assert renamed.email_domain is None


def test_no_domain_is_claimed_when_contacts_disagree_or_use_free_mail(db_session):
    mixed = _company(db_session, "Mixed", "CO-001")
    _contact(db_session, "A", "a@one.org", mixed)
    _contact(db_session, "B", "b@two.org", mixed)
    rename_company(db_session, mixed, "Mixed Renamed")
    assert mixed.email_domain is None

    free = _company(db_session, "Freemail", "CO-002")
    _contact(db_session, "C", "c@gmail.com", free)
    rename_company(db_session, free, "Freemail Renamed")
    assert free.email_domain is None
