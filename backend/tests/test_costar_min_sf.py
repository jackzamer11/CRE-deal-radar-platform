"""
CoStar Tenant Locations import — minimum SF Occupied threshold.

Guards the size filter in /api/companies/costar-import (companies.py, Filter 3):
rows whose "SF Occupied" falls below MIN_SF_OCCUPIED are dropped (counted in
filtered_size), rows at or above it are kept and inserted.

The threshold lives in the named constant MIN_SF_OCCUPIED (currently 1,500).
These boundary tests assert behaviour at 1,499 (dropped) vs 1,500 (kept) and
are pinned to the constant so they track future threshold changes.
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers core tables on Base.metadata
from app.database import Base, get_db
from app.api.routes.companies import COSTAR_TENANT_COLS, MIN_SF_OCCUPIED
from app.models.company import Company


# ── In-memory DB fixture ───────────────────────────────────────────────────────
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
    """A complete, importable CoStar row (passes State + Submarket filters)."""
    base = {col: "" for col in COSTAR_TENANT_COLS}
    base.update({
        "Tenant Name": tenant_name,
        "Address":     f"{tenant_name} Plaza, Tysons, VA",
        "State":       "VA",
        "Submarket":   "Tysons",
        "SF Occupied": str(sf_occupied),
        "Industry":    "Technology",
        "Employees":   "20",
    })
    return base


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


def test_constant_is_1500():
    """The named threshold constant must be 1,500 (the new minimum)."""
    assert MIN_SF_OCCUPIED == 1500


def test_row_below_threshold_is_dropped(client, db_session):
    """SF Occupied = 1,499 → just under MIN_SF_OCCUPIED → filtered out."""
    result = _upload_csv(client, [_row("BelowCo", MIN_SF_OCCUPIED - 1)])

    assert result["total_rows"] == 1
    assert result["filtered_size"] == 1
    assert result["inserted"] == 0
    assert db_session.query(Company).filter_by(name="BelowCo").first() is None


def test_row_at_threshold_is_kept(client, db_session):
    """SF Occupied = 1,500 → exactly MIN_SF_OCCUPIED → kept and inserted."""
    result = _upload_csv(client, [_row("AtCo", MIN_SF_OCCUPIED)])

    assert result["total_rows"] == 1
    assert result["filtered_size"] == 0
    assert result["inserted"] == 1
    assert db_session.query(Company).filter_by(name="AtCo").first() is not None


def test_boundary_split_in_one_import(client, db_session):
    """In a single import the 1,499 row drops and the 1,500 row survives."""
    result = _upload_csv(client, [
        _row("DropCo", MIN_SF_OCCUPIED - 1),
        _row("KeepCo", MIN_SF_OCCUPIED),
    ])

    assert result["total_rows"] == 2
    assert result["filtered_size"] == 1
    assert result["inserted"] == 1
    assert db_session.query(Company).filter_by(name="DropCo").first() is None
    assert db_session.query(Company).filter_by(name="KeepCo").first() is not None
