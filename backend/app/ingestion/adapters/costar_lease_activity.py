"""
CoStar Lease Activity adapter — updates in_place_rent_psf on matched properties.

Exact CoStar Lease Activity export column names (verified from actual export):
  "Property Address" — street address of the property
  "Rent/SF/Yr"       — actual signed lease rent (NOT asking rent)
  "Lease Signed Date"— date lease was executed
  "Space Use"        — filter to "Office" only
  "Submarket Name"   — for logging
"""
import re
import io
from datetime import date
from typing import Optional


# Patterns for suite/unit stripping (applied before fuzzy match)
_SUITE_RE = re.compile(
    r"\b(suite|ste|unit|floor|fl|#|apt|room|rm)\s*[#]?\s*[\w-]+",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SPACE_RE = re.compile(r"\s+")


def _normalize_address(addr: str) -> str:
    """Lowercase, strip suite/unit tokens, remove punctuation, collapse spaces."""
    addr = addr.lower()
    addr = _SUITE_RE.sub(" ", addr)
    addr = _PUNCT_RE.sub(" ", addr)
    addr = _SPACE_RE.sub(" ", addr).strip()
    return addr


def _addr_match(costar_norm: str, db_norm: str) -> bool:
    """True if one address string contains the other (bidirectional contains)."""
    return costar_norm in db_norm or db_norm in costar_norm


def run_costar_lease_activity_import(file_bytes: bytes, filename: str, db) -> dict:
    """
    Load a CoStar Lease Activity export, match properties by address, and
    update in_place_rent_psf for properties that currently have no rent data.

    Returns:
        {"updated": N, "skipped_no_match": N, "skipped_existing": N, "errors": []}
    """
    import pandas as pd
    from app.models.property import Property

    try:
        fname = filename.lower()
        if fname.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(
                io.BytesIO(file_bytes), dtype=str, keep_default_na=False, engine="openpyxl"
            )
    except Exception as exc:
        return {"updated": 0, "skipped_no_match": 0, "skipped_existing": 0, "errors": [str(exc)]}

    df.columns = [c.strip() for c in df.columns]
    df = df.replace("", None)

    # Filter to Office rows only
    if "Space Use" in df.columns:
        df = df[df["Space Use"].str.strip().str.lower() == "office"]

    rows = df.to_dict(orient="records")

    updated = 0
    skipped_no_match = 0
    skipped_existing = 0
    errors: list = []

    # Build normalized address index of DB properties
    all_props = db.query(Property).all()
    prop_index: list[tuple[str, Property]] = [
        (_normalize_address(p.address or ""), p)
        for p in all_props
        if p.address
    ]

    # Collect best rent-per-property (most recent Lease Signed Date wins)
    best: dict[int, tuple[date, float]] = {}  # property.id → (signed_date, rent)

    for row in rows:
        raw_addr = (row.get("Property Address") or "").strip()
        raw_rent = row.get("Rent/SF/Yr") or row.get("Rent/SF/yr") or row.get("Rent/SF/YR")
        raw_date = row.get("Lease Signed Date") or row.get("Lease Signed date")

        if not raw_addr or not raw_rent:
            continue

        # Parse rent
        try:
            rent = float(str(raw_rent).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        if rent <= 0:
            continue

        # Parse signed date
        signed_date: Optional[date] = None
        if raw_date:
            try:
                from dateutil import parser as _dp
                signed_date = _dp.parse(str(raw_date), fuzzy=True).date()
            except Exception:
                signed_date = None

        # Match against DB properties
        cs_norm = _normalize_address(raw_addr)
        matched_prop: Optional[Property] = None
        for db_norm, prop in prop_index:
            if _addr_match(cs_norm, db_norm):
                matched_prop = prop
                break

        if not matched_prop:
            skipped_no_match += 1
            continue

        # Keep only the most recent lease comp per property
        pid = matched_prop.id
        if pid not in best:
            best[pid] = (signed_date or date.min, rent)
        else:
            existing_date, _ = best[pid]
            if (signed_date or date.min) >= existing_date:
                best[pid] = (signed_date or date.min, rent)

    # Apply updates
    for pid, (_, rent) in best.items():
        prop = db.query(Property).filter(Property.id == pid).first()
        if not prop:
            continue
        if prop.in_place_rent_psf and prop.in_place_rent_psf > 0:
            skipped_existing += 1
            continue
        prop.in_place_rent_psf = rent
        prop.in_place_rent_source = "costar_lease_activity"
        updated += 1

    db.commit()

    return {
        "updated":          updated,
        "skipped_no_match": skipped_no_match,
        "skipped_existing": skipped_existing,
        "errors":           errors,
    }
