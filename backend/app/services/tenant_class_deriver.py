"""
Tenant Building Class Deriver
==============================
Auto-derives each tenant's current_building_class by matching their
current_address against a two-tier lookup:

  Tier 1 — CoStar lookup table (primary)
    ~1,800+ NoVA office properties loaded from costar_lookup/*.xlsx at
    module import.  The lookup dict is built once and reused across all
    runs.  If the directory is missing or files fail to load, a warning
    is logged and Tier 2 takes over.

  Tier 2 — Platform DB (fallback)
    The ~50-property platform universe, matched via SQLAlchemy session.

Both tiers apply the same confidence scoring:

  100  Single exact address match AND only one company at that address
       → auto-fill immediately.
   75  Single exact address match but multiple companies share the address
       (multi-tenant building) → log for manual review, do NOT auto-fill.
   50  Partial / fuzzy match: street part before first comma matches but
       the full address differs (zip / city differs) → log for manual
       review, do NOT auto-fill.
    0  No match in either tier → log as unmatched.

Feedback loop
-------------
The tenant_class_feedback table stores user corrections.  Before matching
any tenant the deriver checks this table — if the tenant's address already
has a feedback record the stored class is used directly (no re-matching,
no repeat mistakes).
"""

import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.property import Property

logger = logging.getLogger("deal_radar.pipeline")


# ── CoStar lookup helpers ─────────────────────────────────────────────────────

def _build_costar_lookup_from_dataframes(dataframes: list) -> dict:
    """
    Build a normalized {address: "Class X"} dict from a list of DataFrames.

    Each DataFrame must have 'Property Address' and 'Building Class' columns.
    CoStar exports single-letter class values ('A', 'B', 'C'); this function
    normalizes them to platform format ('Class A', 'Class B', 'Class C').

    First occurrence wins when the same address appears in multiple files —
    callers should pass files in deterministic order so deduplication is
    reproducible.

    Exported as a standalone function so tests can call it directly without
    touching the filesystem.
    """
    result: dict = {}
    for df in dataframes:
        for _, row in df.iterrows():
            addr = str(row.get("Property Address") or "").strip()
            cls  = str(row.get("Building Class")   or "").strip().upper()
            if not addr or cls not in ("A", "B", "C"):
                continue
            addr_norm = addr.lower()
            if addr_norm not in result:          # first occurrence wins
                result[addr_norm] = f"Class {cls}"
    return result


def _load_costar_lookup(lookup_dir: Optional[Path] = None) -> dict:
    """
    Load all CostarExport*.xlsx files from costar_lookup/ at the repo root.

    Path resolution order (when lookup_dir is not supplied):
      1. Four levels above this file  → always points at repo root regardless
         of working directory.
      2. One level above CWD          → handles the common case where the
         platform is launched from backend/ (CWD = backend/, parent = repo root).

    Pass lookup_dir to override both (used in tests against a tmp_path).

    Always prints to stdout in addition to the logger so the load summary is
    visible at startup even before the pipeline logger has handlers attached.
    Never raises.
    """
    try:
        import pandas as pd
    except ImportError:
        msg = "[TenantClassDeriver] pandas unavailable — CoStar lookup disabled"
        print(msg)
        logger.warning(msg)
        return {}

    if lookup_dir is None:
        # Primary: relative to this source file (correct in all deployment modes)
        file_based = (
            Path(__file__).resolve().parent.parent.parent.parent / "costar_lookup"
        )
        # Fallback: one level above CWD — covers the common launch pattern
        # `cd backend && uvicorn app.main:app` where CWD = backend/
        cwd_based = Path.cwd().parent / "costar_lookup"

        if file_based.exists():
            lookup_dir = file_based
        elif cwd_based.exists() and cwd_based.resolve() != file_based.resolve():
            lookup_dir = cwd_based
            msg = f"[TenantClassDeriver] Using CWD-parent path: '{cwd_based}'"
            print(msg)
            logger.info(msg)
        else:
            # Show both paths only when they differ (avoids duplicate noise)
            if file_based.resolve() == cwd_based.resolve():
                checked = str(file_based)
            else:
                checked = f"'{file_based}' and '{cwd_based}'"
            msg = (
                f"[TenantClassDeriver] costar_lookup/ not found — "
                f"checked {checked} — "
                "address matching will use platform DB only"
            )
            print(msg)
            logger.warning(msg)
            return {}

    xlsx_files = sorted(lookup_dir.glob("CostarExport*.xlsx"))
    if not xlsx_files:
        msg = (
            f"[TenantClassDeriver] No CostarExport*.xlsx files in '{lookup_dir}' — "
            "address matching will use platform DB only"
        )
        print(msg)
        logger.warning(msg)
        return {}

    dataframes: list = []
    failed = 0
    for path in xlsx_files:
        try:
            df = pd.read_excel(path, usecols=["Property Address", "Building Class"])
            dataframes.append(df)
        except Exception as exc:
            warn = f"[TenantClassDeriver] Could not load {path.name}: {exc}"
            print(warn)
            logger.warning(warn)
            failed += 1

    if not dataframes:
        msg = (
            f"[TenantClassDeriver] All {failed} CoStar file(s) failed to load — "
            "falling back to platform DB only"
        )
        print(msg)
        logger.warning(msg)
        return {}

    result = _build_costar_lookup_from_dataframes(dataframes)
    msg = (
        f"[TenantClassDeriver] CoStar lookup ready: "
        f"path='{lookup_dir}' "
        f"xlsx_files_found={len(xlsx_files)} "
        f"files_loaded={len(dataframes)} "
        f"files_skipped={failed} "
        f"addresses={len(result)}"
    )
    print(msg)
    logger.info(msg)
    return result


# Built once at module import — shared by all callers.
# In production this is populated from the xlsx files.
# In tests _COSTAR_LOOKUP == {} (no files) and tests inject their own dict.
_COSTAR_LOOKUP: dict = _load_costar_lookup()


# ── Address normalisation ─────────────────────────────────────────────────────

def _norm(addr: str) -> str:
    return addr.strip().lower()


# ── Core matching ─────────────────────────────────────────────────────────────

def match_address_to_property(
    current_address: str,
    db: Session,
    *,
    _property_map: Optional[dict] = None,
    _company_addr_counts: Optional[dict] = None,
    _costar_lookup: Optional[dict] = None,
) -> Optional[dict]:
    """
    Two-tier address matching.  Returns a result dict or None (confidence 0).

    Result shape: {"matched_class": str, "confidence": int,
                   "property_id": int|None, "debug_info": str}

    property_id is set for platform-DB matches; None for CoStar-only matches.

    Pass pre-built _property_map / _company_addr_counts when processing many
    tenants in a batch so those maps are built once, not once per tenant.

    Pass _costar_lookup to override the module-level dict (used by tests to
    inject mock data without touching the filesystem).
    """
    if not current_address or not current_address.strip():
        return None

    addr_norm = _norm(current_address)

    # Resolve CoStar lookup (test injection or module-level)
    costar_lkp: dict = _COSTAR_LOOKUP if _costar_lookup is None else _costar_lookup

    # Build DB maps if not pre-supplied (standalone / single-call path)
    if _property_map is None:
        _property_map = {
            _norm(p.address): p
            for p in db.query(Property).all()
            if p.address
        }
    if _company_addr_counts is None:
        _company_addr_counts = Counter(
            _norm(c.current_address)
            for c in db.query(Company).all()
            if c.current_address
        )

    street_part = addr_norm.split(",")[0].strip()

    # ── Tier 1: CoStar lookup (primary) ──────────────────────────────────
    if addr_norm in costar_lkp:
        matched_class = costar_lkp[addr_norm]
        count_at_addr = _company_addr_counts.get(addr_norm, 1)
        if count_at_addr > 1:
            return {
                "matched_class": matched_class,
                "confidence": 75,
                "property_id": None,
                "debug_info": (
                    f"CoStar exact match ({matched_class}) but {count_at_addr} "
                    "companies share this address — multi-tenant, manual review needed"
                ),
            }
        return {
            "matched_class": matched_class,
            "confidence": 100,
            "property_id": None,
            "debug_info": f"CoStar exact match → {matched_class}",
        }

    if street_part:
        for costar_addr, cls in costar_lkp.items():
            if costar_addr.split(",")[0].strip() == street_part:
                return {
                    "matched_class": cls,
                    "confidence": 50,
                    "property_id": None,
                    "debug_info": (
                        f"CoStar partial match (street matches, zip/city differs): "
                        f"'{current_address}' → '{costar_addr}'"
                    ),
                }

    # ── Tier 2: Platform DB (fallback) ────────────────────────────────────
    if addr_norm in _property_map:
        prop = _property_map[addr_norm]
        count_at_addr = _company_addr_counts.get(addr_norm, 1)
        if count_at_addr > 1:
            return {
                "matched_class": prop.asset_class,
                "confidence": 75,
                "property_id": prop.id,
                "debug_info": (
                    f"DB exact match to '{prop.address}' ({prop.asset_class}) but "
                    f"{count_at_addr} companies share this address — "
                    "multi-tenant building, manual review needed"
                ),
            }
        return {
            "matched_class": prop.asset_class,
            "confidence": 100,
            "property_id": prop.id,
            "debug_info": f"DB exact match → '{prop.address}' ({prop.asset_class})",
        }

    if street_part:
        for prop_addr_norm, prop in _property_map.items():
            if prop_addr_norm.split(",")[0].strip() == street_part:
                return {
                    "matched_class": prop.asset_class,
                    "confidence": 50,
                    "property_id": prop.id,
                    "debug_info": (
                        f"DB partial match (street matches, zip/city differs): "
                        f"'{current_address}' → '{prop.address}'"
                    ),
                }

    return None  # confidence 0 — no match in either tier


# ── Feedback helper ───────────────────────────────────────────────────────────

def record_building_class_feedback(
    company: Company,
    user_corrected_class: Optional[str],
    db: Session,
) -> None:
    """
    Called after a user edits a company's building class via the PATCH endpoint.

    Determines what the deriver would have inferred for this address, then
    writes a TenantClassFeedback row if the user's value differs. This entry
    becomes the ground truth for all future deriver runs on this address.

    Silently no-ops on blank address, missing table, or any other error —
    the PATCH endpoint must never fail due to feedback logging.
    """
    if not company.current_address:
        return

    inferred_class: Optional[str] = None
    try:
        result = match_address_to_property(company.current_address, db)
        if result and result["confidence"] == 100:
            inferred_class = result["matched_class"]
    except Exception as exc:
        logger.warning(
            "[TenantClassDeriver] match failed during feedback: %s", exc
        )

    if user_corrected_class == inferred_class:
        return  # user confirmed the deriver's guess — no correction to record

    try:
        from app.models.tenant_class_feedback import TenantClassFeedback
        db.add(TenantClassFeedback(
            company_id=company.id,
            current_address=company.current_address,
            inferred_class=inferred_class,
            user_corrected_class=user_corrected_class,
        ))
        db.commit()
        logger.info(
            "[TenantClassDeriver] Feedback recorded: '%s' inferred=%s → user=%s",
            company.current_address, inferred_class, user_corrected_class,
        )
    except Exception as exc:
        db.rollback()
        logger.warning("[TenantClassDeriver] Feedback write failed: %s", exc)


# ── Main deriver ──────────────────────────────────────────────────────────────

def derive_tenant_building_classes(
    db: Session,
    backfill: bool = False,
    dry_run: bool = False,
    _costar_lookup: Optional[dict] = None,
) -> dict:
    """
    Derive building classes for tenants via two-tier address matching, then
    apply confidence-gated auto-fill rules.

    backfill=False   Process only tenants with current_building_class IS NULL
                     (daily 6 am pipeline mode — new tenants only).
    backfill=True    Process ALL tenants (one-time 395-tenant catch-up).
    dry_run=True     Compute results but do NOT persist any DB changes.
    _costar_lookup   Override the module-level CoStar dict (test injection).

    Returns the admin endpoint JSON contract:
    {
        "total_processed": int,
        "auto_filled_100_confidence": int,
        "logged_75_confidence": [{"company_id", "name", "address",
                                   "matched_class", "reason"}],
        "logged_50_confidence": [...],
        "unmatched": [{"company_id", "name", "address"}],
        "feedback_hits": int,
    }
    """
    # Resolve CoStar lookup once for the whole batch
    costar_lkp: dict = _COSTAR_LOOKUP if _costar_lookup is None else _costar_lookup

    # ── O(n) lookup structures — built once, threaded through every call ──
    all_properties = db.query(Property).all()
    property_map = {_norm(p.address): p for p in all_properties if p.address}

    all_companies = db.query(Company).all()
    company_addr_counts = Counter(
        _norm(c.current_address) for c in all_companies if c.current_address
    )

    # ── Load feedback table (gracefully handles missing table on first run) ─
    feedback_map: dict = {}
    try:
        from app.models.tenant_class_feedback import TenantClassFeedback
        for row in db.query(TenantClassFeedback).all():
            if row.current_address:
                feedback_map[_norm(row.current_address)] = row.user_corrected_class
    except Exception as exc:
        logger.warning(
            "[TenantClassDeriver] Could not load feedback table (first run?): %s",
            exc,
        )

    # ── Select tenants ────────────────────────────────────────────────────
    tenants = all_companies if backfill else [
        c for c in all_companies if c.current_building_class is None
    ]

    # ── Per-tenant matching ───────────────────────────────────────────────
    total_processed = 0
    auto_filled     = 0
    logged_75: list = []
    logged_50: list = []
    unmatched: list = []
    feedback_hits   = 0

    for tenant in tenants:
        if not tenant.current_address or not tenant.current_address.strip():
            continue  # null / blank address — skip gracefully

        total_processed += 1
        addr_norm = _norm(tenant.current_address)

        # Feedback takes priority — prevents repeat mistakes
        if addr_norm in feedback_map:
            corrected = feedback_map[addr_norm]
            logger.info(
                "[TenantClassDeriver] Feedback hit: %s ('%s') → %s",
                tenant.name, tenant.current_address, corrected,
            )
            if not dry_run:
                tenant.current_building_class = corrected
            feedback_hits += 1
            continue

        result = match_address_to_property(
            tenant.current_address,
            db,
            _property_map=property_map,
            _company_addr_counts=company_addr_counts,
            _costar_lookup=costar_lkp,
        )

        if result is None:
            logger.info(
                "[TenantClassDeriver] No match (conf 0): %s — '%s'",
                tenant.name, tenant.current_address,
            )
            unmatched.append({
                "company_id": tenant.company_id,
                "name":       tenant.name,
                "address":    tenant.current_address,
            })

        elif result["confidence"] == 100:
            logger.info(
                "[TenantClassDeriver] Auto-fill (conf 100): %s → %s",
                tenant.name, result["matched_class"],
            )
            if not dry_run:
                tenant.current_building_class = result["matched_class"]
            auto_filled += 1

        elif result["confidence"] == 75:
            logger.info(
                "[TenantClassDeriver] Manual review (conf 75): %s — %s",
                tenant.name, result["debug_info"],
            )
            logged_75.append({
                "company_id":   tenant.company_id,
                "name":         tenant.name,
                "address":      tenant.current_address,
                "matched_class": result["matched_class"],
                "reason":       result["debug_info"],
            })

        elif result["confidence"] == 50:
            logger.info(
                "[TenantClassDeriver] Manual review (conf 50): %s — %s",
                tenant.name, result["debug_info"],
            )
            logged_50.append({
                "company_id":   tenant.company_id,
                "name":         tenant.name,
                "address":      tenant.current_address,
                "matched_class": result["matched_class"],
                "reason":       result["debug_info"],
            })

    if not dry_run:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("[TenantClassDeriver] DB commit failed: %s", exc)
            raise

    summary = {
        "total_processed":          total_processed,
        "auto_filled_100_confidence": auto_filled,
        "logged_75_confidence":     logged_75,
        "logged_50_confidence":     logged_50,
        "unmatched":                unmatched,
        "feedback_hits":            feedback_hits,
    }

    logger.info(
        "[TenantClassDeriver] Complete (backfill=%s dry_run=%s) — "
        "processed=%d auto_filled=%d conf75=%d conf50=%d "
        "unmatched=%d feedback_hits=%d",
        backfill, dry_run,
        total_processed, auto_filled,
        len(logged_75), len(logged_50),
        len(unmatched), feedback_hits,
    )

    return summary
