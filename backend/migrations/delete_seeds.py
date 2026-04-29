"""
Delete Seed Data Migration
===========================
Removes the 15 bootstrap seed properties and 8 seed companies inserted by
seed_data.py, along with all their dependent records.

Seed identification uses EXACT address / company name matches hardcoded from
seed_data.py — NOT property_id/company_id prefix patterns, which also appear
on real CoStar-imported records and would cause mass data loss.

Usage:
  Dry run (default):  python -m migrations.delete_seeds
  Actually delete:    python -m migrations.delete_seeds --confirm

User-edit preservation (seeds excluded from deletion):
  Companies: lease_expiry_last_verified IS NOT NULL
          OR lease_expiry_source = 'manual'
          OR any user-created ActivityLog exists (created_by != 'system')
  Properties: any user-created ActivityLog exists (created_by != 'system')
  Opportunities: stage not in ('IDENTIFIED',) — user advanced the deal

Cascade deletion order:
  1. ActivityLogs linked to deletable Opportunities
  2. ActivityLogs directly on deletable Properties
  3. ActivityLogs directly on deletable Companies
  4. Detach preserved Opportunities (null their FK to deleted entity)
  5. Delete deletable Opportunities
  6. Delete Companies
  7. Delete Properties

Post-deletion: automatically re-runs signals + deal creation engine.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import or_

from app.database import init_db, SessionLocal
from app.models.property import Property
from app.models.company import Company
from app.models.opportunity import Opportunity
from app.models.activity import ActivityLog


# ── Exact seed record identifiers (sourced directly from seed_data.py) ───────
# These are the only records that should ever be matched for deletion.
# Add or remove entries here if seed_data.py changes in the future.

SEED_PROPERTY_ADDRESSES: set[str] = {
    "1760 Reston Pkwy, Suite 200, Reston, VA 20190",
    "10600 Sunset Hills Rd, Suite 100, Reston, VA 20190",
    "1875 Explorer St, Suite 400, Reston Town Center, VA 20190",
    "8300 Boone Blvd, Suite 200, Vienna, VA 22182",
    "7900 Westpark Dr, Suite 300, McLean, VA 22102",
    "8000 Towers Crescent Dr, Suite 100, Vienna, VA 22182",
    "3100 Clarendon Blvd, Suite 300, Arlington, VA 22201",
    "1800 N Kent St, Suite 600, Arlington, VA 22209",
    "901 N Stuart St, Suite 200, Arlington, VA 22203",
    "3601 Wilson Blvd, Suite 100, Arlington, VA 22201",
    "500 N Washington St, Suite 200, Alexandria, VA 22314",
    "520 N Washington St, Alexandria, VA 22314",
    "105 Park St, Falls Church, VA 22046",
    "400 S Maple Ave, Suite 200, Falls Church, VA 22046",
    "2530 Columbia Pike, Arlington, VA 22204",
}

SEED_COMPANY_NAMES: set[str] = {
    "ClearPath Technologies",
    "Apex Federal Solutions",
    "DataCore Intelligence",
    "Meridian Policy Group",
    "Silverline Health Innovations",
    "Skybridge Federal Advisors",
    "NoVA Wealth Partners",
    "Patriot Logistics Solutions",
}

# Hard safety caps — abort if identification looks wrong
MAX_PROPERTIES_TO_DELETE = 20
MAX_COMPANIES_TO_DELETE  = 15


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_user_activity(db, *, property_id=None, company_id=None) -> bool:
    """True if any non-system ActivityLog entry exists for this record."""
    q = db.query(ActivityLog).filter(
        ActivityLog.created_by != "system",
        ActivityLog.action_type.in_(["CALL", "EMAIL", "MEETING", "NOTE", "RESEARCH"]),
    )
    if property_id is not None:
        q = q.filter(ActivityLog.property_id == property_id)
    if company_id is not None:
        q = q.filter(ActivityLog.company_id == company_id)
    return q.first() is not None


def _opportunity_is_user_touched(opp: Opportunity) -> bool:
    """True if a user has manually advanced the opportunity beyond IDENTIFIED."""
    return opp.stage not in ("IDENTIFIED",)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(confirm: bool = False) -> None:
    init_db()
    db = SessionLocal()

    try:
        # ── 1. Find seed candidates by exact address / name ───────────────────
        seed_props_all = (
            db.query(Property)
            .filter(Property.address.in_(SEED_PROPERTY_ADDRESSES))
            .all()
        )
        seed_cos_all = (
            db.query(Company)
            .filter(Company.name.in_(SEED_COMPANY_NAMES))
            .all()
        )

        print(f"\nSeed properties matched (exact address): {len(seed_props_all)}")
        print(f"Seed companies matched  (exact name):    {len(seed_cos_all)}")

        # ── 2. Safety cap — abort if identification looks wrong ───────────────
        if len(seed_props_all) > MAX_PROPERTIES_TO_DELETE:
            raise ValueError(
                f"Seed identification appears wrong — {len(seed_props_all)} properties "
                f"matched, exceeds safety cap of {MAX_PROPERTIES_TO_DELETE}. "
                f"Review SEED_PROPERTY_ADDRESSES in delete_seeds.py before proceeding."
            )
        if len(seed_cos_all) > MAX_COMPANIES_TO_DELETE:
            raise ValueError(
                f"Seed identification appears wrong — {len(seed_cos_all)} companies "
                f"matched, exceeds safety cap of {MAX_COMPANIES_TO_DELETE}. "
                f"Review SEED_COMPANY_NAMES in delete_seeds.py before proceeding."
            )

        # ── 3. Separate deletable vs user-edited ──────────────────────────────
        preserved_props: list[tuple[Property, list[str]]] = []
        deletable_props: list[Property] = []

        for prop in seed_props_all:
            reasons: list[str] = []
            if _has_user_activity(db, property_id=prop.id):
                reasons.append("has user-created activity log (CALL/EMAIL/MEETING/NOTE/RESEARCH)")
            if reasons:
                preserved_props.append((prop, reasons))
            else:
                deletable_props.append(prop)

        preserved_cos: list[tuple[Company, list[str]]] = []
        deletable_cos: list[Company] = []

        for co in seed_cos_all:
            reasons: list[str] = []
            if co.lease_expiry_last_verified:
                reasons.append(f"lease_expiry_last_verified = {co.lease_expiry_last_verified}")
            if co.lease_expiry_source == "manual":
                reasons.append("lease_expiry_source = 'manual'")
            if _has_user_activity(db, company_id=co.id):
                reasons.append("has user-created activity log")
            if reasons:
                preserved_cos.append((co, reasons))
            else:
                deletable_cos.append(co)

        prop_ids = {p.id for p in deletable_props}
        co_ids   = {c.id for c in deletable_cos}

        # ── 4. Find dependent Opportunities ───────────────────────────────────
        candidate_opps: list[Opportunity] = []
        if prop_ids or co_ids:
            filters = []
            if prop_ids:
                filters.append(Opportunity.property_id.in_(prop_ids))
            if co_ids:
                filters.append(Opportunity.company_id.in_(co_ids))
            candidate_opps = db.query(Opportunity).filter(or_(*filters)).all()

        preserved_opps: list[Opportunity] = []   # user-touched — keep but detach FK
        deletable_opps: list[Opportunity] = []

        for opp in candidate_opps:
            if _opportunity_is_user_touched(opp):
                preserved_opps.append(opp)
            else:
                deletable_opps.append(opp)

        opp_ids = {o.id for o in deletable_opps}

        # ── 5. Count affected activity logs ───────────────────────────────────
        act_via_opp = 0
        if opp_ids:
            act_via_opp = (
                db.query(ActivityLog)
                .filter(ActivityLog.opportunity_id.in_(opp_ids))
                .count()
            )

        act_on_props = 0
        if prop_ids:
            act_on_props = (
                db.query(ActivityLog)
                .filter(ActivityLog.property_id.in_(prop_ids))
                .count()
            )

        act_on_cos = 0
        if co_ids:
            act_on_cos = (
                db.query(ActivityLog)
                .filter(ActivityLog.company_id.in_(co_ids))
                .count()
            )

        total_activity = act_via_opp + act_on_props + act_on_cos

        # ── 6. Print report ───────────────────────────────────────────────────
        sep = "═" * 65
        print(f"\n{sep}")
        print("DRY RUN — what WOULD be deleted:" if not confirm else "DELETION PLAN")
        print(sep)

        print(f"\n  Seed properties      → {len(deletable_props)} will be deleted")
        print(f"  Seed companies       → {len(deletable_cos)} will be deleted")
        print(f"  Opportunities        → {len(deletable_opps)} will be deleted")
        print(f"  Activity log entries → {total_activity} will be deleted")

        if deletable_props:
            print("\n  Properties queued for deletion:")
            for p in deletable_props:
                listed = " [LISTED]" if p.is_listed else ""
                print(f"    {p.address}{listed}")

        if deletable_cos:
            print("\n  Companies queued for deletion:")
            for c in deletable_cos:
                print(f"    {c.name} ({c.industry})")

        if preserved_props or preserved_cos:
            print(f"\n{'─'*65}")
            print("  PRESERVED (user-edited seeds — will NOT be deleted):")
            for prop, reasons in preserved_props:
                print(f"    [PROPERTY] {prop.address}")
                for r in reasons:
                    print(f"               ↳ {r}")
            for co, reasons in preserved_cos:
                print(f"    [COMPANY]  {co.name}")
                for r in reasons:
                    print(f"               ↳ {r}")
        else:
            print("\n  (No seed records were preserved — none have user edits)")

        if preserved_opps:
            print(f"\n{'─'*65}")
            print("  PRESERVED OPPORTUNITIES (user-advanced stage — FK will be nulled):")
            for opp in preserved_opps:
                print(
                    f"    [{opp.opportunity_id}] stage={opp.stage} "
                    f"score={opp.score:.0f} type={opp.deal_type}"
                )

        if not confirm:
            print(f"\n{'-'*65}")
            print("DRY RUN complete — no data was modified.")
            print("Re-run with --confirm to execute the deletion.")
            return

        # ── 7. Execute deletion ───────────────────────────────────────────────
        print(f"\n{sep}")
        print("Executing deletion...")

        # 7a. Detach preserved opportunities (null the FK pointing to deleted entity)
        for opp in preserved_opps:
            if opp.property_id in prop_ids:
                opp.property_id = None
            if opp.company_id in co_ids:
                opp.company_id = None
        if preserved_opps:
            db.flush()
            print(f"  Detached {len(preserved_opps)} preserved opportunities (FK nulled)")

        # 7b. Delete activity logs via deletable opportunities
        if opp_ids:
            n = (
                db.query(ActivityLog)
                .filter(ActivityLog.opportunity_id.in_(opp_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} activity logs (via opportunities)")

        # 7c. Delete activity logs directly on seed properties
        if prop_ids:
            n = (
                db.query(ActivityLog)
                .filter(ActivityLog.property_id.in_(prop_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} activity logs (property direct)")

        # 7d. Delete activity logs directly on seed companies
        if co_ids:
            n = (
                db.query(ActivityLog)
                .filter(ActivityLog.company_id.in_(co_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} activity logs (company direct)")

        # 7e. Delete opportunities
        if opp_ids:
            n = (
                db.query(Opportunity)
                .filter(Opportunity.id.in_(opp_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} opportunities")

        # 7f. Delete companies, then properties
        if co_ids:
            n = (
                db.query(Company)
                .filter(Company.id.in_(co_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} companies")

        if prop_ids:
            n = (
                db.query(Property)
                .filter(Property.id.in_(prop_ids))
                .delete(synchronize_session=False)
            )
            print(f"  Deleted {n} properties")

        db.commit()
        print("\n✓ Seed deletion complete.")

        # ── 8. Re-run pipeline on remaining data ──────────────────────────────
        print(f"\n{'─'*65}")
        print("Re-running pipeline on remaining real data...")
        from app.ingestion.pipeline import run_full_pipeline  # noqa: PLC0415

        result = run_full_pipeline(db)
        print(f"  Properties refreshed:   {result['properties_refreshed']}")
        print(f"  Companies refreshed:    {result['companies_refreshed']}")
        print(f"  New opportunities:      {result['new_opportunities']}")
        print(f"  Elapsed:                {result['elapsed_seconds']}s")

        detail = result.get("pipeline_detail", {})
        if detail:
            print(f"\n  Pipeline detail:")
            print(f"    Companies considered:   {detail.get('companies_considered', '—')}")
            print(f"    Properties considered:  {detail.get('properties_considered', '—')}")
            print(f"    Pairings evaluated:     {detail.get('pairings_evaluated', '—')}")
            print(f"    Passed submarket check: {detail.get('passed_submarket', '—')}")
            print(f"    Passed SF-fit check:    {detail.get('passed_sf_fit', '—')}")
            print(f"    Tenant matches created: {detail.get('tenant_matches_created', '—')}")
            print(f"    Standalone created:     {detail.get('standalone_created', '—')}")

        print(f"\n{'═'*65}")
        print("Done. Refresh the dashboard to see updated rankings.")

    finally:
        db.close()


if __name__ == "__main__":
    confirm = "--confirm" in sys.argv
    run(confirm=confirm)
