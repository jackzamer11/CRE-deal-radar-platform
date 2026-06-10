"""
Filler-strip broadening + CoStar property-import submarket upsert (Herndon).

Fix 1 — _MARKET_FILLER_PATTERN strips the additional generic "match"/"requirements"
        phrasings ("ideal match", "perfect match", "strong match", "could be a
        match", "is a match for your", "aligns with your requirements", "meets your
        requirements") from tenant-side copy before delivery.

Fix 2 — the CoStar property importer (/api/properties/costar-import) UPSERTS on
        address: a re-import of a row whose submarket now maps to "Herndon" must
        update the existing property's stored submarket rather than skip it, and
        "Herndon" must be present in every submarket validation/enumeration gate.

Offline: _chat mocked for the filler tests; an in-memory SQLite DB + FastAPI
get_db override for the import trace.
"""
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
from app.database import Base, get_db
from app.models.property import Property
from app.api.routes.properties import (
    COSTAR_REQUIRED_COLS, COSTAR_SUBMARKET_MAP, VALID_SUBMARKETS,
)
import app.services.property_outreach_service as svc


# ── Fix 1: broadened tenant-side filler strip ───────────────────────────────────
def _property(submarket="Tysons", sf_avail=5000):
    return {
        "property_id": "TST-001",
        "address": "100 Test Ave, McLean, VA",
        "submarket": submarket,
        "total_sf": 20000,
        "sf_avail": sf_avail,
        "owner_name": "Test Owner LLC",
        "in_place_rent_psf": 35.0,
        "vacancy_pct": 27.0,
        "listed_for_sale": False,
        "asset_class": "office",
        "dominant_score_type": "tenant_match",
    }


def _tenant():
    return {
        "name": "ZZZ Test Co",
        "industry": "Technology",
        "current_submarket": "Tysons",
        "current_sf_occupied": 3500,
        "lease_expiry_months": 12,
        "primary_contact_name": "Jane Smith",
    }


@pytest.mark.parametrize("filler", [
    "This space is an ideal match for your team.",
    "It is a perfect match given your timing.",
    "This is a strong match worth a look.",
    "It could be a match for what you described.",
    "The building is a match for your needs.",
    "This space aligns with your requirements nicely.",
    "It aligns with your requirements going forward.",
    "This option meets your requirements well.",
])
def test_broadened_match_filler_stripped(filler):
    """Each new generic 'match'/'requirements' claim is stripped from the rendered
    tenant-side email; the fact-bearing neighbours survive."""
    def _mock_chat(system, user, **kw):
        return (
            "SUBJECT: Tysons opportunity\n"
            "EMAIL:\n"
            "Hi Jane Smith,\n\n"
            f"With your lease expiring in 12 months, I wanted to flag 5,000 SF in Tysons. {filler} "
            "Happy to walk you through it.\n\n"
            "I'd welcome a brief call at your convenience.\n\n"
            "Thank you,\n\nJack Zamer\n571-205-6228\n"
            "OPENING:\no\nCORE:\nc\nPAIN_PROBE:\np\nCLOSE:\nx"
        )

    with patch.object(svc, "_chat", side_effect=_mock_chat):
        body = svc.generate_property_outreach(
            property_dict=_property(),
            outreach_type="tenant_match",
            direction="tenant_side",
            tenant_dict=_tenant(),
        )["email_body"]
    assert filler not in body, f"generic filler must be stripped: {filler!r}"
    assert "5,000 SF in Tysons" in body, "fact-bearing sentence must survive"
    assert "Happy to walk you through it." in body


def test_match_filler_regex_units():
    """The pattern matches each new phrase directly."""
    for phrase in (
        "ideal match", "perfect match", "strong match", "could be a match",
        "is a match for your", "aligns with your requirements", "meets your requirements",
    ):
        sentence = f"This option {phrase} today."
        assert svc._MARKET_FILLER_PATTERN.search(sentence), f"pattern must catch {phrase!r}"


def test_match_filler_does_not_overstrip_facts():
    """A sentence carrying a concrete fact and none of the banned phrases is kept."""
    sentence = "The 5,000 SF suite was renovated in 2024 and sits two blocks from the Metro."
    assert not svc._MARKET_FILLER_PATTERN.search(sentence)


# ── Fix 2: CoStar property-import submarket upsert + validation chain ────────────
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
    from app.main import app
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


HERNDON_ADDRESS = "200 Herndon Pkwy, Herndon, VA 20170"


def _costar_row(submarket_name: str) -> dict:
    """A complete, importable CoStar property row (passes State/Submarket/Status)."""
    base = {col: "" for col in COSTAR_REQUIRED_COLS}
    base.update({
        "Property Address": HERNDON_ADDRESS,
        "Building Class":   "A",
        "RBA":              "40000",
        "Submarket Name":   submarket_name,
        "City":             "Herndon",
        "State":            "VA",
        "Zip":              "20170",
        "Year Built":       "2005",
        "Percent Leased":   "80",
        "Building Status":  "Existing",
        "For Sale Status":  "No",
        "True Owner Name":  "Herndon Holdings LLC",
    })
    return base


def _import_csv(client, rows: list) -> dict:
    import pandas as pd
    df = pd.DataFrame(rows, columns=COSTAR_REQUIRED_COLS)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    resp = client.post(
        "/api/properties/costar-import",
        files={"file": ("props.csv", buf.getvalue(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_herndon_in_validation_chain():
    """Herndon must be present in every submarket gate the import path touches."""
    assert "Herndon" in VALID_SUBMARKETS
    assert COSTAR_SUBMARKET_MAP.get("herndon") == "Herndon"
    assert COSTAR_SUBMARKET_MAP.get("herndon/dulles") == "Herndon"
    assert COSTAR_SUBMARKET_MAP.get("route 28 corridor north") == "Herndon"


def test_costar_import_inserts_herndon_property(client, db_session):
    """A first import of a Herndon row creates the property with submarket Herndon."""
    result = _import_csv(client, [_costar_row("Herndon")])
    assert result["inserted"] == 1, result
    prop = db_session.query(Property).filter(
        Property.address == HERNDON_ADDRESS
    ).first()
    assert prop is not None and prop.submarket == "Herndon"


def test_costar_reimport_updates_submarket_to_herndon(client, db_session):
    """A property previously stored under 'Reston' is UPDATED (not skipped) to
    'Herndon' when re-imported from a row whose submarket now maps to Herndon."""
    # Seed an existing property at the same address with a stale submarket.
    stale = Property(
        property_id="NVA-OLD", address=HERNDON_ADDRESS, submarket="Reston",
        total_sf=40000, year_built=2005, owner_name="Old Owner",
        in_place_rent_psf=30.0, market_rent_psf=37.84, market_cap_rate=6.5,
        listed_for_sale=False,
    )
    db_session.add(stale)
    db_session.commit()

    # Re-import the same address with the Herndon-mapping submarket.
    result = _import_csv(client, [_costar_row("Herndon")])
    assert result["inserted"] == 0, result
    assert result["updated"] == 1, result

    db_session.expire_all()
    rows = db_session.query(Property).filter(
        Property.address == HERNDON_ADDRESS
    ).all()
    assert len(rows) == 1, "upsert must not create a duplicate"
    assert rows[0].submarket == "Herndon", (
        "re-import must update the stored submarket to Herndon, not leave 'Reston'"
    )
    assert rows[0].property_id == "NVA-OLD", "upsert must update the existing record"


def test_costar_reimport_via_route28_north_updates_to_herndon(client, db_session):
    """The 'Route 28 Corridor North' CoStar submarket also upserts to Herndon."""
    stale = Property(
        property_id="NVA-R28", address=HERNDON_ADDRESS, submarket="Reston",
        total_sf=40000, year_built=2005, owner_name="Old Owner",
        in_place_rent_psf=30.0, market_rent_psf=37.84, market_cap_rate=6.5,
        listed_for_sale=False,
    )
    db_session.add(stale)
    db_session.commit()

    result = _import_csv(client, [_costar_row("Route 28 Corridor North")])
    assert result["updated"] == 1, result
    db_session.expire_all()
    prop = db_session.query(Property).filter(
        Property.address == HERNDON_ADDRESS
    ).first()
    assert prop.submarket == "Herndon"
