"""The submarket list grows instead of constraining.

What this file locks:

  1. The table is seeded with the platform submarkets and every value a
     company already carries — nothing existing falls off the dropdown.
  2. Add new from the dropdown joins the list permanently.
  3. "sterling" matches an existing "Sterling" — no duplicate, by the API, by
     a company edit, or by a confirmed lease.
  4. A confirmed lease naming a place not on the list adds it (auto_created)
     and assigns it to the company.
  5. An unparseable address leaves the submarket unchanged, and a more
     specific submarket is never traded for its city.
  6. A submarket with no benchmark neither errors nor quotes a benchmark.
  7. /api/companies/ still returns all seven contract fields.

In-memory SQLite, dependency-overridden get_db, a tmp_path leases folder. No
live DB file, no network, no real Anthropic call.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.api.routes import leases as leases_route
from app.config import PLATFORM_SUBMARKETS, SUBMARKET_BENCHMARKS, settings
from app.database import Base, get_db
from app.main import app
from app.models.company import Company
from app.models.submarket import Submarket
from app.services.lease_extraction_service import LEASE_FIELDS, extract_lease
from app.services.submarket_service import (
    apply_derived_submarket, derive_place_name, ensure_seeded,
    get_or_create_submarket,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def leases_dir(tmp_path, monkeypatch):
    folder = tmp_path / "Leases"
    folder.mkdir()
    monkeypatch.setattr(settings, "LEASES_FOLDER", str(folder))
    return folder


def _company(db, name="Acme Corp", business_id="CO-001", **kw):
    c = Company(company_id=business_id, name=name, industry="Technology", **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _field(value, source_text, page=1):
    return {"value": value, "source_text": source_text, "page": page}


def _extraction(address):
    return {
        "lease_commencement_date": _field("March 1, 2022", "Term commences March 1, 2022."),
        "lease_expiration_date":   _field("June 30, 2027", "and expires June 30, 2027."),
        "premises_address":        _field(address, f"the Premises at {address}"),
        "suite_or_unit":           _field("Suite 400", "Suite 400 of the Building"),
        "rentable_square_footage": _field("12,500", "containing 12,500 rentable square feet"),
        "base_rent":               _field("$412,500 per year", "Base Rent of $412,500 per annum"),
        "escalation_terms":        _field("3% annually", "three percent (3%) annually"),
        "renewal_options":         _field("One 5-year option", "one (1) option to extend"),
        "tenant_legal_entity_name": _field("Acme Corp LLC", "ACME CORP LLC"),
    }


def _confirm_lease_at(client, monkeypatch, company, address, **confirm_body):
    raw = _extraction(address)
    monkeypatch.setattr(
        leases_route, "extract_lease_from_pdf",
        lambda pdf_bytes, extractor=None: extract_lease(["p"], lambda pages: raw),
    )
    up = client.post(
        f"/api/leases/companies/{company.id}/upload",
        files={"file": ("Lease.pdf", b"%PDF", "application/pdf")},
    )
    assert up.status_code == 200, up.text
    body = {"accepted_fields": list(LEASE_FIELDS)}
    body.update(confirm_body)
    r = client.post(f"/api/leases/companies/{company.id}/confirm", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _names(db):
    return [s.name for s in db.query(Submarket).all()]


# ══ 1-2. Seeded, and it grows ═════════════════════════════════════════════════

def test_the_list_is_seeded_with_the_platform_values_and_existing_company_values(
    db_session, client,
):
    _company(db_session, "Odd Co", "CO-ODD", current_submarket="Loudoun Tech Corridor")
    rows = client.get("/api/submarkets/").json()
    names = [r["name"] for r in rows]
    # The eight that used to be the whole dropdown are all still there...
    for original in (
        "Arlington (Clarendon)", "Arlington (Rosslyn)", "Arlington (Ballston)",
        "Arlington (Columbia Pike)", "Alexandria (Old Town)", "Tysons", "Reston",
        "Falls Church",
    ):
        assert original in names
    # ...and so is every other platform submarket and every value in use.
    for platform in PLATFORM_SUBMARKETS:
        assert platform in names
    assert "Loudoun Tech Corridor" in names
    assert all(r["auto_created"] is False for r in rows)
    # Seeding twice adds nothing.
    assert ensure_seeded(db_session) == 0
    assert len(_names(db_session)) == len(names)


def test_add_new_joins_the_list_permanently(db_session, client):
    client.get("/api/submarkets/")
    r = client.post("/api/submarkets/", json={"name": "  Sterling  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Sterling"
    assert body["created"] is True
    assert body["auto_created"] is False
    assert "Sterling" in [s["name"] for s in client.get("/api/submarkets/").json()]


def test_a_blank_submarket_name_is_refused(db_session, client):
    assert client.post("/api/submarkets/", json={"name": "   "}).status_code == 422


def test_a_company_can_be_given_a_new_submarket_and_it_joins_the_list(db_session, client):
    company = _company(db_session, "Loudoun Co", "CO-LDN", current_submarket="Reston")
    r = client.patch(f"/api/companies/{company.company_id}/submarket",
                     json={"current_submarket": "Ashburn"})
    assert r.status_code == 200, r.text
    assert r.json()["current_submarket"] == "Ashburn"
    assert "Ashburn" in _names(db_session)


# ══ 3. Case-insensitive matching ══════════════════════════════════════════════

def test_lowercase_sterling_matches_an_existing_sterling(db_session, client, leases_dir, monkeypatch):
    existing, created = get_or_create_submarket(db_session, "Sterling")
    db_session.commit()
    assert created is True

    # By the dropdown's Add new...
    r = client.post("/api/submarkets/", json={"name": "sterling"}).json()
    assert r["created"] is False
    assert r["id"] == existing.id
    # ...by a company edit...
    company = _company(db_session, "Case Co", "CO-CAS")
    body = client.patch(f"/api/companies/{company.company_id}/submarket",
                        json={"current_submarket": "STERLING"}).json()
    assert body["current_submarket"] == "Sterling"
    # ...and by a confirmed lease.
    other = _company(db_session, "Lease Case Co", "CO-LCS")
    result = _confirm_lease_at(client, monkeypatch, other,
                               "21000 Atlantic Blvd, sterling, VA 20166")
    assert result["current_submarket"] == "Sterling"
    assert result["submarket_created"] is False

    db_session.expire_all()
    sterlings = [n for n in _names(db_session) if n.lower() == "sterling"]
    assert sterlings == ["Sterling"]


def test_the_database_itself_refuses_a_case_variant_duplicate(db_session):
    """Belt and braces: the NOCASE unique index backs up the lookup."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(Submarket(name="Sterling"))
    db_session.commit()
    db_session.add(Submarket(name="STERLING"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ══ 4. Derived on lease confirmation ══════════════════════════════════════════

def test_a_submarket_not_in_the_table_is_created_on_confirmation_and_assigned(
    db_session, client, leases_dir, monkeypatch,
):
    client.get("/api/submarkets/")                      # seeded list, no Sterling
    assert "Sterling" not in _names(db_session)
    company = _company(db_session, "Sterling Tenant", "CO-STR", current_submarket="Reston")

    result = _confirm_lease_at(client, monkeypatch, company,
                               "21000 Atlantic Blvd, Suite 400, Sterling, Virginia 20166")
    assert result["current_submarket"] == "Sterling"
    assert result["submarket_created"] is True

    db_session.refresh(company)
    assert company.current_submarket == "Sterling"
    row = db_session.query(Submarket).filter_by(name="Sterling").one()
    assert row.auto_created is True


def test_an_existing_submarket_is_assigned_without_creating_anything(
    db_session, client, leases_dir, monkeypatch,
):
    client.get("/api/submarkets/")
    before = len(_names(db_session))
    company = _company(db_session, "Reston Tenant", "CO-RST", current_submarket="Tysons")
    result = _confirm_lease_at(client, monkeypatch, company,
                               "11911 Freedom Dr, Reston, VA 20190")
    assert result["current_submarket"] == "Reston"
    assert result["submarket_created"] is False
    db_session.expire_all()
    assert len(_names(db_session)) == before


def test_a_manually_typed_address_derives_the_submarket_too(
    db_session, client, leases_dir, monkeypatch,
):
    company = _company(db_session, "Typed Tenant", "CO-TYP")
    result = _confirm_lease_at(
        client, monkeypatch, company, "1750 Tysons Blvd, McLean, VA",
        manual_values={"premises_address": "45 Main St, Leesburg, VA 20175"},
    )
    assert result["current_submarket"] == "Leesburg"


def test_an_unchecked_address_derives_nothing(db_session, client, leases_dir, monkeypatch):
    company = _company(db_session, "Unchecked Tenant", "CO-UNT", current_submarket="Vienna")
    _confirm_lease_at(
        client, monkeypatch, company, "21000 Atlantic Blvd, Sterling, VA 20166",
        accepted_fields=["lease_expiration_date"],
    )
    db_session.refresh(company)
    assert company.current_submarket == "Vienna"
    assert "Sterling" not in _names(db_session)


# ══ 5. Failing safe ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("address", [
    "Suite 400",
    "1750 Tysons Blvd",
    "1750 Tysons Blvd McLean VA",          # no comma before the city
    "Building C, Floor 3",
    "12345, VA 22102",                     # a number where the city belongs
    "",
    "   ",
])
def test_an_unparseable_address_leaves_the_submarket_unchanged(
    db_session, client, leases_dir, monkeypatch, address,
):
    company = _company(db_session, "Unparseable Tenant", "CO-UNP", current_submarket="Tysons")
    client.get("/api/submarkets/")
    before = sorted(_names(db_session))

    result = _confirm_lease_at(
        client, monkeypatch, company, "1750 Tysons Blvd, McLean, VA",
        manual_values={"premises_address": address} if address.strip() else None,
        accepted_fields=[f for f in LEASE_FIELDS if address.strip() or f != "premises_address"],
    )
    db_session.refresh(company)
    assert company.current_submarket == "Tysons"
    assert result["current_submarket"] == "Tysons"
    assert result["submarket_created"] is False
    db_session.expire_all()
    assert sorted(_names(db_session)) == before


def test_derivation_never_raises_and_never_guesses(db_session):
    company = _company(db_session, "Direct Co", "CO-DIR", current_submarket="Reston")
    for junk in (None, "", 12345, "no state here, at all", ",,,", "VA"):
        assert apply_derived_submarket(db_session, company, junk) is None
    assert company.current_submarket == "Reston"


@pytest.mark.parametrize("address,expected", [
    ("1750 Tysons Blvd, Suite 400, McLean, VA 22102", "McLean"),
    ("21000 Atlantic Blvd, Sterling, Virginia 20166", "Sterling"),
    ("21000 Atlantic Blvd, Sterling VA 20166", "Sterling"),
    ("9500 Innovation Dr, Manassas, VA 20110, USA", "Manassas"),
    ("100 Main St, FALLS CHURCH, VA", "Falls Church"),
    ("1750 Tysons Blvd McLean VA", None),
    ("Suite 400, Floor 3", None),
])
def test_the_place_name_parser(address, expected):
    assert derive_place_name(address) == expected


def test_a_more_specific_submarket_is_not_replaced_by_its_city(
    db_session, client, leases_dir, monkeypatch,
):
    company = _company(db_session, "Ballston Tenant", "CO-BAL",
                       current_submarket="Arlington (Ballston)")
    result = _confirm_lease_at(client, monkeypatch, company,
                               "4200 Wilson Blvd, Arlington, VA 22203")
    assert result["current_submarket"] == "Arlington (Ballston)"
    db_session.refresh(company)
    assert company.current_submarket == "Arlington (Ballston)"


# ══ 6. No benchmark is handled as missing ═════════════════════════════════════

def test_a_submarket_with_no_benchmark_does_not_error_and_quotes_nothing(
    db_session, client,
):
    from app.config import is_provisional_submarket
    from app.services.outreach_service import (
        _quotable_submarket_asking, build_call_sheet,
    )

    assert "Sterling" not in SUBMARKET_BENCHMARKS
    company = _company(
        db_session, "Benchless Co", "CO-BEN", current_submarket="Sterling",
        current_headcount=40, current_sf_occupied=8000,
        effective_rent_psf=31.0, starting_rent_psf=27.0,
        lease_expiry_date=date.today() + timedelta(days=240),
    )

    # The company surfaces load.
    assert client.get(f"/api/companies/{company.company_id}").status_code == 200
    rows = client.get("/api/companies/", params={"submarket": "Sterling"}).json()
    assert [r["company_id"] for r in rows] == ["CO-BEN"]
    # The benchmarks the Submarket Intel card reads simply have no entry.
    assert "Sterling" not in client.get("/api/benchmarks/nova").json()["submarkets"]

    # Outreach inputs: nothing to quote, and no error building the sheet.
    assert _quotable_submarket_asking("Sterling") is None
    assert is_provisional_submarket("Sterling") is False
    sheet = build_call_sheet({
        "name": "Benchless Co", "current_submarket": "Sterling",
        "current_headcount": 40, "current_sf_occupied": 8000,
        "effective_rent_psf": 31.0, "starting_rent_psf": 27.0,
        "lease_expiry_months": 8,
    })
    data = sheet["data"].splitlines()
    vacancy = next(l for l in data if l.startswith("Submarket Vacancy %"))
    asking = next(l for l in data if l.startswith("Submarket Asking Rent"))
    assert "not on file" in vacancy and "%" not in vacancy.split(":", 1)[1]
    assert "not on file" in asking and "$" not in asking
    # No other submarket's benchmark leaks in either.
    for bench in SUBMARKET_BENCHMARKS.values():
        assert f"${bench['market_rent_psf']:.2f}" not in asking


# ══ 7. The contract ═══════════════════════════════════════════════════════════

def test_companies_list_still_returns_all_seven_contract_fields(db_session, client):
    _company(
        db_session, name="Contract Sterling Co", business_id="CO-702",
        current_headcount=42, headcount_growth_pct=12.5,
        current_submarket="Sterling", opportunity_score=77.0, priority="HIGH",
        lease_expiry_date=date.today() + timedelta(days=200),
    )
    resp = client.get("/api/companies/")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["company_id"] == "CO-702")
    assert row["company_id"] == "CO-702"
    assert row["priority"] == "HIGH"
    assert row["current_headcount"] == 42            # headcount
    assert row["headcount_growth_pct"] == 12.5       # growth_rate
    assert row["lease_expiry_months"] is not None
    assert row["current_submarket"] == "Sterling"    # submarket
    assert row["opportunity_score"] == 77.0          # score
