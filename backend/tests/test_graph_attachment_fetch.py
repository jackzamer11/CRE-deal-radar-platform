"""Fetching attachment bytes from Microsoft Graph and storing them on an entry.

The Outlook MCP connector can find an email and list what came with it, but it
returns a PDF as extracted text and a JPEG as a rendered picture — never the
original file. Graph's /$value is the only route to the real bytes, so
POST /api/activity/attachments/fetch-from-graph exists.

What these lock:

  - a fetched attachment REPAIRS the row /from-email already wrote
  - the size ceiling is applied from Graph's declared size, without downloading
  - an inline image is skipped, not stored
  - one attachment that fails to download does not cost the others
  - no activity entry is created, modified or deleted by a fetch
  - the "#d2" suffix a split entry carries is stripped before lookup
  - a missing credential is a clear 503, not a crash

Graph is stubbed throughout by monkeypatching `graph_client`'s own bindings.
Nothing here makes a network call, and no real credential is needed.
"""
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 - registers every table
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_ingest import ActivityAttachment
from app.services import attachment_storage, graph_client


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


@pytest.fixture
def docs_folder(tmp_path, monkeypatch):
    folder = tmp_path / "attachments"
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(folder), raising=False,
    )
    assert attachment_storage.documents_folder() == str(folder)
    return folder


@pytest.fixture
def graph_configured(monkeypatch):
    """All four settings present, so is_configured() passes. No real values."""
    for key, value in (
        ("GRAPH_CLIENT_ID", "test-client"),
        ("GRAPH_TENANT_ID", "test-tenant"),
        ("GRAPH_CLIENT_SECRET", "test-secret"),
        ("GRAPH_MAILBOX", "jack@example.com"),
    ):
        monkeypatch.setattr(graph_client.settings, key, value, raising=False)
    graph_client.reset_token_cache()
    return True


@pytest.fixture
def entry(db_session):
    company = Company(company_id="CAV-001", name="Collaborative AV", industry="Tech")
    db_session.add(company)
    db_session.flush()
    contact = Contact(name="Dana Reid", email="dana@collaborative-av.com",
                      company_id=company.id)
    db_session.add(contact)
    db_session.flush()
    log = ActivityLog(
        log_date=date(2026, 4, 2),
        action_type="Email",
        action_taken="Dana sent the floor plan.",
        contact_id=contact.id,
        source_message_id="<msg-graph-1@mail>",
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _stub_graph(monkeypatch, items, blobs, *, graph_id="GRAPH-ID-1", calls=None):
    """Point graph_client at canned metadata and bytes."""
    def fake_list(message_id):
        if calls is not None:
            calls.append(message_id)
        return graph_id, items

    def fake_fetch(gid, attachment_id):
        value = blobs[attachment_id]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(graph_client, "list_attachments", fake_list)
    monkeypatch.setattr(graph_client, "fetch_attachment_bytes", fake_fetch)


def _item(aid, name, size, *, inline=False, odata="#microsoft.graph.fileAttachment"):
    return {
        "id": aid, "name": name, "size": size, "inline": inline,
        "content_type": "application/pdf", "odata_type": odata,
    }


def _fetch(client, entry_id, message_id, **kw):
    payload = {"entry_id": entry_id, "message_id": message_id}
    payload.update(kw)
    response = client.post("/api/activity/attachments/fetch-from-graph", json=payload)
    return response


# ── The case the whole thing exists for ───────────────────────────────────────

def test_a_fetched_attachment_repairs_the_row_from_email_wrote(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """The row exists with no file; the fetch fills it in rather than adding one."""
    placeholder = ActivityAttachment(
        activity_log_id=entry.id,
        file_name="Floor Plan.pdf",
        stored_year=2026,
        stored_path=None,
        oversize=False,
        description="Suite 400 test fit",
        saved_date=date(2026, 4, 2),
    )
    db_session.add(placeholder)
    db_session.commit()
    placeholder_id = placeholder.id

    _stub_graph(
        monkeypatch,
        [_item("att-1", "Floor Plan.pdf", 13)],
        {"att-1": b"%PDF-1.4 real"},
    )

    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()

    assert body["attachments_found"] == 1
    assert body["stored"] == 1
    assert body["repaired"] == 1
    assert body["inserted"] == 0

    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    assert rows[0].id == placeholder_id
    db_session.refresh(rows[0])
    assert rows[0].stored_path is not None
    assert rows[0].description == "Suite 400 test fit"

    stored = attachment_storage.resolve_stored_path(rows[0].stored_path)
    assert open(stored, "rb").read() == b"%PDF-1.4 real"

    # And it opens from the entry.
    assert client.get(f"/api/activity/attachments/{placeholder_id}/file").content == b"%PDF-1.4 real"


def test_a_fetch_inserts_when_no_row_is_waiting(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    _stub_graph(monkeypatch, [_item("att-1", "New.pdf", 5)], {"att-1": b"bytes"})

    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()

    assert body["inserted"] == 1 and body["repaired"] == 0
    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1 and rows[0].file_name == "New.pdf"


def test_fetching_twice_leaves_exactly_one_row(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """A re-run of the backfill must not double anything."""
    _stub_graph(monkeypatch, [_item("att-1", "Plan.pdf", 3)], {"att-1": b"one"})
    _fetch(client, entry.id, "<msg-graph-1@mail>")
    _fetch(client, entry.id, "<msg-graph-1@mail>")

    assert db_session.query(ActivityAttachment).count() == 1
    assert len(list((docs_folder / "2026").iterdir())) == 1


# ── The rules the ordinary path already has ───────────────────────────────────

def test_an_oversize_attachment_is_recorded_without_downloading_it(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """Graph's declared size is enough — pulling 80MB to discard it is waste."""
    monkeypatch.setattr(
        attachment_storage.settings, "MAX_ATTACHMENT_BYTES", 100, raising=False,
    )
    fetched = []

    def fake_fetch(gid, aid):
        fetched.append(aid)
        return b"x" * 500

    _stub_graph(monkeypatch, [_item("att-1", "Huge.mp4", 500)], {})
    monkeypatch.setattr(graph_client, "fetch_attachment_bytes", fake_fetch)

    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()

    assert body["oversize"] == 1
    assert body["stored"] == 0
    # The bytes were never pulled.
    assert fetched == []

    row = db_session.query(ActivityAttachment).one()
    assert row.oversize is True
    assert row.stored_path is None

    # Nothing was written to disk.
    written = []
    for _root, _dirs, files in os.walk(str(docs_folder)):
        written.extend(files)
    assert written == []


def test_an_inline_image_is_skipped(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    fetched = []

    def fake_fetch(gid, aid):
        fetched.append(aid)
        return b"\x89PNG"

    _stub_graph(monkeypatch, [_item("att-1", "signature.png", 40, inline=True)], {})
    monkeypatch.setattr(graph_client, "fetch_attachment_bytes", fake_fetch)

    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()

    assert body["inline_skipped"] == 1
    assert body["stored"] == 0
    assert fetched == []
    assert db_session.query(ActivityAttachment).count() == 0


def test_one_failed_download_does_not_cost_the_others(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """Three attachments, one broken: the other two still land."""
    _stub_graph(
        monkeypatch,
        [
            _item("att-1", "Good One.pdf", 4),
            _item("att-2", "Broken.pdf", 4),
            _item("att-3", "Good Two.pdf", 4),
        ],
        {
            "att-1": b"aaaa",
            "att-2": graph_client.GraphError("Graph returned 500", 502),
            "att-3": b"cccc",
        },
    )

    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()

    assert body["stored"] == 2
    assert body["failed"] == 1
    names = sorted(r.file_name for r in db_session.query(ActivityAttachment).all())
    assert names == ["Good One.pdf", "Good Two.pdf"]

    broken = [a for a in body["attachments"] if a["file_name"] == "Broken.pdf"][0]
    assert broken["status"] == "fetch_failed"
    assert broken["stored"] is False


def test_a_reference_attachment_has_no_file_to_fetch(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """A OneDrive link or forwarded mail is not a file; it must not 500."""
    _stub_graph(
        monkeypatch,
        [_item("att-1", "Shared doc", 0, odata="#microsoft.graph.referenceAttachment")],
        {},
    )
    body = _fetch(client, entry.id, "<msg-graph-1@mail>").json()
    assert body["failed"] == 1
    assert body["attachments"][0]["status"] == "not_a_file"


# ── Entries are never touched ─────────────────────────────────────────────────

def test_a_fetch_never_creates_modifies_or_deletes_an_entry(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    before = {
        "count": db_session.query(ActivityLog).count(),
        "action": entry.action_taken,
        "date": entry.log_date,
        "contact": entry.contact_id,
    }
    _stub_graph(monkeypatch, [_item("att-1", "Plan.pdf", 3)], {"att-1": b"abc"})
    _fetch(client, entry.id, "<msg-graph-1@mail>")

    db_session.refresh(entry)
    assert db_session.query(ActivityLog).count() == before["count"]
    assert entry.action_taken == before["action"]
    assert entry.log_date == before["date"]
    assert entry.contact_id == before["contact"]


# ── Lookup and configuration ──────────────────────────────────────────────────

def test_the_split_entry_suffix_is_stripped_before_lookup(
    db_session, client, entry, docs_folder, graph_configured, monkeypatch,
):
    """A multi-deal entry stores "<id>#d2"; the provider never saw the suffix."""
    assert graph_client.normalize_message_id("<abc@host>#d3") == "<abc@host>"
    assert graph_client.normalize_message_id("<abc@host>") == "<abc@host>"

    calls = []
    _stub_graph(monkeypatch, [_item("att-1", "P.pdf", 3)], {"att-1": b"abc"}, calls=calls)
    _fetch(client, entry.id, "<msg-graph-1@mail>#d2")
    # The route hands the raw value to list_attachments, which normalizes it.
    assert calls == ["<msg-graph-1@mail>#d2"]


def test_an_unknown_entry_is_a_404(client, docs_folder, graph_configured):
    response = _fetch(client, 999999, "<msg-graph-1@mail>")
    assert response.status_code == 404


def test_missing_credentials_are_a_clear_503(client, entry, docs_folder, monkeypatch):
    """Not a crash, and it names what is missing."""
    for key in ("GRAPH_CLIENT_ID", "GRAPH_TENANT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_MAILBOX"):
        monkeypatch.setattr(graph_client.settings, key, None, raising=False)

    response = _fetch(client, entry.id, "<msg-graph-1@mail>")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "GRAPH_CLIENT_SECRET" in detail
    assert ".env" in detail


def test_a_message_missing_from_the_mailbox_is_reported(
    client, entry, docs_folder, graph_configured, monkeypatch,
):
    def fake_list(message_id):
        raise graph_client.GraphError("No message in the mailbox has id x.", 404)

    monkeypatch.setattr(graph_client, "list_attachments", fake_list)
    response = _fetch(client, entry.id, "<gone@mail>")
    assert response.status_code == 404
    assert "mailbox" in response.json()["detail"]


def test_a_delegated_permission_mistake_is_explained(
    client, entry, docs_folder, graph_configured, monkeypatch,
):
    """403 is the likeliest first-setup failure, so it says what to fix."""
    def fake_list(message_id):
        raise graph_client.GraphError(
            "Graph refused access to the mailbox (403). The app registration "
            "most likely has DELEGATED Mail.Read rather than the APPLICATION "
            "permission, or admin consent has not been granted.",
            403,
        )

    monkeypatch.setattr(graph_client, "list_attachments", fake_list)
    response = _fetch(client, entry.id, "<msg-graph-1@mail>")
    assert response.status_code == 403
    assert "APPLICATION" in response.json()["detail"]


def test_no_credential_value_is_ever_returned_in_a_response(
    client, entry, docs_folder, graph_configured, monkeypatch,
):
    """A secret must not leak into an error body the caller logs."""
    _stub_graph(monkeypatch, [_item("att-1", "P.pdf", 3)], {"att-1": b"abc"})
    body = _fetch(client, entry.id, "<msg-graph-1@mail>").text
    assert "test-secret" not in body
    assert "test-client" not in body
