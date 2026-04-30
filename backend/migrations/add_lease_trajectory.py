"""
One-time migration: add lease_trajectory column to the companies table.

Allowed values: AUTO | CONTRACTING | FLAT | GROWING
Default: AUTO (existing behavior — outreach agent uses tiered SF/head logic)

OPTION A — Python (run from the backend/ directory):
    python -m migrations.add_lease_trajectory
    OR
    python migrations/add_lease_trajectory.py

OPTION B — Raw SQLite shell:
    sqlite3 deal_radar.db
    ALTER TABLE companies ADD COLUMN lease_trajectory TEXT NOT NULL DEFAULT 'AUTO';
    .quit

IMPORTANT: This migration does not modify any existing column values.
           It only adds the new column with a DEFAULT — all existing rows
           retain their current values verbatim.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

NEW_COLS = [
    ("lease_trajectory", "TEXT NOT NULL DEFAULT 'AUTO'"),
]


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(companies)")
    existing_cols = {row["name"] for row in cur.fetchall()}

    added = 0
    for col_name, col_type in NEW_COLS:
        if col_name in existing_cols:
            print(f"  {col_name}: already exists, skipping.")
        else:
            cur.execute(f"ALTER TABLE companies ADD COLUMN {col_name} {col_type}")
            print(f"  {col_name}: added ({col_type}).")
            added += 1

    conn.commit()
    conn.close()

    if added:
        print(f"\nDone — {added} column(s) added to companies table.")
    else:
        print("\nMigration already fully applied.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
