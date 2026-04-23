"""
One-time migration: add lease_expiry_source and lease_expiry_last_verified
columns to the companies table.

SQLite supports ALTER TABLE ... ADD COLUMN for nullable/defaulted columns,
so no table recreation is needed.

Valid lease_expiry_source values:
  costar | manual | sec_filing | landlord_confirmed | public_record

OPTION A — Python (run from the backend/ directory):
    python -m migrations.add_lease_expiry_metadata

OPTION B — Raw SQLite shell:
    sqlite3 deal_radar.db
    ALTER TABLE companies ADD COLUMN lease_expiry_source       TEXT;
    ALTER TABLE companies ADD COLUMN lease_expiry_last_verified TEXT;
    .quit
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

NEW_COLS = [
    ("lease_expiry_source",        "TEXT"),
    ("lease_expiry_last_verified", "TEXT"),
]


def _existing_columns(cur: sqlite3.Cursor, table: str) -> set:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    cur = conn.cursor()

    existing = _existing_columns(cur, "companies")
    for col_name, col_def in NEW_COLS:
        if col_name in existing:
            print(f"  companies.{col_name} already exists — skipping.")
        else:
            cur.execute(f"ALTER TABLE companies ADD COLUMN {col_name} {col_def}")
            print(f"  Added companies.{col_name}.")

    conn.commit()
    conn.close()
    print("Done — lease expiry metadata columns added to companies.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
