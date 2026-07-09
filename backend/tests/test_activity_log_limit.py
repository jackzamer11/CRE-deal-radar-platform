"""
Activity Log pagination-cap regression test.

Covers the "Activity Log losing old entries" root cause: the list endpoint
used to default/cap at limit=100-200, so once activity volume passed that
count, older rows were silently omitted from the response (data intact in
SQLite, just never fetched). This confirms a default call now returns rows
well past the old ceiling, still correctly ordered newest-first.

In-memory SQLite, dependency-overridden get_db, no live DB or network.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models                 # noqa: F401 — registers core tables on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.models.activity import ActivityLog
from app.main import app

ROW_COUNT = 150


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


def _seed_many(db_session, count=ROW_COUNT):
    """Seed `count` rows with strictly increasing created_at timestamps."""
    base = datetime(2020, 1, 1)
    for i in range(count):
        db_session.add(ActivityLog(
            action_type="NOTE",
            action_taken=f"Entry {i}",
            created_at=base + timedelta(minutes=i),
        ))
    db_session.commit()


def test_default_call_returns_all_rows_past_old_cap(db_session, client):
    """150+ rows seeded — a plain GET with no explicit limit must return all of
    them, since the new default (1000) sits above both the old default (50)
    and the old hard cap (200)."""
    _seed_many(db_session, ROW_COUNT)

    resp = client.get("/api/activity/")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == ROW_COUNT, (
        f"expected all {ROW_COUNT} seeded rows back, got {len(rows)} — "
        "the response is being truncated below the new cap"
    )


def test_default_call_is_ordered_newest_first(db_session, client):
    _seed_many(db_session, ROW_COUNT)

    rows = client.get("/api/activity/").json()
    action_taken = [r["action_taken"] for r in rows]
    expected_newest_first = [f"Entry {i}" for i in reversed(range(ROW_COUNT))]
    assert action_taken == expected_newest_first, (
        "rows must be ordered by created_at descending (newest first)"
    )


def test_explicit_limit_above_1000_is_rejected(client):
    """The hard ceiling is still enforced — le=1000, not unbounded."""
    resp = client.get("/api/activity/", params={"limit": 1001})
    assert resp.status_code == 422
