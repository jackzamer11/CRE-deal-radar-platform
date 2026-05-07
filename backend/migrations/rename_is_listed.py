"""
Migration: rename properties.is_listed -> properties.listed_for_sale.

Why: the ORM model and downstream code now use `listed_for_sale` to
distinguish from `listed_for_lease` (which is derived from sf_avail at
serialization time and is NOT a column). The live SQLite DB may have:

    - Already been renamed (column `listed_for_sale` exists)            → skip
    - The legacy column `is_listed`                                     → ALTER RENAME
    - Neither column (fresh DB or pre-Part-1 schema)                    → ALTER ADD

Run from backend/ directory:
    python -m migrations.rename_is_listed

Safe to re-run — idempotent.
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
    cur  = conn.cursor()

    cur.execute("PRAGMA table_info(properties)")
    cols = {row[1] for row in cur.fetchall()}

    if "listed_for_sale" in cols:
        print("  listed_for_sale already exists, skipping.")
    elif "is_listed" in cols:
        cur.execute("ALTER TABLE properties RENAME COLUMN is_listed TO listed_for_sale")
        print("  Renamed is_listed -> listed_for_sale.")
    else:
        cur.execute("ALTER TABLE properties ADD COLUMN listed_for_sale BOOLEAN DEFAULT 0")
        print("  Added listed_for_sale (BOOLEAN DEFAULT 0).")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
