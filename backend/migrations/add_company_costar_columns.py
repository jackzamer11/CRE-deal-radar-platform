"""
One-time migration: add CoStar Tenant enrichment columns to the companies table.

SQLite supports ALTER TABLE ... ADD COLUMN for new nullable columns,
so no table recreation is needed here.

OPTION A — Python (run from the backend/ directory):
    python -m migrations.add_company_costar_columns
    OR
    python migrations/add_company_costar_columns.py

OPTION B — Raw SQLite shell:
    sqlite3 deal_radar.db

    Paste these (safe to run if columns already exist — SQLite will error
    on duplicates, so only run the ones you're missing):

        ALTER TABLE companies ADD COLUMN tenant_representative TEXT;
        ALTER TABLE companies ADD COLUMN current_rent_psf REAL;
        ALTER TABLE companies ADD COLUMN future_move_flag INTEGER;
        ALTER TABLE companies ADD COLUMN future_move_type TEXT;
        ALTER TABLE companies ADD COLUMN linked_property_id INTEGER;
        .quit
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

NEW_COLS = [
    ("tenant_representative", "TEXT"),
    ("current_rent_psf",      "REAL"),
    ("future_move_flag",      "INTEGER"),   # SQLite stores booleans as integers
    ("future_move_type",      "TEXT"),
    ("linked_property_id",    "INTEGER"),
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
