"""A sender's domain matches a company website exactly, never as a substring.

fredzamer@cs.com was filed under Magnet Forensics because "cs.com" is a
substring of "https://www.magnetforensics.com/", and ingestion then wrote
"cs.com" onto Magnet Forensics as its email_domain. The website test is now an
exact match on the website's domain (or a subdomain of it), and a free-mail or
personal-provider domain is never written to any company's email_domain.

In-memory SQLite, dependency-overridden get_db. No live database, no network,
no OpenAI or Anthropic calls.
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
from app.services.contact_service import (
    domain_matches_website, resolve_company_for_email,
)

MAGNET_SITE = "https://www.magnetforensics.com/"

PERSONAL_PROVIDERS = [
    "gmail.com", "cs.com", "aol.com", "yahoo.com", "outlook.com",
    "hotmail.com", "icloud.com", "comcast.net", "verizon.net",
]


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
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── The rule itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("domain, website, expected", [
    ("cs.com", MAGNET_SITE, False),                    # the substring that misfiled Fred
    ("netforensics.com", MAGNET_SITE, False),          # a longer substring, still not it
    ("magnetforensics.com", MAGNET_SITE, True),        # exact
    ("mail.magnetforensics.com", MAGNET_SITE, True),   # subdomain of the site
    ("magnetforensics.com", "magnetforensics.com/about", True),   # no scheme
    ("magnetforensics.com.evil.io", MAGNET_SITE, False),
    ("forensics.com", MAGNET_SITE, False),             # the site is not a subdomain of the sender
    ("magnetforensics.com", None, False),
])
def test_domain_matches_website_is_exact(domain, website, expected):
    assert domain_matches_website(domain, website) is expected


# ── Through the resolver and the ingestion endpoint ─────────────────────────

def test_fredzamer_at_cs_com_does_not_match_magnet_forensics(db_session, client):
    magnet = _company(db_session, "Magnet Forensics", "CO-155", website=MAGNET_SITE)

    resp = client.post("/api/activity/from-email", json={
        "from_email": "fredzamer@cs.com",
        "from_name": "fred zamer",
        "direction": "inbound",
        "subject": "Fw: MLS listing 103 channel buoy rd",
        "source_message_id": "<cs-com-fred@mail>",
    })
    assert resp.status_code == 200, resp.text

    entry = db_session.query(ActivityLog).filter(
        ActivityLog.source_message_id == "<cs-com-fred@mail>"
    ).one()
    assert entry.company_stamp_id != magnet.id
    assert entry.company_id != magnet.id
    assert entry.contact.company_id is None

    db_session.refresh(magnet)
    assert magnet.email_domain is None
    # A personal provider is not a company either — no "Cs" record.
    assert db_session.query(Company).count() == 1


def test_a_substring_domain_that_is_not_free_mail_gets_its_own_company(db_session):
    magnet = _company(db_session, "Magnet Forensics", "CO-155", website=MAGNET_SITE)

    company, created = resolve_company_for_email(db_session, "someone@netforensics.com")
    db_session.flush()

    assert created is True
    assert company.id != magnet.id
    assert company.email_domain == "netforensics.com"
    db_session.refresh(magnet)
    assert magnet.email_domain is None


def test_an_exact_website_domain_still_claims_the_company(db_session):
    magnet = _company(db_session, "Magnet Forensics", "CO-155", website=MAGNET_SITE)

    company, created = resolve_company_for_email(
        db_session, "angelo@mail.magnetforensics.com",
    )

    assert created is False
    assert company.id == magnet.id
    assert magnet.email_domain == "mail.magnetforensics.com"


@pytest.mark.parametrize("provider", PERSONAL_PROVIDERS)
def test_a_personal_provider_domain_is_never_written_to_a_company(db_session, provider):
    # A company whose website contains the provider domain as a substring, and
    # one whose name would loosely match the name derived from it.
    _company(db_session, "Lookalike", "CO-1", website=f"https://www.not{provider}/")
    stem = provider.split(".")[0]
    _company(db_session, f"{stem} Holdings", "CO-2")

    company, created = resolve_company_for_email(db_session, f"someone@{provider}")
    db_session.flush()

    assert company is None
    assert created is False
    assert db_session.query(Company).filter(
        Company.email_domain == provider
    ).count() == 0
    assert db_session.query(Company).count() == 2
