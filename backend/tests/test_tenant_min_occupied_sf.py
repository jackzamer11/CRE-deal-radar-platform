"""
Configurable "SF Occupied" floor on the CoStar Tenant Locations import.

Covers settings.TENANT_MIN_OCCUPIED_SF wired into Filter 3 of
/api/companies/costar-import (companies.py). Feeds an in-memory CSV with four
representative rows — 1,000 / 2,499 / blank / 5,000 SF Occupied — and asserts:

  * default floor (0): every row imports, including the blank-SF row;
  * floor raised to 2,500: the 1,000 and 2,499 rows drop (filtered_size),
    while the 5,000 row AND the blank-SF row survive (blank always passes);
  * re-running the import is idempotent (rows UPDATE, never duplicate).

No live DB, network, or CoStar — a SQLite :memory: engine and an in-memory CSV.
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
from app.config import settings
from app.database import Base, get_db
from app.api.routes.companies import COSTAR_TENANT_COLS
from app.models.company import Company


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


def _row(tenant_name: str, sf_occupied) -> dict:
    """A complete CoStar row that clears the State + Submarket filters.

    Pass sf_occupied="" to model a blank/missing SF Occupied cell.
    """
    base = {col: "" for col in COSTAR_TENANT_COLS}
    base.update({
        "Tenant Name": tenant_name,
        "Address":     f"{tenant_name} Plaza, Tysons, VA",
        "State":       "VA",
        "Submarket":   "Tysons",
        "SF Occupied": "" if sf_occupied == "" else str(sf_occupied),
        "Industry":    "Technology",
        "Employees":   "20",
    })
    return base


def _four_rows() -> list:
    return [
        _row("SmallCo", 1000),
        _row("EdgeCo", 2499),
        _row("BlankCo", ""),     # blank / missing SF Occupied
        _row("BigCo", 5000),
    ]


def _upload_csv(client, rows: list) -> dict:
    import pandas as pd

    df = pd.DataFrame(rows, columns=COSTAR_TENANT_COLS)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    resp = client.post(
        "/api/companies/costar-import",
        files={"file": ("tenants.csv", buf.getvalue(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_default_floor_imports_all_rows_including_blank(client, db_session):
    """Default floor (0): 1,000 / 2,499 / blank / 5,000 all import, no 500."""
    assert settings.TENANT_MIN_OCCUPIED_SF == 0

    result = _upload_csv(client, _four_rows())

    assert result["total_rows"] == 4
    assert result["filtered_size"] == 0
    assert result["inserted"] == 4
    for name in ("SmallCo", "EdgeCo", "BlankCo", "BigCo"):
        assert db_session.query(Company).filter_by(name=name).first() is not None

    # Blank SF Occupied is stored as NULL, never a crash or a zero default.
    blank = db_session.query(Company).filter_by(name="BlankCo").first()
    assert blank.current_sf_occupied is None


def test_floor_2500_removes_small_rows_blank_still_passes(client, db_session, monkeypatch):
    """Floor = 2,500: drop 1,000 & 2,499; keep 5,000 and the blank-SF row."""
    monkeypatch.setattr(settings, "TENANT_MIN_OCCUPIED_SF", 2500)

    result = _upload_csv(client, _four_rows())

    assert result["total_rows"] == 4
    assert result["filtered_size"] == 2          # SmallCo (1,000) + EdgeCo (2,499)
    assert result["inserted"] == 2               # BigCo (5,000) + BlankCo (blank)

    assert db_session.query(Company).filter_by(name="SmallCo").first() is None
    assert db_session.query(Company).filter_by(name="EdgeCo").first() is None
    assert db_session.query(Company).filter_by(name="BigCo").first() is not None
    # Blank SF Occupied passes the floor — never filtered.
    assert db_session.query(Company).filter_by(name="BlankCo").first() is not None


def test_reimport_is_idempotent(client, db_session):
    """Re-running the same import UPDATES rows — it never duplicates them."""
    first = _upload_csv(client, _four_rows())
    assert first["inserted"] == 4
    assert first["updated"] == 0

    second = _upload_csv(client, _four_rows())
    assert second["inserted"] == 0
    assert second["updated"] == 4

    assert db_session.query(Company).count() == 4
