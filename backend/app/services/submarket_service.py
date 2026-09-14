"""The growing submarket list: matching, adding, and deriving from an address.

Three rules, each locked by tests/test_submarkets.py:

1. **Match before creating, case-insensitively.** "sterling" is Sterling. The
   table's NOCASE unique index backs this up, but the lookup happens first so
   a duplicate is never even attempted.
2. **Derive from what the document states.** On lease confirmation the premises
   address's city becomes the submarket — Jack should not have to type a place
   the lease already names.
3. **A failed derivation changes nothing.** An address that cannot be parsed
   leaves the company's submarket exactly as it was: no wrong value, no error.

Benchmarks are NOT this module's concern. A submarket added here has no entry
in config.SUBMARKET_BENCHMARKS, and every benchmark consumer already treats a
missing entry as "not on file" — nothing is quoted that does not exist.
"""
import re
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import PLATFORM_SUBMARKETS
from app.models.company import Company
from app.models.submarket import Submarket

MAX_NAME_LENGTH = 60

_WHITESPACE = re.compile(r"\s+")

# A trailing "VA", "Virginia 20166", "DC 20001", "MD" ... component. The state
# is what tells us the component before it is a city rather than a street or a
# suite: without one the address is treated as unparseable.
_STATE = r"(?:VA|Va|Virginia|DC|D\.C\.|District of Columbia|MD|Maryland)"
_ZIP = r"\d{5}(?:-\d{4})?"
_STATE_ZIP_RE = re.compile(rf"^{_STATE}\.?(?:\s+{_ZIP})?$", re.IGNORECASE)
_CITY_STATE_ZIP_RE = re.compile(
    rf"^(?P<city>[A-Za-z][A-Za-z .'\-]*?)\s+{_STATE}\.?(?:\s+{_ZIP})?$", re.IGNORECASE,
)
_ZIP_ONLY_RE = re.compile(rf"^{_ZIP}$")
_COUNTRY_RE = re.compile(r"^(?:USA|U\.S\.A\.|US|United States(?: of America)?)$", re.IGNORECASE)
_PLACE_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,39}$")
# Words that mean the component is part of the premises, not a place name.
_NOT_A_PLACE = re.compile(
    r"\b(?:suite|ste|floor|unit|room|building|bldg|"
    r"street|avenue|ave|road|rd|blvd|boulevard|drive|dr|parkway|pkwy)\b",
    re.IGNORECASE,
)
# "Arlington (Ballston)" -> "Arlington": the place a more specific submarket
# sits inside.
_PARENTHETICAL = re.compile(r"\s*\(.*\)\s*$")


def normalize_name(raw: Optional[str]) -> str:
    """Trim and collapse whitespace. Casing is kept as typed."""
    return _WHITESPACE.sub(" ", (raw or "").strip())


def find_submarket(db: Session, name: Optional[str]) -> Optional[Submarket]:
    """The existing submarket with this name, matched case-insensitively."""
    clean = normalize_name(name)
    if not clean:
        return None
    return (
        db.query(Submarket)
        .filter(func.lower(Submarket.name) == clean.lower())
        .first()
    )


def get_or_create_submarket(
    db: Session, name: Optional[str], auto_created: bool = False,
) -> Tuple[Submarket, bool]:
    """Return (submarket, created). Matches case-insensitively first.

    Raises ValueError for a blank or over-long name. Flushes, never commits —
    the caller owns the transaction.
    """
    clean = normalize_name(name)
    if not clean:
        raise ValueError("A submarket name is required.")
    if len(clean) > MAX_NAME_LENGTH:
        raise ValueError(f"A submarket name must be {MAX_NAME_LENGTH} characters or fewer.")
    existing = find_submarket(db, clean)
    if existing is not None:
        return existing, False
    row = Submarket(name=clean, auto_created=auto_created, created_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row, True


def _seed_names(db: Session) -> List[str]:
    names: List[str] = list(PLATFORM_SUBMARKETS)
    # Every value a company already carries, so an existing assignment is
    # always selectable in the dropdown it now reads from.
    for (value,) in db.query(Company.current_submarket).distinct().all():
        if value and normalize_name(value):
            names.append(normalize_name(value))
    return names


def ensure_seeded(db: Session) -> int:
    """Seed an EMPTY submarket table. Returns the number of rows added.

    The live database is seeded by migrations/ensure_schema.py; this covers a
    brand-new database, which ensure_schema skips because the file does not
    exist yet when it runs.
    """
    if db.query(Submarket.id).first() is not None:
        return 0
    seen = set()
    added = 0
    for name in _seed_names(db):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        db.add(Submarket(name=name, auto_created=False, created_at=datetime.utcnow()))
        added += 1
    db.commit()
    return added


def list_submarkets(db: Session) -> List[Submarket]:
    ensure_seeded(db)
    return db.query(Submarket).order_by(func.lower(Submarket.name)).all()


# ── Derivation from a premises address ────────────────────────────────────────

def _clean_place(raw: str) -> Optional[str]:
    place = normalize_name(raw).strip(" .")
    if not place or not _PLACE_RE.match(place) or _NOT_A_PLACE.search(place):
        return None
    # An all-caps or all-lower city ("MCLEAN") reads as a proper name when it
    # has to be created; an existing name is matched case-insensitively anyway.
    if place.isupper() or place.islower():
        place = place.title()
    return place


def derive_place_name(address: Optional[str]) -> Optional[str]:
    """The city named in a premises address, or None when it cannot be told.

    "1750 Tysons Blvd, Suite 400, McLean, VA 22102"  -> "McLean"
    "21000 Atlantic Blvd, Sterling, Virginia 20166"  -> "Sterling"
    "1750 Tysons Blvd McLean VA"                     -> None (no comma before
                                                        the city: guessing
                                                        which words are the
                                                        city is how a wrong
                                                        value gets written)
    """
    if not address or not isinstance(address, str):
        return None
    parts = [p.strip() for p in address.replace("\n", ",").split(",") if p.strip()]
    while parts and (_COUNTRY_RE.match(parts[-1]) or _ZIP_ONLY_RE.match(parts[-1])):
        parts.pop()
    if len(parts) < 2:
        return None

    last = parts[-1]
    if _STATE_ZIP_RE.match(last):
        return _clean_place(parts[-2])
    match = _CITY_STATE_ZIP_RE.match(last)
    if match:
        return _clean_place(match.group("city"))
    return None


def _base_place(submarket: Optional[str]) -> str:
    return _PARENTHETICAL.sub("", submarket or "").strip().lower()


def apply_derived_submarket(
    db: Session, company: Company, address: Optional[str],
) -> Optional[dict]:
    """Assign the submarket a confirmed premises address names.

    Returns {"submarket", "created"} describing what the company now holds, or
    None when nothing could be derived — in which case the company is left
    exactly as it was. Never raises for a bad address.

    A more specific submarket is never traded for a vaguer one: a company in
    "Arlington (Ballston)" whose lease says "Arlington, VA" keeps Ballston.
    """
    try:
        place = derive_place_name(address)
    except Exception:  # noqa: BLE001 — derivation failing must not fail a confirm
        return None
    if not place:
        return None

    current = company.current_submarket
    if current and _base_place(current) == place.lower():
        return {"submarket": current, "created": False}

    try:
        row, created = get_or_create_submarket(db, place, auto_created=True)
    except ValueError:
        return None
    company.current_submarket = row.name
    return {"submarket": row.name, "created": created}


def names(rows: Iterable[Submarket]) -> List[str]:
    return [r.name for r in rows]
