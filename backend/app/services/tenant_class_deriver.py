"""
Tenant Building Class Deriver
==============================
Auto-derives each tenant's current_building_class by matching their
current_address against the platform's property universe.

Runs after county enrichment and before signal recalculation in the
daily 6 am pipeline (backfill=False, new/unclassified tenants only).
One-time catch-up via the admin backfill endpoint uses backfill=True.

Confidence tiers
----------------
100  Exactly one property matches the address AND only one company is at
     that address → auto-fill immediately.
 75  Exactly one property matches but multiple companies share the address
     (multi-tenant building) → log for manual review, do NOT auto-fill.
 50  Partial / fuzzy match: street part before first comma matches but
     the full address string differs (zip / city differs) → log for
     manual review, do NOT auto-fill.
  0  No match → log tenant name + address as unmatched, do NOT auto-fill.

Feedback loop
-------------
The tenant_class_feedback table stores user corrections (address →
user_corrected_class). Before matching any tenant, the deriver checks
this table. If the tenant's address has a feedback record, the stored
class is used directly — no re-matching, no repeat mistakes.
"""

import logging
from collections import Counter
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.property import Property

logger = logging.getLogger("deal_radar.pipeline")


def _norm(addr: str) -> str:
    return addr.strip().lower()


def match_address_to_property(
    current_address: str,
    db: Session,
    *,
    _property_map: Optional[dict] = None,
    _company_addr_counts: Optional[dict] = None,
) -> Optional[dict]:
    """
    Match a single company address against the property universe.

    Returns {"matched_class", "confidence", "property_id", "debug_info"}
    or None when confidence is 0 (no match found).

    Pass pre-built _property_map / _company_addr_counts for O(1) lookups
    when processing many tenants in a batch (the deriver builds these once
    and threads them through every call).
    """
    if not current_address or not current_address.strip():
        return None

    addr_norm = _norm(current_address)

    # Build maps if not supplied (standalone / test call)
    if _property_map is None:
        props = db.query(Property).all()
        _property_map = {_norm(p.address): p for p in props if p.address}

    if _company_addr_counts is None:
        companies = db.query(Company).all()
        _company_addr_counts = Counter(
            _norm(c.current_address)
            for c in companies
            if c.current_address
        )

    # ── Exact match ───────────────────────────────────────────────────────
    if addr_norm in _property_map:
        prop = _property_map[addr_norm]
        count_at_addr = _company_addr_counts.get(addr_norm, 1)
        if count_at_addr > 1:
            return {
                "matched_class": prop.asset_class,
                "confidence": 75,
                "property_id": prop.id,
                "debug_info": (
                    f"Exact match to '{prop.address}' ({prop.asset_class}) but "
                    f"{count_at_addr} companies share this address — "
                    "multi-tenant building, manual review needed"
                ),
            }
        return {
            "matched_class": prop.asset_class,
            "confidence": 100,
            "property_id": prop.id,
            "debug_info": f"Exact match → '{prop.address}' ({prop.asset_class})",
        }

    # ── Partial / fuzzy: street number + name before first comma ─────────
    street_part = addr_norm.split(",")[0].strip()
    if street_part:
        for prop_addr_norm, prop in _property_map.items():
            prop_street = prop_addr_norm.split(",")[0].strip()
            if street_part == prop_street:
                return {
                    "matched_class": prop.asset_class,
                    "confidence": 50,
                    "property_id": prop.id,
                    "debug_info": (
                        f"Partial match (street matches, zip/city differs): "
                        f"'{current_address}' → '{prop.address}'"
                    ),
                }

    return None  # confidence 0 — no match


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

    Silently no-ops if the address is blank, the table is missing, or any
    other error occurs — the PATCH endpoint must never fail due to feedback.
    """
    if not company.current_address:
        return

    inferred_class: Optional[str] = None
    try:
        result = match_address_to_property(company.current_address, db)
        if result and result["confidence"] == 100:
            inferred_class = result["matched_class"]
    except Exception as exc:
        logger.warning("[TenantClassDeriver] match failed during feedback: %s", exc)

    if user_corrected_class == inferred_class:
        return  # user confirmed the deriver's guess — no correction to record

    try:
        from app.models.tenant_class_feedback import TenantClassFeedback
        feedback = TenantClassFeedback(
            company_id=company.id,
            current_address=company.current_address,
            inferred_class=inferred_class,
            user_corrected_class=user_corrected_class,
        )
        db.add(feedback)
        db.commit()
        logger.info(
            "[TenantClassDeriver] Feedback recorded: '%s' inferred=%s → user=%s",
            company.current_address, inferred_class, user_corrected_class,
        )
    except Exception as exc:
        db.rollback()
        logger.warning("[TenantClassDeriver] Feedback write failed: %s", exc)


def derive_tenant_building_classes(
    db: Session,
    backfill: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Derive building classes for tenants by matching their address to the
    property universe, then apply confidence-gated auto-fill rules.

    backfill=False  Process only tenants with current_building_class IS NULL.
                    This is the daily 6 am mode — touches only new tenants.
    backfill=True   Process ALL tenants (one-time catch-up on 395 existing).
    dry_run=True    Compute the preview but do NOT persist any changes.

    Returns the admin endpoint JSON contract:
    {
        "total_processed": int,
        "auto_filled_100_confidence": int,
        "logged_75_confidence": [{"company_id", "name", "address", "matched_class", "reason"}],
        "logged_50_confidence": [...],
        "unmatched": [{"company_id", "name", "address"}],
        "feedback_hits": int,
    }
    """
    # ── O(n) lookup structures — built once, used for every tenant ────────
    all_properties = db.query(Property).all()
    property_map = {_norm(p.address): p for p in all_properties if p.address}

    all_companies = db.query(Company).all()
    company_addr_counts = Counter(
        _norm(c.current_address) for c in all_companies if c.current_address
    )

    # ── Load feedback table (gracefully handle missing table on first run) ─
    feedback_map: dict = {}
    try:
        from app.models.tenant_class_feedback import TenantClassFeedback
        for row in db.query(TenantClassFeedback).all():
            if row.current_address:
                feedback_map[_norm(row.current_address)] = row.user_corrected_class
    except Exception as exc:
        logger.warning(
            "[TenantClassDeriver] Could not load feedback table (first run?): %s", exc
        )

    # ── Select tenants to process ──────────────────────────────────────────
    if backfill:
        tenants = all_companies
    else:
        tenants = [c for c in all_companies if c.current_building_class is None]

    # ── Per-tenant matching ────────────────────────────────────────────────
    total_processed = 0
    auto_filled = 0
    logged_75: list = []
    logged_50: list = []
    unmatched: list = []
    feedback_hits = 0

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
        )

        if result is None:
            logger.info(
                "[TenantClassDeriver] No match (conf 0): %s — '%s'",
                tenant.name, tenant.current_address,
            )
            unmatched.append({
                "company_id": tenant.company_id,
                "name": tenant.name,
                "address": tenant.current_address,
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
                "company_id": tenant.company_id,
                "name": tenant.name,
                "address": tenant.current_address,
                "matched_class": result["matched_class"],
                "reason": result["debug_info"],
            })

        elif result["confidence"] == 50:
            logger.info(
                "[TenantClassDeriver] Manual review (conf 50): %s — %s",
                tenant.name, result["debug_info"],
            )
            logged_50.append({
                "company_id": tenant.company_id,
                "name": tenant.name,
                "address": tenant.current_address,
                "matched_class": result["matched_class"],
                "reason": result["debug_info"],
            })

    if not dry_run:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("[TenantClassDeriver] DB commit failed: %s", exc)
            raise

    summary = {
        "total_processed": total_processed,
        "auto_filled_100_confidence": auto_filled,
        "logged_75_confidence": logged_75,
        "logged_50_confidence": logged_50,
        "unmatched": unmatched,
        "feedback_hits": feedback_hits,
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
