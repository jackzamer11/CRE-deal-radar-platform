"""
Migration: add last_modified_by_user column and restore Amentum's
manually-verified lease data.

Background:
-----------
A CoStar tenant import ran after Jack manually entered Amentum's lease
expiry (2 months, via the pencil icon on the Companies page).  The import
code unconditionally overwrote lease_expiry_months with None because
CoStar's export for that row had no "Next Break Date" — even though the
existing value was user-verified.  The bug was in the CoStar import
update path (companies.py::costar_tenant_import), not in any migration.
This has been fixed by adding a PROTECTED_LEASE_SOURCES guard there.

This migration:
1. Adds last_modified_by_user (TEXT/datetime) column to companies table.
2. Restores CO-021 (Amentum) to its manually-verified state.

IMPORTANT: This migration does not modify any existing column values
           except for the explicit Amentum data restoration in step 2.
           All other rows are left exactly as-is.

Run from backend/ directory:
    python -m migrations.add_user_data_protection
    OR
    python migrations/add_user_data_protection.py

Safe to re-run — idempotent on column add; Amentum restore is explicit.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Step 1: add last_modified_by_user column ────────────────────────────
    cur.execute("PRAGMA table_info(companies)")
    existing_cols = {row["name"] for row in cur.fetchall()}

    if "last_modified_by_user" in existing_cols:
        print("  last_modified_by_user: already exists, skipping.")
    else:
        cur.execute("ALTER TABLE companies ADD COLUMN last_modified_by_user TEXT")
        print("  last_modified_by_user: added (TEXT).")

    # ── Step 2: restore Amentum (CO-021) lease data ─────────────────────────
    cur.execute("SELECT company_id, name, lease_expiry_months FROM companies WHERE company_id = 'CO-021'")
    row = cur.fetchone()

    if row is None:
        print("  CO-021 (Amentum) not found in this database — no restoration needed.")
        print("  (This is expected in dev; the restore targets the production database.)")
    else:
        cur.execute(
            """
            UPDATE companies
            SET
                lease_expiry_months      = 2,
                lease_expiry_source      = 'manual',
                lease_expiry_last_verified = '2026-04-28',
                lease_trajectory         = 'CONTRACTING',
                last_modified_by_user    = '2026-04-28T00:00:00'
            WHERE company_id = 'CO-021'
            """,
        )
        print(
            f"  CO-021 ({row['name']}): restored lease_expiry_months=2, "
            f"source='manual', verified='2026-04-28', trajectory='CONTRACTING'."
        )

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
