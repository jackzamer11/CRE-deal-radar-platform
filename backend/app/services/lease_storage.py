"""Where a lease PDF lives on disk, and how a stored filename resolves to it.

One rule, and everything here exists to enforce it: **the database stores a bare
filename, never a path.** The folder is a single setting
(`settings.LEASES_FOLDER`, overridable by the LEASES_FOLDER env var) and the
join happens at read time. Moving the leases folder is then a one-setting
change, not a migration that rewrites every row — and no absolute,
machine-specific or user-specific value is ever written into a data column.

The same rule is what makes the schema tenant-agnostic: the folder is a
deployment setting, not a property of a row, so adding an owner column later
does not have to untangle per-user paths out of stored data.
"""
import os
import re
from typing import Optional

from app.config import settings

# A stored filename must survive a round trip through the filesystem and a URL.
# Anything outside this set is replaced rather than rejected: losing the upload
# because of a stray character in the name would be the worse failure.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]+")

# Guard against a filename that would escape the leases folder. Defence in
# depth — collision_safe_name() already strips separators.
_TRAVERSAL = ("..", "/", "\\", ":")


def leases_folder() -> str:
    """The configured leases folder, read at call time (never cached at import).

    Reading it per call is what makes the setting live: flipping LEASES_FOLDER
    re-points every existing row with no data change.
    """
    return os.path.abspath(os.path.expanduser(str(settings.LEASES_FOLDER)))


def sanitize_file_name(raw: Optional[str]) -> str:
    """Reduce an uploaded name to a bare, safe filename.

    Strips any directory component the browser sent (Windows and POSIX
    separators both), then anything that is not plainly safe.
    """
    name = (raw or "").strip()
    # Take the basename under either separator — a browser may send either.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_CHARS.sub("_", name).strip(". ")
    return name or "lease.pdf"


def is_bare_file_name(name: Optional[str]) -> bool:
    """True when `name` is a filename with no path component.

    Used by the tests that lock "no absolute path is ever written to a column",
    and by resolve_lease_path() before it trusts a stored value.
    """
    if not name:
        return False
    if os.path.isabs(name):
        return False
    return not any(token in name for token in _TRAVERSAL)


def collision_safe_name(file_name: str, folder: Optional[str] = None) -> str:
    """A filename that does not already exist in the leases folder.

    "Lease.pdf" → "Lease.pdf", then "Lease (2).pdf", "Lease (3).pdf", …
    Never overwrites an existing lease: two tenants' leases can easily arrive
    with the same name out of the same email client, and silently replacing one
    would destroy a document.
    """
    target_folder = folder or leases_folder()
    safe = sanitize_file_name(file_name)
    stem, ext = os.path.splitext(safe)
    candidate = safe
    counter = 2
    while os.path.exists(os.path.join(target_folder, candidate)):
        candidate = f"{stem} ({counter}){ext}"
        counter += 1
    return candidate


def store_lease_file(
    file_name: str, contents: bytes, folder: Optional[str] = None,
) -> str:
    """Copy an uploaded lease into the leases folder. Returns the BARE filename.

    The returned value is what goes in Company.lease_file_name — the caller
    never stores the path it was written to. `folder` is injectable so tests
    write into a temp directory instead of Jack's OneDrive.
    """
    target_folder = folder or leases_folder()
    os.makedirs(target_folder, exist_ok=True)
    stored_name = collision_safe_name(file_name, folder=target_folder)
    with open(os.path.join(target_folder, stored_name), "wb") as handle:
        handle.write(contents)
    return stored_name


def resolve_lease_path(file_name: Optional[str], folder: Optional[str] = None) -> Optional[str]:
    """Absolute path of a stored lease, or None when there is nothing stored.

    Returns a path whether or not the file is actually there — existence is the
    caller's question to answer, so it can say "the file is missing" plainly
    instead of failing silently. A stored value that is not a bare filename is
    refused rather than joined: that would mean a path leaked into the column.
    """
    if not file_name:
        return None
    if not is_bare_file_name(file_name):
        return None
    return os.path.join(folder or leases_folder(), file_name)


def lease_file_exists(file_name: Optional[str], folder: Optional[str] = None) -> bool:
    path = resolve_lease_path(file_name, folder=folder)
    return bool(path) and os.path.isfile(path)
