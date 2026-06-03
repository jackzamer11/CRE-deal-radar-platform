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
  - parse_lease_pdf                        : the ONLY side-effecting piece — sends
                                            the PDF to Claude and returns parsed
                                            lease dicts. Isolated so tests mock it.

Tiered matching contract:
  (a) EXACT match after normalization (case, punctuation, legal suffixes,
      internal spaces removed) → auto-apply.
  (b) FUZZY close match (SequenceMatcher ratio >= FUZZY_THRESHOLD) → needs review.
  (c) No match → skipped-no-match.

Protection: a Company that already has ANY lease expiry value is NEVER
overwritten — it is reported as skipped-already-set. When one tenant appears on
multiple leases, the SOONEST expiration wins.
"""
import os
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
    # Try ISO first, then a couple of common CoStar formats.
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %Y", "%B %Y", "%m/%Y"):
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


# ── PDF parsing via Claude (the only side-effecting piece) ─────────────────────

_PDF_PROMPT = (
    "This is a CoStar Lease Activity export. Each lease is a card with a header "
    "line (e.g. '2,593 SF Sublet Lease - $31.50/SF FS Asking Rent'), an address "
    "line, then label/value pairs for 'Move In', 'Expiration', and 'Space Use'. "
    "The tenant company name appears in the header/address area of each card.\n\n"
    "Extract EVERY lease. Return ONLY a JSON array (no prose, no code fences) of "
    "objects with exactly these keys:\n"
    '  "tenant_name"     : string  (the tenant company name)\n'
    '  "sf"              : integer or null (square footage)\n'
    '  "expiration_date" : string "YYYY-MM-DD" or null if no Expiration is shown\n'
    '  "move_in_date"    : string "YYYY-MM-DD" or null\n\n'
    "If a field is absent, use null — never invent a value. If a card has no "
    "Expiration, set expiration_date to null."
)


def parse_lease_pdf(pdf_bytes: bytes) -> list:
    """Send a PDF to Claude and return a list of parsed lease dicts.

    Each dict: {tenant_name, sf, expiration_date, move_in_date}.
    Raises LeaseParseError on any failure (missing key, API error, bad JSON) so
    the route can surface a clean 400 instead of a 500.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LeaseParseError("ANTHROPIC_API_KEY is not set on the server.")
    if not pdf_bytes:
        raise LeaseParseError("Empty file.")

    import base64
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PDF_PROMPT},
                ],
            }],
        )
    except Exception as e:  # anthropic.APIError and friends
        raise LeaseParseError(f"Claude request failed: {e}")

    text = "".join(
        getattr(b, "text", "") for b in resp.content if hasattr(b, "text")
    ).strip()
    return _parse_json_leases(text)


def _parse_json_leases(text: str) -> list:
    """Tolerantly pull a JSON array of lease objects out of Claude's response."""
    import json

    if not text:
        raise LeaseParseError("Claude returned an empty response.")

    # Strip ```json fences if present.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # Fall back to slicing the outermost [ ... ] if there's surrounding prose.
    if not cleaned.startswith("["):
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise LeaseParseError("No JSON array found in Claude's response.")
        cleaned = cleaned[start:end + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LeaseParseError(f"Could not decode lease JSON: {e}")

    if not isinstance(data, list):
        raise LeaseParseError("Expected a JSON array of leases.")

    leases = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("tenant_name")
        if not name or not str(name).strip():
            continue
        leases.append({
            "tenant_name": str(name).strip(),
            "sf": item.get("sf"),
            "expiration_date": item.get("expiration_date"),
            "move_in_date": item.get("move_in_date"),
        })
    return leases
