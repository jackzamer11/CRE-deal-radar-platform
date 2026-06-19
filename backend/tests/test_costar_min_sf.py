"""
CoStar Tenant Locations import — minimum SF Occupied floor (configurable).

Guards Filter 3 in /api/companies/costar-import (companies.py): rows whose
"SF Occupied" falls below settings.TENANT_MIN_OCCUPIED_SF are dropped (counted
in filtered_size); rows at or above it — and rows with blank/missing SF — are
kept.

The floor is no longer a hardcoded constant. It lives in
settings.TENANT_MIN_OCCUPIED_SF (config.py), defaults to 0 (no floor), and is
read live at request time so the env var TENANT_MIN_OCCUPIED_SF can change the
behaviour without a code edit. These tests assert the default-0 behaviour and
that raising the floor reinstates the size filter.
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


def test_default_floor_is_zero():
    """The configurable floor defaults to 0 (no size filter)."""
    assert settings.TENANT_MIN_OCCUPIED_SF == 0


def test_small_row_kept_by_default(client, db_session):
    """With the default floor of 0, a tiny SF Occupied row is still kept."""
    result = _upload_csv(client, [_row("TinyCo", 1)])

    assert result["total_rows"] == 1
    assert result["filtered_size"] == 0
    assert result["inserted"] == 1
    assert db_session.query(Company).filter_by(name="TinyCo").first() is not None


def test_floor_override_drops_below_and_keeps_at(client, db_session, monkeypatch):
    """Raising TENANT_MIN_OCCUPIED_SF reinstates the size filter at the boundary."""
    monkeypatch.setattr(settings, "TENANT_MIN_OCCUPIED_SF", 1500)

    result = _upload_csv(client, [
        _row("DropCo", 1499),
        _row("KeepCo", 1500),
    ])

    assert result["total_rows"] == 2
    assert result["filtered_size"] == 1
    assert result["inserted"] == 1
    assert db_session.query(Company).filter_by(name="DropCo").first() is None
    assert db_session.query(Company).filter_by(name="KeepCo").first() is not None
