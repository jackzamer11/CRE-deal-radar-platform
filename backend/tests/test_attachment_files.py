"""Email attachments are saved to disk, and an entry can open them.

Before this, activity_attachments held only a filename and a year, and the
ingestion task had an attachment's BYTES rather than a file on Jack's disk — so
nothing was ever written and every attachment on every entry rendered
"(file missing)". These lock the behaviour that fixes it:

  - a file saves and its RELATIVE path is stored (never an absolute one)
  - an entry plus a filename is ONE attachment: a re-send repairs the row
    and its file rather than adding a second of either
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


# ── 2. One attachment, one row: repair in place ────────────────────────────────

def test_uploading_the_same_file_twice_leaves_exactly_one_row(
    db_session, client, entry, docs_folder,
):
    """An entry plus a filename identifies ONE attachment, so a re-send repairs
    it rather than adding a second.

    This replaces an earlier contract where two uploads of one name produced
    two rows. That was wrong for the way the file actually arrives: the
    nightly task writes the row first and the bytes second, so "same entry,
    same name" means "the file for that row", and inserting again would show
    Jack the attachment twice — once dead, once live. Collision safety still
    holds where it matters, between entries, which
    test_the_same_name_on_two_entries_does_not_collide covers.
    """
    first = _upload(client, entry.id, "Proposal.docx", contents=b"revision one")
    second = _upload(client, entry.id, "Proposal.docx", contents=b"revision two")

    assert first["stored"] and second["stored"]
    assert first["created"] is True
    assert second["created"] is False
    # The same row, repaired — not a new one.
    assert second["id"] == first["id"]
    assert second["stored_path"] == first["stored_path"]

    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    assert rows[0].file_name == "Proposal.docx"

    # The copy on disk is the latest bytes, and there is only one of it.
    path = attachment_storage.resolve_stored_path(rows[0].stored_path)
    assert open(path, "rb").read() == b"revision two"
    assert len(list((docs_folder / "2026").iterdir())) == 1

    assert client.get(f"/api/activity/attachments/{rows[0].id}/file").content == b"revision two"


def test_uploading_for_an_existing_row_updates_it_and_creates_nothing(
    db_session, client, entry, docs_folder,
):
    """The case that happens every night: the row exists, the file does not.

    /from-email writes the entry and its attachment rows with no file, then the
    task returns with the bytes. That upload must fill in the row it finds.
    """
    # The row as /from-email leaves it: recorded, no file, renders "(missing)".
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

    body = _upload(client, entry.id, "Floor Plan.pdf", contents=b"%PDF-1.4 real")

    assert body["created"] is False
    assert body["id"] == placeholder_id
    assert body["stored"] is True
    assert body["missing"] is False

    # Still exactly one row, and it is the original.
    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    assert rows[0].id == placeholder_id

    db_session.refresh(rows[0])
    assert rows[0].stored_path is not None
    # The description written when the row was created survives an upload that
    # does not carry one.
    assert rows[0].description == "Suite 400 test fit"
    assert open(attachment_storage.resolve_stored_path(rows[0].stored_path), "rb").read() == b"%PDF-1.4 real"

    # And it now opens from the entry.
    assert client.get(f"/api/activity/attachments/{placeholder_id}/file").status_code == 200


def test_uploading_with_no_matching_row_inserts_one(
    db_session, client, entry, docs_folder,
):
    """A file with no row waiting for it is still stored, not refused.

    The row is written first in the ordinary flow, but an upload that arrives
    on its own must not be dropped on the floor.
    """
    assert db_session.query(ActivityAttachment).count() == 0

    body = _upload(client, entry.id, "Unannounced.pdf", contents=b"bytes")

    assert body["created"] is True
    assert body["stored"] is True

    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    assert rows[0].file_name == "Unannounced.pdf"
    assert rows[0].stored_path is not None


def test_a_matching_row_is_found_on_the_sanitized_name(
    db_session, client, entry, docs_folder,
):
    """The caller sends the name as it arrived; the column holds it cleaned.

    Outlook's "4,562SF.jpg" is stored as "4_562SF.jpg", so matching on the raw
    name would miss the row and duplicate it — which is exactly the bug this
    endpoint exists to avoid.
    """
    placeholder = ActivityAttachment(
        activity_log_id=entry.id,
        file_name=attachment_storage.sanitize_file_name("4,562SF.jpg"),
        stored_year=2026,
        stored_path=None,
        oversize=False,
        saved_date=date(2026, 4, 2),
    )
    db_session.add(placeholder)
    db_session.commit()

    body = _upload(client, entry.id, "4,562SF.jpg", contents=b"jpegbytes")

    assert body["created"] is False
    assert db_session.query(ActivityAttachment).count() == 1


def test_repairing_a_row_does_not_orphan_its_previous_file(
    db_session, client, entry, docs_folder,
):
    """A replaced copy overwrites the one the row points at.

    Writing a fresh name each time would leave the superseded file sitting in
    the folder with nothing referring to it, which the missing-file check
    cannot see and nobody would ever clean up.
    """
    _upload(client, entry.id, "Plan.pdf", contents=b"one")
    _upload(client, entry.id, "Plan.pdf", contents=b"two")
    _upload(client, entry.id, "Plan.pdf", contents=b"three")

    assert db_session.query(ActivityAttachment).count() == 1
    assert len(list((docs_folder / "2026").iterdir())) == 1


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


def test_from_email_then_upload_leaves_one_working_row(
    db_session, client, entry, docs_folder,
):
    """The nightly sequence, end to end: row first, bytes second.

    This is the shape of the 7pm task. /from-email records what arrived with
    no file (it is handed no readable local path), and the upload that follows
    must fill THAT row in. If it inserted instead, every attachment would show
    twice on the entry from tonight onwards.
    """
    posted = client.post("/api/activity/from-email", json={
        "direction": "inbound",
        "from_email": "dana@collaborative-av.com",
        "from_name": "Dana Reid",
        "to_email": "jzamer@z-reg.com",
        "subject": "Suite 400",
        "action_taken": "Dana sent the floor plan.",
        "sent_at": "2026-04-02",
        "source_message_id": "<msg-e2e-1@mail>",
        "attachments": [{"filename": "Floor Plan.pdf", "description": "Test fit"}],
    })
    assert posted.status_code == 200, posted.text
    body = posted.json()
    log_id = body["id"]

    # One row, recorded with no file — exactly what renders "(file missing)".
    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    assert rows[0].stored_path is None
    row_id = rows[0].id

    # The task comes back with the bytes.
    uploaded = _upload(client, log_id, "Floor Plan.pdf", contents=b"%PDF-1.4 plan")
    assert uploaded["created"] is False
    assert uploaded["id"] == row_id
    assert uploaded["stored"] is True

    # Still one row, now with a file behind it.
    rows = db_session.query(ActivityAttachment).all()
    assert len(rows) == 1
    db_session.refresh(rows[0])
    assert rows[0].stored_path is not None
    assert rows[0].description == "Test fit"

    assert client.get(f"/api/activity/attachments/{row_id}/file").content == b"%PDF-1.4 plan"

    # And the entry itself was neither duplicated nor altered.
    assert db_session.query(ActivityLog).filter(ActivityLog.id == log_id).count() == 1


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


def test_an_oversize_resend_does_not_blank_out_a_stored_file(
    db_session, client, entry, docs_folder, monkeypatch,
):
    """A good file already on disk survives a later oversize upload of it.

    Only a real write moves the pointer. Clearing stored_path here would turn
    a working attachment into "(file missing)" over an upload that never had
    any chance of replacing it.
    """
    _upload(client, entry.id, "Plan.pdf", contents=b"the good copy")
    row = db_session.query(ActivityAttachment).one()
    good_path = row.stored_path
    assert good_path is not None

    monkeypatch.setattr(
        attachment_storage.settings, "MAX_ATTACHMENT_BYTES", 8, raising=False,
    )
    body = _upload(client, entry.id, "Plan.pdf", contents=b"x" * 500)

    assert body["created"] is False
    assert body["oversize"] is True

    db_session.refresh(row)
    assert db_session.query(ActivityAttachment).count() == 1
    assert row.oversize is True
    # The file, and the link to it, are untouched.
    assert row.stored_path == good_path
    assert open(attachment_storage.resolve_stored_path(good_path), "rb").read() == b"the good copy"


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
