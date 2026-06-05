"""
Lease Comps ingestion — parse a CoStar Lease Activity PDF and match each lease's
tenant to an existing Company by NAME, proposing a lease-expiration date.

Companies in this platform are free-floating (name, industry, headcount,
submarket, lease_expiry, score — NO street address), so matching is by company
NAME only.

This module is split so the matching logic is pure and unit-testable without a
live database, network, or Claude call:

  - normalize_company_name / _compact_key : name canonicalization
  - compute_lease_matches                 : tiered name match (pure, no writes)
  - apply_expiry_to_company               : mutate a Company with a new expiry
  - parse_lease_text                       : pure text→leases parser (unit-testable)
  - parse_lease_pdf                        : extract text from the PDF locally
                                            (pdfplumber) then parse_lease_text it.
                                            No network, no Claude, zero API credits.

Tiered matching contract:
  (a) EXACT match after normalization (case, punctuation, legal suffixes,
      internal spaces removed) → auto-apply.
  (b) FUZZY close match (SequenceMatcher ratio >= FUZZY_THRESHOLD) → needs review.
  (c) No match → skipped-no-match.

Protection: a Company that already has ANY lease expiry value is NEVER
overwritten — it is reported as skipped-already-set. When one tenant appears on
multiple leases, the SOONEST expiration wins.
"""
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Optional


class LeaseParseError(Exception):
    """Raised when the uploaded PDF cannot be parsed into lease records."""


# Legal-entity suffixes stripped before comparing company names. Lower-case,
# punctuation already removed by the time this set is consulted.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "pllc", "pc", "pa", "llp", "lp",
    "corp", "corporation", "co", "company", "ltd", "limited", "plc", "group",
})

# SequenceMatcher ratio at/above which a non-exact name pair is offered for review.
FUZZY_THRESHOLD = 0.86

# lease_expiry_source written for PDF-sourced dates. "costar" is an accepted
# source in companies.VALID_LEASE_SOURCES and is accurate (CoStar export).
LEASE_SOURCE = "costar"


def normalize_company_name(name: Optional[str]) -> str:
    """Lower-case, drop punctuation, strip legal suffixes, collapse whitespace.

    "CannonDesign, Inc." -> "cannondesign"
    "Cannon Design"      -> "cannon design"
    """
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)          # punctuation -> space
    tokens = [t for t in s.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def _compact_key(name: Optional[str]) -> str:
    """Normalized name with ALL whitespace removed — the exact-match key.

    Makes "Cannon Design" and "CannonDesign Inc" collide on the same key so a
    pure spacing/suffix/case difference still auto-applies.
    """
    return normalize_company_name(name).replace(" ", "")


def parse_expiration(value) -> Optional[date]:
    """Coerce an expiration value to a date, or None.

    Accepts date / datetime / ISO-ish strings. Returns None for missing or
    unparseable values so a lease with no expiration is skipped, not crashed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a", "na", "tbd", "-"):
        return None
    # Try ISO first, then the CoStar formats. "%B %d, %Y" / "%b %d, %Y" cover the
    # real export form ("September 1, 2027"); the slash + "Month YYYY" forms cover
    # older/alternate exports.
    for fmt in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
        "%B %d, %Y", "%b %d, %Y",
        "%b %Y", "%B %Y", "%m/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _has_existing_expiry(company) -> bool:
    """True if the company already carries any lease expiry — never overwrite."""
    return (
        getattr(company, "lease_expiry_date", None) is not None
        or getattr(company, "lease_expiry_months", None) is not None
    )


def compute_lease_matches(parsed_leases, companies) -> dict:
    """Match parsed leases to companies by name. Pure — performs NO writes.

    Args:
      parsed_leases: list of dicts with keys tenant_name, expiration_date
                     (move_in_date / sf optional and ignored here).
      companies:     iterable of Company-like objects exposing .name,
                     .company_id, .lease_expiry_date, .lease_expiry_months.

    Returns a dict with four lists:
      auto_applied:        exact matches eligible for immediate write
      needs_review:        fuzzy matches awaiting user confirmation (with confidence)
      skipped_no_match:    tenants with no company match
      skipped_already_set: tenants whose matched company already has an expiry
    """
    # 1) Collapse leases to one entry per tenant, soonest valid expiry wins.
    tenants: dict = {}
    for lease in parsed_leases or []:
        name = (lease.get("tenant_name") or "").strip()
        if not name:
            continue
        expiry = parse_expiration(lease.get("expiration_date"))
        if expiry is None:
            # No usable expiration on this lease — skip it (no crash).
            continue
        key = _compact_key(name)
        if not key:
            continue
        cur = tenants.get(key)
        if cur is None or expiry < cur["expiry"]:
            tenants[key] = {"tenant_name": name, "expiry": expiry}

    # 2) Build company lookups: exact-key map + (normalized, company) for fuzzy.
    comp_by_compact: dict = {}
    comp_norm_list = []
    for c in companies:
        ck = _compact_key(getattr(c, "name", ""))
        if ck and ck not in comp_by_compact:
            comp_by_compact[ck] = c
        comp_norm_list.append((normalize_company_name(getattr(c, "name", "")), c))

    auto: dict = {}                 # company_id -> entry
    review: dict = {}               # company_id -> entry
    skipped_no_match = []
    skipped_already_set: dict = {}  # company_id -> entry

    for key, t in tenants.items():
        tenant_name = t["tenant_name"]
        expiry_iso = t["expiry"].isoformat()

        comp = comp_by_compact.get(key)
        tier = "auto"
        confidence = 1.0

        if comp is None:
            # Fuzzy tier — best ratio over normalized names.
            best, best_ratio = None, 0.0
            tnorm = normalize_company_name(tenant_name)
            for cnorm, c in comp_norm_list:
                if not cnorm:
                    continue
                ratio = SequenceMatcher(None, tnorm, cnorm).ratio()
                if ratio > best_ratio:
                    best, best_ratio = c, ratio
            if best is not None and best_ratio >= FUZZY_THRESHOLD:
                comp, tier, confidence = best, "review", round(best_ratio, 3)
            else:
                skipped_no_match.append(
                    {"tenant_name": tenant_name, "proposed_expiry": expiry_iso}
                )
                continue

        # Never overwrite an existing expiry (protects manual + prior imports).
        if _has_existing_expiry(comp):
            existing = (
                comp.lease_expiry_date.isoformat()
                if getattr(comp, "lease_expiry_date", None) else None
            )
            skipped_already_set[comp.company_id] = {
                "tenant_name": tenant_name,
                "company_id": comp.company_id,
                "company_name": comp.name,
                "existing_expiry": existing,
            }
            continue

        entry = {
            "tenant_name": tenant_name,
            "company_id": comp.company_id,
            "company_name": comp.name,
            "proposed_expiry": expiry_iso,
            "confidence": confidence,
        }
        bucket = auto if tier == "auto" else review
        cur = bucket.get(comp.company_id)
        # If two tenants resolve to one company, keep the soonest expiry.
        if cur is None or entry["proposed_expiry"] < cur["proposed_expiry"]:
            bucket[comp.company_id] = entry

    # A company landing in both buckets is auto (stronger) — drop its review entry.
    for cid in list(review.keys()):
        if cid in auto:
            del review[cid]

    return {
        "auto_applied": list(auto.values()),
        "needs_review": list(review.values()),
        "skipped_no_match": skipped_no_match,
        "skipped_already_set": list(skipped_already_set.values()),
    }


def apply_expiry_to_company(company, expiry_date: date, source: str = LEASE_SOURCE) -> None:
    """Write a lease expiry onto a Company (date + months + source + verified).

    Sets lease_expiry_months too because _run_signals scores off it. Caller is
    responsible for re-running signals and committing.
    """
    today = date.today()
    months = max(
        0, (expiry_date.year - today.year) * 12 + (expiry_date.month - today.month)
    )
    company.lease_expiry_date = expiry_date
    company.lease_expiry_months = months
    company.lease_expiry_source = source
    company.lease_expiry_last_verified = today


# ── Local PDF parsing (no network, no Claude, zero API credits) ────────────────
#
# A CoStar Lease Activity export is a sequence of lease "cards". The real
# pdfplumber text (verified against an actual export) looks like:
#
#     3,019 SF Sublet Lease - $31.50/SF FS Asking Rent
#     1750 Tysons Blvd, McLean, VA 22102
#     Move In May 11, 2026 Expiration September 1, 2027
#     Tenant Name Polysonics Size Occupied 3,019 SF
#
# So the field positions are:
#   - SF         : the number before " SF" on the header line  ("3,019 SF ...")
#   - Move In    : between "Move In " and " Expiration"         ("May 11, 2026")
#   - Expiration : everything after "Expiration " to EOL        ("September 1, 2027")
#   - Tenant Name: between "Tenant Name " and " Size Occupied"  ("Polysonics")
#
# We split the text on each "X,XXX SF" header line, then read those fields out of
# each card. A card with no "Tenant Name" is skipped — without a tenant name no
# match is possible. Pure string logic, unit-testable via parse_lease_text();
# only _extract_pdf_text() touches the file.

# Header line that opens a lease card: leading "X,XXX SF" (the rent's "$/SF"
# never appears at line start, so anchoring to ^ keeps the two apart). The
# Tenant Name line also contains "X,XXX SF" but starts with a word, not a digit.
_SF_HEADER_RE = re.compile(r"^\s*([\d,]+)\s+SF\b", re.IGNORECASE)

# A date token, longest/most-specific form first:
#   "September 1, 2027"  ->  Month D, YYYY
#   "5/1/2027"           ->  M/D/YYYY (or with dashes)
#   "September 2027"     ->  Month YYYY
_DATE_TOKEN_RE = re.compile(
    r"[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}"
    r"|\d{1,4}[/-]\d{1,2}[/-]\d{1,4}"
    r"|[A-Za-z]{3,9}\s+\d{4}"
)


def _find_label_value(lines: list, label: str) -> Optional[str]:
    """Return the text following `label` on the line it appears on.

    If the label line has nothing after the label, fall back to the next
    non-empty line (handles layouts where label and value wrap separately).
    Case-insensitive. Returns None when the label is absent.
    """
    pat = re.compile(rf"{re.escape(label)}\s*[:\-]?\s*(.*)", re.IGNORECASE)
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m:
            val = m.group(1).strip()
            if val:
                return val
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return nxt.strip()
            return None
    return None


def _cut_before(value: Optional[str], *markers: str) -> Optional[str]:
    """Trim `value` at the EARLIEST case-insensitive occurrence of any marker.

    Used to drop trailing same-line fields. The Tenant Name line's next field
    varies across CoStar exports — " Size Occupied", " Established",
    " Locations", " HQ City" — so pass them all and we cut at whichever appears
    first, e.g.:
      "Polysonics Size Occupied 3,019 SF"        -> "Polysonics"
      "Mosaic Spine and Knee Established 1996 ..." -> "Mosaic Spine and Knee"
      "May 11, 2026 Expiration September 1, 2027" -> "May 11, 2026"
    If no marker is present, the value is returned unchanged (handles
    single-field-per-line layouts too).
    """
    if not value:
        return value
    low = value.lower()
    positions = [low.find(m.lower()) for m in markers]
    positions = [p for p in positions if p != -1]
    if not positions:
        return value.strip()
    return value[:min(positions)].strip()


def _date_str_from(raw_value: Optional[str]) -> Optional[str]:
    """Extract the first date token from a label value, or None.

    parse_expiration() does the actual date coercion downstream; this just
    isolates the date so trailing words on the line don't trip it up.
    """
    if not raw_value:
        return None
    m = _DATE_TOKEN_RE.search(raw_value)
    return m.group(0) if m else raw_value.strip() or None


def parse_lease_text(text: str) -> list:
    """Parse raw extracted CoStar text into lease dicts. Pure — no I/O.

    Each returned dict: {tenant_name, sf, expiration_date, move_in_date}.
      - Splits into cards on each 'X,XXX SF' header line.
      - tenant_name: after "Tenant Name ", cut at the first trailing field
                     (" Size Occupied" / " Established" / " Locations" / " HQ City").
      - move_in    : between "Move In " and " Expiration".
      - expiration : after "Expiration " to end of line.
      - Cards with no Tenant Name are skipped (no match possible).
      - A card with no Expiration yields expiration_date=None (no error).
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()

    header_idxs = [i for i, ln in enumerate(lines) if _SF_HEADER_RE.match(ln)]
    if not header_idxs:
        return []

    leases = []
    for n, start in enumerate(header_idxs):
        end = header_idxs[n + 1] if n + 1 < len(header_idxs) else len(lines)
        card = lines[start:end]

        sf = None
        m = _SF_HEADER_RE.match(card[0])
        if m:
            try:
                sf = int(m.group(1).replace(",", ""))
            except ValueError:
                sf = None

        # Tenant Name: drop the trailing field that follows it. Which field that
        # is varies by export, so cut at whichever delimiter appears first.
        tenant_name = _cut_before(
            _find_label_value(card, "Tenant Name"),
            " Size Occupied", " Established", " Locations", " HQ City",
        )
        if not tenant_name:
            # No Tenant Name -> cannot match. Skip the card.
            continue

        # Move In and Expiration may share one line: cut Move In at " Expiration".
        move_in_raw = _cut_before(_find_label_value(card, "Move In"), " Expiration")
        expiration_raw = _find_label_value(card, "Expiration")

        leases.append({
            "tenant_name": tenant_name.strip(),
            "sf": sf,
            "expiration_date": _date_str_from(expiration_raw),
            "move_in_date": _date_str_from(move_in_raw),
        })
    return leases


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF page-by-page using pdfplumber (pure-Python).

    Raises LeaseParseError on any read failure so the route returns a clean 400.
    """
    import io

    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - dependency is in requirements
        raise LeaseParseError(f"PDF parsing library unavailable: {e}")

    try:
        parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as e:
        raise LeaseParseError(f"Could not read PDF: {e}")


def parse_lease_pdf(pdf_bytes: bytes) -> list:
    """Extract lease records from a CoStar Lease Activity PDF locally.

    Pulls text with pdfplumber, then parses the card structure via
    parse_lease_text. No API call, no key, zero credits. Raises LeaseParseError
    on an unreadable/empty PDF so the route surfaces a clean 400 not a 500.
    Each dict: {tenant_name, sf, expiration_date, move_in_date}.
    """
    if not pdf_bytes:
        raise LeaseParseError("Empty file.")
    text = _extract_pdf_text(pdf_bytes)
    if not text.strip():
        raise LeaseParseError("No extractable text found in the PDF.")
    return parse_lease_text(text)
