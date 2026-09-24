"""Email attachments are saved to disk, and an entry can open them.

Before this, activity_attachments held only a filename and a year, and the
ingestion task had an attachment's BYTES rather than a file on Jack's disk — so
nothing was ever written and every attachment on every entry rendered
"(file missing)". These lock the behaviour that fixes it:

  - a file saves and its RELATIVE path is stored (never an absolute one)
  - a duplicate filename does not overwrite the first file
  - an oversize file records a row with no file and does not fail the entry
  - an inline image records a row with no file
  - an entry with no attachments is unaffected
  - the missing-file check finds a file deleted off disk

The folder is injected via settings.DOCUMENTS_FOLDER, so nothing here writes
outside its temp directory, and no live DB, network or CoStar call is made.
"""
import base64
import os
from datetime import date

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models                 # noqa: F401 - registers every table on Base.metadata
import app.models.outreach_log    # noqa: F401
import app.models.outreach_draft  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_ingest import ActivityAttachment
from app.services import attachment_storage


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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def docs_folder(tmp_path, monkeypatch):
    """Point the documents folder at a temp directory.

    Patched on `attachment_storage.settings` — the binding the storage module
    actually reads — so the real folder is never touched.
    """
    folder = tmp_path / "attachments"
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(folder), raising=False,
    )
    assert attachment_storage.documents_folder() == str(folder)
    return folder


@pytest.fixture
def entry(db_session):
    """One activity entry to hang attachments on."""
    company = Company(
        company_id="CAV-001", name="Collaborative AV", industry="Tech",
    )
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
    )
    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)
    return log


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _upload(client, entry_id, filename, contents=b"%PDF-1.4 fake", **kw):
    payload = {
        "entry_id": entry_id,
        "filename": filename,
        "content_base64": _b64(contents),
    }
    payload.update(kw)
    response = client.post("/api/activity/attachments/upload", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ── 1. A file saves and its path is stored ────────────────────────────────────

def test_a_file_saves_and_its_path_is_stored(db_session, client, entry, docs_folder):
    body = _upload(client, entry.id, "Floor Plan.pdf",
                   contents=b"%PDF-1.4 fake", description="Suite 400 test fit")

    assert body["status"] == "stored"
    assert body["stored"] is True
    assert body["oversize"] is False
    assert body["missing"] is False

    row = db_session.query(ActivityAttachment).one()
    # The ORIGINAL name is what the interface shows.
    assert row.file_name == "Floor Plan.pdf"
    assert row.stored_year == 2026
    assert row.description == "Suite 400 test fit"
    assert row.oversize is False

    # The stored path is RELATIVE to the documents folder — the invariant the
    # whole storage layer exists to hold. No absolute path, no drive letter, no
    # machine- or user-specific value in any column.
    assert row.stored_path == "2026/{}-Floor Plan.pdf".format(entry.id)
    assert not os.path.isabs(row.stored_path)
    for value in (row.file_name, row.stored_path, row.description):
        assert ":" not in str(value)
        assert "\\" not in str(value)
        assert "Jackz" not in str(value)

    # It resolves through the configured folder, and the bytes are really there.
    resolved = attachment_storage.resolve_stored_path(row.stored_path)
    assert resolved == str(docs_folder / "2026" / f"{entry.id}-Floor Plan.pdf")
    assert os.path.isfile(resolved)
    assert open(resolved, "rb").read() == b"%PDF-1.4 fake"

    # And the entry can open it.
    opened = client.get(f"/api/activity/attachments/{row.id}/file")
    assert opened.status_code == 200
    assert opened.content == b"%PDF-1.4 fake"


def test_the_folder_is_created_when_it_does_not_exist(client, entry, docs_folder):
    """Nothing is pre-made on disk; the first upload builds the year folder."""
    assert not docs_folder.exists()
    _upload(client, entry.id, "First.pdf")
    assert (docs_folder / "2026").is_dir()


def test_moving_the_documents_folder_repoints_a_stored_row(
    db_session, client, entry, docs_folder, tmp_path, monkeypatch,
):
    """A relative stored_path is what keeps the folder a one-setting change."""
    _upload(client, entry.id, "Notes.pdf")
    row = db_session.query(ActivityAttachment).one()

    moved = tmp_path / "somewhere-else"
    monkeypatch.setattr(
        attachment_storage.settings, "DOCUMENTS_FOLDER", str(moved), raising=False,
    )
    assert attachment_storage.resolve_stored_path(row.stored_path) == str(
        moved / "2026" / f"{entry.id}-Notes.pdf"
    )


# ── 2. A duplicate filename does not overwrite ────────────────────────────────

def test_a_duplicate_filename_does_not_overwrite(
    db_session, client, entry, docs_folder,
):
    """Two files named the same must both survive.

    Tenants send "Proposal.docx" over and over, and three revisions of one
    document all arriving as the same name is the ordinary case, not the odd
    one. Silently replacing the first would destroy a document.
    """
    first = _upload(client, entry.id, "Proposal.docx", contents=b"revision one")
    second = _upload(client, entry.id, "Proposal.docx", contents=b"revision two")

    assert first["stored"] and second["stored"]
    assert first["stored_path"] != second["stored_path"]

    # Both rows still show the name the file arrived under.
    rows = db_session.query(ActivityAttachment).order_by(ActivityAttachment.id).all()
    assert [r.file_name for r in rows] == ["Proposal.docx", "Proposal.docx"]

    # And both sets of bytes are on disk, unharmed.
    path_one = attachment_storage.resolve_stored_path(rows[0].stored_path)
    path_two = attachment_storage.resolve_stored_path(rows[1].stored_path)
    assert open(path_one, "rb").read() == b"revision one"
    assert open(path_two, "rb").read() == b"revision two"

    # Each opens its own file through the entry.
    assert client.get(f"/api/activity/attachments/{rows[0].id}/file").content == b"revision one"
    assert client.get(f"/api/activity/attachments/{rows[1].id}/file").content == b"revision two"


def test_the_same_name_on_two_entries_does_not_collide(
    db_session, client, entry, docs_folder,
):
    """The entry id prefix is what keeps one flat year folder workable."""
    other = ActivityLog(
        log_date=date(2026, 4, 3), action_type="Email",
        action_taken="Someone else sent a plan.",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    a = _upload(client, entry.id, "Floor Plan.pdf", contents=b"theirs")
    b = _upload(client, other.id, "Floor Plan.pdf", contents=b"ours")

    assert a["stored_path"] != b["stored_path"]
    assert str(entry.id) in a["stored_path"]
    assert str(other.id) in b["stored_path"]


# ── 3. An oversize file records a row with no file ────────────────────────────

def test_an_oversize_file_records_a_row_and_does_not_fail_the_entry(
    db_session, client, entry, docs_folder, monkeypatch,
):
    """One outsized file must not cost Jack the record of what arrived.

    The ceiling is lowered rather than a 50MB payload built: the rule is the
    behaviour at the boundary, not the number.
    """
    monkeypatch.setattr(
        attachment_storage.settings, "MAX_ATTACHMENT_BYTES", 32, raising=False,
    )

    body = _upload(client, entry.id, "Site Walkthrough.mp4", contents=b"x" * 500)

    # Reported back so the caller can tell Jack, rather than raising.
    assert body["status"] == "oversize"
    assert body["oversize"] is True
    assert body["stored"] is False
    assert body["stored_path"] is None
    assert body["size_bytes"] == 500
    assert body["max_bytes"] == 32
    assert "over the" in body["detail"]

    # The row exists — the filename and description are what Jack reads.
    row = db_session.query(ActivityAttachment).one()
    assert row.file_name == "Site Walkthrough.mp4"
    assert row.oversize is True
    assert row.stored_path is None

    # Nothing was written to disk.
    assert not (docs_folder / "2026").exists() or not any(
        (docs_folder / "2026").iterdir()
    )

    # The entry itself is untouched and still readable.
    db_session.refresh(entry)
    assert entry.action_taken == "Dana sent the floor plan."

    # Opening it says it was too large, not that something is broken.
    opened = client.get(f"/api/activity/attachments/{row.id}/file")
    assert opened.status_code == 404
    assert "too large" in opened.json()["detail"]


def test_an_oversize_file_does_not_stop_the_next_attachment(
    db_session, client, entry, docs_folder, monkeypatch,
):
    """The failure is per-file, so a good file after a bad one still lands."""
    monkeypatch.setattr(
        attachment_storage.settings, "MAX_ATTACHMENT_BYTES", 32, raising=False,
    )
    _upload(client, entry.id, "Huge.mp4", contents=b"x" * 500)
    good = _upload(client, entry.id, "Small.pdf", contents=b"tiny")

    assert good["status"] == "stored"
    rows = db_session.query(ActivityAttachment).order_by(ActivityAttachment.id).all()
    assert [r.oversize for r in rows] == [True, False]


# ── 4. An inline image records a row with no file ─────────────────────────────

def test_an_inline_image_records_a_row_with_no_file(
    db_session, client, entry, docs_folder,
):
    """A signature image is recorded so the entry shows what came with the
    email, and never written — filing forty copies of a logo helps nobody."""
    body = _upload(client, entry.id, "signature-logo.png",
                   contents=b"\x89PNG fake", inline=True)

    assert body["status"] == "inline"
    assert body["stored"] is False
    assert body["stored_path"] is None
    assert body["missing"] is True

    row = db_session.query(ActivityAttachment).one()
    assert row.file_name == "signature-logo.png"
    assert row.stored_path is None
    assert row.oversize is False

    # No file was written anywhere under the documents folder.
    written = []
    for root, _dirs, files in os.walk(str(docs_folder)):
        written.extend(files)
    assert written == []


# ── 5. An entry with no attachments is unaffected ─────────────────────────────

def test_an_entry_with_no_attachments_is_unaffected(db_session, client, entry, docs_folder):
    """The commonest entry there is. It must cost nothing and show nothing."""
    assert db_session.query(ActivityAttachment).count() == 0

    thread = client.get(f"/api/contacts/{entry.contact_id}/timeline")
    assert thread.status_code == 200
    entries = thread.json()["entries"]
    assert len(entries) >= 1
    assert all(e["attachments"] == [] for e in entries)

    # And the check reports nothing to worry about.
    report = client.get("/api/activity/attachments/missing-files").json()
    assert report["checked"] == 0
    assert report["missing"] == 0
    assert report["attachments"] == []


# ── 6. The missing-file check finds a deleted file ────────────────────────────

def test_the_missing_file_check_finds_a_deleted_file(
    db_session, client, entry, docs_folder,
):
    """A document that went missing off disk is the thing worth reporting."""
    kept = _upload(client, entry.id, "Kept.pdf", contents=b"still here")
    lost = _upload(client, entry.id, "Lost.pdf", contents=b"about to vanish")

    # Both are fine to begin with.
    clean = client.get("/api/activity/attachments/missing-files").json()
    assert clean["checked"] == 2
    assert clean["missing"] == 0

    # Delete one off disk behind the database's back.
    os.remove(attachment_storage.resolve_stored_path(lost["stored_path"]))

    report = client.get("/api/activity/attachments/missing-files").json()
    assert report["checked"] == 2
    assert report["missing"] == 1
    found = report["attachments"][0]
    assert found["id"] == lost["id"]
    assert found["file_name"] == "Lost.pdf"
    assert found["entry_id"] == entry.id
    assert found["reason"] == "stored file is no longer on disk"

    # The surviving one is not dragged in with it.
    assert kept["id"] not in [a["id"] for a in report["attachments"]]

    # The thread now renders the deleted one as missing and the other as fine.
    thread = client.get(f"/api/contacts/{entry.contact_id}/timeline").json()
    by_name = {
        a["file_name"]: a
        for e in thread["entries"] for a in e["attachments"]
    }
    assert by_name["Lost.pdf"]["missing"] is True
    assert by_name["Kept.pdf"]["missing"] is False


def test_the_check_ignores_rows_that_never_had_a_file_by_default(
    db_session, client, entry, docs_folder,
):
    """An inline image with no file is working as intended, not breakage.

    Burying a real loss among dozens of signature images would make the report
    useless, so they are reported only when asked for.
    """
    _upload(client, entry.id, "signature.png", inline=True)

    default = client.get("/api/activity/attachments/missing-files").json()
    assert default["checked"] == 1
    assert default["missing"] == 0

    asked = client.get(
        "/api/activity/attachments/missing-files",
        params={"include_never_stored": "true"},
    ).json()
    assert asked["missing"] == 1
    assert asked["attachments"][0]["reason"] == "no file was ever stored for this row"


# ── Guards ────────────────────────────────────────────────────────────────────

def test_a_stored_path_that_climbs_out_of_the_folder_is_refused():
    """The column is never trusted to address the filesystem."""
    for hostile in (
        "../../Windows/System32/config",
        "/etc/passwd",
        r"C:\Users\Jackz\secrets.txt",
        "2026/../../../escape.pdf",
        "",
        None,
    ):
        assert attachment_storage.resolve_stored_path(hostile) is None


def test_uploading_against_an_unknown_entry_is_a_404(client, docs_folder):
    """There is nothing to attach to, and inventing a row would hide the bug."""
    response = client.post("/api/activity/attachments/upload", json={
        "entry_id": 999999,
        "filename": "Orphan.pdf",
        "content_base64": _b64(b"x"),
    })
    assert response.status_code == 404
