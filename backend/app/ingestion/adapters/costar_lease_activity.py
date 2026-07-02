"""
CoStar Lease Activity adapter — updates in_place_rent_psf on matched properties
and effective_rent_psf on name-matched tenant companies.

Exact CoStar Lease Activity export column names (verified from actual export):
  "Property Address" — street address of the property
  "Rent/SF/Yr"       — actual signed lease rent (NOT asking rent)
  "Lease Signed Date"— date lease was executed
  "Space Use"        — filter to "Office" only
  "Submarket Name"   — for logging

Tenant effective-rent pass (optional columns — the pass is skipped when absent):
  "Tenant Name"              — matched to Company by NAME (exact after
                               normalization only; no fuzzy guessing)
  "Effective Rent (Annual)"  — $/SF, stored on effective_rent_psf AS-IS
                               (no escalation or vintage adjustment)
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


def _parse_psf(raw) -> "Optional[float]":
    """Parse a $/SF cell ("$32.50", "32.50/SF FS", "1,234.00") to float, or None."""
    if raw is None:
        return None
    cleaned = (
        str(raw)
        .replace("$", "")
        .replace(",", "")
        .replace("/SF", "")
        .replace("/sf", "")
        .replace("FS", "")
        .strip()
    )
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None


def run_costar_lease_activity_import(file_bytes: bytes, filename: str, db) -> dict:
    """
    Load a CoStar Lease Activity export, match properties by address, and
    update in_place_rent_psf for properties that currently have no rent data.

    Additionally name-matches each row's "Tenant Name" to a Company and stores
    "Effective Rent (Annual)" $/SF on effective_rent_psf, as-is. Exact
    normalized name match only — unmatched rows are reported, never guessed.

    Returns:
        {"updated": N, "skipped_no_match": N, "skipped_existing": N, "errors": [],
         "tenants_matched": N, "tenants_skipped": N,
         "tenant_skips": [{"tenant_name": ..., "reason": ...}]}
    """
    import pandas as pd
    from app.models.property import Property

    _empty = {
        "updated": 0, "skipped_no_match": 0, "skipped_existing": 0,
        "tenants_matched": 0, "tenants_skipped": 0, "tenant_skips": [],
    }
    try:
        fname = filename.lower()
        if fname.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(
                io.BytesIO(file_bytes), dtype=str, keep_default_na=False, engine="openpyxl"
            )
    except Exception as exc:
        return {**_empty, "errors": [str(exc)]}

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

    # ── Tenant effective-rent pass setup ───────────────────────────────────
    # Name-match "Tenant Name" → Company (exact after normalization — the same
    # canonicalization the Lease Comps import uses; no fuzzy guessing) and
    # store "Effective Rent (Annual)" on effective_rent_psf as-is.
    from app.models.company import Company
    from app.services.lease_comps_service import _compact_key

    has_tenant_cols = "Tenant Name" in df.columns and "Effective Rent (Annual)" in df.columns
    comp_by_key: dict = {}
    if has_tenant_cols:
        for c in db.query(Company).all():
            key = _compact_key(c.name)
            if key and key not in comp_by_key:
                comp_by_key[key] = c

    tenant_skips: list[dict] = []      # {"tenant_name": ..., "reason": ...}
    # company.id → (signed_date, effective_rent, tenant_name); latest signing wins
    best_tenant: dict[int, tuple[date, float, str]] = {}

    # Collect best rent-per-property (most recent Lease Signed Date wins)
    best: dict[int, tuple[date, float]] = {}  # property.id → (signed_date, rent)

    for row in rows:
        raw_addr = (row.get("Property Address") or "").strip()
        raw_rent = row.get("Rent/SF/Yr") or row.get("Rent/SF/yr") or row.get("Rent/SF/YR")
        raw_date = row.get("Lease Signed Date") or row.get("Lease Signed date")

        # Parse signed date once — shared by the property and tenant passes.
        row_signed_date: Optional[date] = None
        if raw_date:
            try:
                from dateutil import parser as _dp
                row_signed_date = _dp.parse(str(raw_date), fuzzy=True).date()
            except Exception:
                row_signed_date = None

        # ── Tenant effective-rent pass (runs on every Office row) ───────────
        if has_tenant_cols:
            tenant_name = (row.get("Tenant Name") or "").strip()
            eff_rent = _parse_psf(row.get("Effective Rent (Annual)"))
            if not tenant_name:
                if row.get("Effective Rent (Annual)"):
                    tenant_skips.append({
                        "tenant_name": "(blank)",
                        "reason": "missing Tenant Name",
                    })
            elif eff_rent is None:
                tenant_skips.append({
                    "tenant_name": tenant_name,
                    "reason": "missing/unparseable Effective Rent (Annual)",
                })
            else:
                comp = comp_by_key.get(_compact_key(tenant_name))
                if comp is None:
                    tenant_skips.append({
                        "tenant_name": tenant_name,
                        "reason": "no matching tenant company by name",
                    })
                else:
                    cur = best_tenant.get(comp.id)
                    if cur is None or (row_signed_date or date.min) >= cur[0]:
                        best_tenant[comp.id] = (row_signed_date or date.min, eff_rent, tenant_name)

        if not raw_addr or not raw_rent:
            continue

        # Parse rent
        try:
            rent = float(str(raw_rent).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        if rent <= 0:
            continue

        signed_date = row_signed_date

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

    # ── Apply tenant effective rents (as-is; never overwrite an existing value) ──
    tenants_matched = 0
    for cid, (_, eff_rent, tenant_name) in best_tenant.items():
        comp = db.query(Company).filter(Company.id == cid).first()
        if comp is None:
            continue
        if comp.effective_rent_psf is not None:
            tenant_skips.append({
                "tenant_name": tenant_name,
                "reason": "effective_rent_psf already set — not overwritten",
            })
            continue
        comp.effective_rent_psf = eff_rent
        tenants_matched += 1

    db.commit()

    # Summary: N matched, N skipped, skipped names with reasons.
    if has_tenant_cols:
        print(
            f"[lease-activity] effective rents: {tenants_matched} matched, "
            f"{len(tenant_skips)} skipped"
        )
        for s in tenant_skips:
            print(f"  - skipped {s['tenant_name']}: {s['reason']}")
    else:
        print(
            "[lease-activity] no 'Tenant Name' + 'Effective Rent (Annual)' columns "
            "in this export — tenant effective-rent pass skipped"
        )

    return {
        "updated":          updated,
        "skipped_no_match": skipped_no_match,
        "skipped_existing": skipped_existing,
        "errors":           errors,
        "tenants_matched":  tenants_matched,
        "tenants_skipped":  len(tenant_skips),
        "tenant_skips":     tenant_skips,
    }
