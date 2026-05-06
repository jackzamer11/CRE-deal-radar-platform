"""
Migration: rename properties.is_listed → properties.listed_for_sale.

Background:
-----------
The original is_listed column conflated "for sale" and "for lease."
Real CoStar data distinguishes the two. This migration renames the
column so the schema reflects its true meaning: the property is
actively listed for sale.

listed_for_lease is a DERIVED field (sf_avail > 0) computed at
serialization time — no migration needed for it.

SQLite 3.25+ supports ALTER TABLE RENAME COLUMN directly, so no
table recreation is needed.

Safe to re-run — checks whether listed_for_sale already exists first.

Run from backend/ directory:
    python -m migrations.rename_is_listed
    OR
    python migrations/rename_is_listed.py
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")


def _has_column(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    cur  = conn.cursor()

    if _has_column(cur, "properties", "listed_for_sale"):
        print("  listed_for_sale already exists — nothing to do.")
        conn.close()
        return

    if not _has_column(cur, "properties", "is_listed"):
        print("  Neither column found — adding listed_for_sale ...")
        cur.execute(
            "ALTER TABLE properties ADD COLUMN listed_for_sale BOOLEAN DEFAULT 0"
        )
        conn.commit()
        conn.close()
        print("Done.")
        return

    print("  Renaming properties.is_listed → properties.listed_for_sale ...")
    cur.execute("ALTER TABLE properties RENAME COLUMN is_listed TO listed_for_sale")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
