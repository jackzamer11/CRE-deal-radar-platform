"""Where an emailed attachment lives on disk, and how a stored row resolves to it.

The same rule as leases, and everything here exists to enforce it: **the
database stores a bare filename plus a year, never a path.** The folder is a
single setting (`settings.DOCUMENTS_FOLDER`, overridable by the DOCUMENTS_FOLDER
env var) and the join happens at read time:

    <DOCUMENTS_FOLDER>/<stored_year>/<file_name>

Moving the documents folder is then a one-setting change, not a migration that
rewrites every row — and no absolute, machine-specific or user-specific value is
ever written into a data column.

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
]


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
