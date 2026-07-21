import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401 — registers core tables on Base.metadata
import app.models.observation  # noqa: F401
from app.database import Base, get_db
from app.main import app


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
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_observation_with_null_value_succeeds(client):
    response = client.post(
        "/api/observations/",
        json={
            "entity_type": "company",
            "entity_id": 42,
            "field": "lease_expiration",
            "value": None,
            "confidence": 0.4,
            "source_doc": "lease.pdf",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["value"] is None
    assert body["field"] == "lease_expiration"


def test_verification_correction_creates_new_row_and_supersedes_old(client):
    create_resp = client.post(
        "/api/observations/",
        json={
            "entity_type": "company",
            "entity_id": 7,
            "field": "expiration_date",
            "value": "2025-01-01",
            "confidence": 0.7,
            "source_doc": "lease.pdf",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    old_id = create_resp.json()["id"]

    verify_resp = client.post(
        f"/api/observations/{old_id}/verify",
        json={"value": "2026-01-01"},
    )

    assert verify_resp.status_code == 200, verify_resp.text
    body = verify_resp.json()
    assert body["id"] != old_id
    assert body["human_verified"] is True
    assert body["value"] == "2026-01-01"

    list_resp = client.get("/api/observations/")
    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    assert len(rows) == 2
    old_row = next(row for row in rows if row["id"] == old_id)
    assert old_row["superseded_by_id"] == body["id"]


def test_list_filter_by_human_verified_works(client):
    client.post(
        "/api/observations/",
        json={
            "entity_type": "company",
            "entity_id": 1,
            "field": "expiration_date",
            "value": "2025-01-01",
            "confidence": 0.9,
            "source_doc": "lease.pdf",
        },
    )
    client.post(
        "/api/observations/",
        json={
            "entity_type": "company",
            "entity_id": 2,
            "field": "premises_sqft",
            "value": "10000",
            "confidence": 0.3,
            "source_doc": "lease.pdf",
        },
    )

    verify_resp = client.post(
        "/api/observations/1/verify",
        json={},
    )
    assert verify_resp.status_code == 200, verify_resp.text

    verified_resp = client.get("/api/observations/", params={"human_verified": True})
    unverified_resp = client.get("/api/observations/", params={"human_verified": False})

    assert verified_resp.status_code == 200
    assert unverified_resp.status_code == 200
    assert len(verified_resp.json()) == 1
    assert len(unverified_resp.json()) == 1
