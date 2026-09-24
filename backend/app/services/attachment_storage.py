"""Where an emailed attachment lives on disk, and how a stored row resolves to it.

The same rule as leases, and everything here exists to enforce it: **the
database never stores an absolute path.** The folder is a single setting
(`settings.DOCUMENTS_FOLDER`, overridable by the DOCUMENTS_FOLDER env var) and
the join happens at read time:

    <DOCUMENTS_FOLDER>/<stored_path>          e.g. ".../2026/41-Floor Plan.pdf"
    <DOCUMENTS_FOLDER>/<stored_year>/<file_name>   (rows predating stored_path)

Moving the documents folder is then a one-setting change, not a migration that
rewrites every row — and no absolute, machine-specific or user-specific value is
ever written into a data column.

Two names, on purpose. `file_name` is what arrived and what the interface
shows; `stored_path` is where the copy went, prefixed with the entry id because
names collide constantly and no attachment may ever overwrite another. A file
over `settings.MAX_ATTACHMENT_BYTES` is recorded and not written.

Nothing here raises. A file that cannot be stored costs the file, never the
entry it arrived on: the row is written either way and the interface says
plainly whether the file is missing or was too large.

Deliberately NOT the leases folder, and deliberately no path into the lease
flow. Leases get traded back and forth in draft, and the ingestion task cannot
tell draft eleven from the executed copy. Only Jack uploads an executed lease,
and he does it on purpose.
"""
import os
import shutil
from datetime import date
from typing import Optional, Tuple

from app.config import settings

# Reuse the lease rules for what a safe filename is — one definition of "bare
# filename" across both stores, so a test that locks it locks both.
from app.services.lease_storage import (
    collision_safe_name, is_bare_file_name, sanitize_file_name,
)

__all__ = [
    "documents_folder", "year_folder", "is_bare_file_name", "sanitize_file_name",
    "store_attachment", "resolve_attachment_path", "attachment_file_exists",
    "max_attachment_bytes", "unique_stored_name", "store_attachment_bytes",
    "resolve_stored_path", "stored_path_exists", "relative_stored_path",
    "OVERSIZE", "STORED", "NO_FILE",
]

# What became of the bytes. Returned rather than raised: an attachment that
# cannot be written must cost the attachment, never the entry it arrived on.
STORED = "stored"
OVERSIZE = "oversize"
NO_FILE = "no_file"


def max_attachment_bytes() -> int:
    """The per-file ceiling, read at call time so raising it needs no edit."""
    try:
        return int(settings.MAX_ATTACHMENT_BYTES)
    except (TypeError, ValueError):
        return 50 * 1024 * 1024


def documents_folder() -> str:
    """The configured documents folder, read at call time (never cached).

    Reading it per call is what makes the setting live: flipping
    DOCUMENTS_FOLDER re-points every existing row with no data change.
    """
    return os.path.abspath(os.path.expanduser(str(settings.DOCUMENTS_FOLDER)))


def _is_valid_year(year) -> bool:
    """A year is a number, not a path fragment. Anything else is refused."""
    try:
        value = int(year)
    except (TypeError, ValueError):
        return False
    return 1900 <= value <= 2999


def year_folder(year: int, folder: Optional[str] = None) -> Optional[str]:
    """The <folder>/<year> subfolder, or None when the year is not a year."""
    if not _is_valid_year(year):
        return None
    return os.path.join(folder or documents_folder(), str(int(year)))


def store_attachment(
    file_name: str,
    source_path: Optional[str] = None,
    *,
    saved_on: Optional[date] = None,
    folder: Optional[str] = None,
) -> Tuple[str, int, bool]:
    """File an attachment under <folder>/<year>. Returns (file_name, year, stored).

    The returned file_name is a BARE filename and year is an int — together they
    are the only things the caller writes to a column.

    `source_path` is where the ingestion task downloaded the file. When it is
    missing or unreadable the metadata row is still written (stored=False) and
    the UI reports the file as missing, exactly as a lease whose PDF has been
    moved does. Losing the record of what arrived, because the copy failed,
    would be the worse outcome: the filename and the description are the part
    Jack actually reads.

    `folder` is injectable so tests write into a temp directory instead of
    Jack's OneDrive.
    """
    year = (saved_on or date.today()).year
    target_folder = year_folder(year, folder) or os.path.join(
        folder or documents_folder(), str(year)
    )
    safe = sanitize_file_name(file_name)

    if not source_path or not os.path.isfile(source_path):
        return safe, year, False

    try:
        os.makedirs(target_folder, exist_ok=True)
        stored_name = collision_safe_name(safe, folder=target_folder)
        destination = os.path.join(target_folder, stored_name)
        # Already filed here by a previous run — nothing to copy, and copying
        # would produce "file (2).pdf" beside an identical file.
        if os.path.abspath(source_path) == os.path.abspath(
            os.path.join(target_folder, safe)
        ):
            return safe, year, True
        shutil.copyfile(source_path, destination)
        return stored_name, year, True
    except OSError:
        # A copy that fails must not cost the entry. The row is written with
        # the bare name and the UI says the file is missing.
        return safe, year, False


def unique_stored_name(entry_id: int, file_name: str, target_folder: str) -> str:
    """The name the copy is written under: "<entry id>-<original>".

    The original name is NOT what lands on disk, because names collide
    constantly — two tenants both send "Floor Plan.pdf" in the same week, and
    one email thread sends three revisions of "Proposal.docx". Prefixing the
    entry id keeps every entry's files apart in one flat year folder, and
    collision_safe_name() handles the leftover case that the prefix cannot:
    the SAME entry receiving the same filename twice, which becomes
    "41-Proposal.docx" then "41-Proposal (2).docx". Nothing is ever overwritten.

    file_name is shown in the interface untouched; only the copy is renamed.
    """
    safe = sanitize_file_name(file_name)
    return collision_safe_name(f"{int(entry_id)}-{safe}", folder=target_folder)


def relative_stored_path(year: int, stored_name: str) -> str:
    """The value that goes in the column: "<year>/<stored name>", POSIX slash.

    Relative, and always with a forward slash, so the stored value is identical
    on Windows and POSIX and carries nothing machine-specific. os.path.join()
    re-joins it to the configured folder at read time.
    """
    return f"{int(year)}/{stored_name}"


def store_attachment_bytes(
    entry_id: int,
    file_name: str,
    contents: bytes,
    *,
    year: Optional[int] = None,
    saved_on: Optional[date] = None,
    folder: Optional[str] = None,
) -> Tuple[str, Optional[str], int, str]:
    """Write attachment bytes under <folder>/<year>. Never raises.

    Returns (display_name, relative_stored_path, year, status) where status is
    STORED, OVERSIZE or NO_FILE. display_name is the sanitized ORIGINAL name —
    what the interface shows — and relative_stored_path is what goes in the
    column, or None when no file was written.

    A file over the ceiling is refused here rather than at the route, so the
    row is still recorded and the caller reports the ceiling instead of failing
    the entry. A write that fails for any other reason (a full disk, a locked
    folder) is treated the same way: the metadata survives, the file does not.

    The year folder is created when it does not exist.
    """
    resolved_year = year if _is_valid_year(year) else (saved_on or date.today()).year
    resolved_year = int(resolved_year)
    display = sanitize_file_name(file_name)

    if contents is not None and len(contents) > max_attachment_bytes():
        return display, None, resolved_year, OVERSIZE

    target_folder = year_folder(resolved_year, folder)
    if not target_folder:
        return display, None, resolved_year, NO_FILE

    try:
        os.makedirs(target_folder, exist_ok=True)
        stored_name = unique_stored_name(entry_id, display, target_folder)
        with open(os.path.join(target_folder, stored_name), "wb") as handle:
            handle.write(contents or b"")
        return display, relative_stored_path(resolved_year, stored_name), resolved_year, STORED
    except OSError:
        return display, None, resolved_year, NO_FILE


def resolve_stored_path(
    stored_path: Optional[str], folder: Optional[str] = None,
) -> Optional[str]:
    """Absolute path of a stored copy, or None when there is nothing to resolve.

    Returns a path whether or not the file is there — existence is the caller's
    question, so it can say "the file is missing" plainly. A stored value that
    is absolute or climbs out of the folder is refused rather than joined:
    either would mean the column is being trusted to address the filesystem,
    which is exactly what storing a relative path is meant to prevent.
    """
    if not stored_path:
        return None
    raw = str(stored_path).strip().replace("\\", "/")
    if not raw or raw.startswith("/") or os.path.isabs(raw):
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return os.path.join(folder or documents_folder(), *parts)


def stored_path_exists(
    stored_path: Optional[str], folder: Optional[str] = None,
) -> bool:
    path = resolve_stored_path(stored_path, folder=folder)
    return bool(path) and os.path.isfile(path)


def resolve_attachment_path(
    file_name: Optional[str], year: Optional[int], folder: Optional[str] = None,
) -> Optional[str]:
    """Absolute path of a stored attachment, or None when there is nothing to resolve.

    Returns a path whether or not the file is actually there — existence is the
    caller's question, so it can say "the file is missing" plainly instead of
    failing silently. A stored value carrying a path component, or a year that
    is not a year, is refused rather than joined: either would mean a path
    leaked into a data column.
    """
    if not file_name or not is_bare_file_name(file_name):
        return None
    base = year_folder(year, folder)
    if not base:
        return None
    return os.path.join(base, file_name)


def attachment_file_exists(
    file_name: Optional[str], year: Optional[int], folder: Optional[str] = None,
) -> bool:
    path = resolve_attachment_path(file_name, year, folder=folder)
    return bool(path) and os.path.isfile(path)
