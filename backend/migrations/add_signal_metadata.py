"""
One-time migration: add signals_scored_count and insufficient_data columns
to both the companies and properties tables.

SQLite supports ALTER TABLE ... ADD COLUMN for nullable/defaulted columns,
so no table recreation is needed.

OPTION A — Python (run from the backend/ directory):
    python -m migrations.add_signal_metadata

OPTION B — Raw SQLite shell:
    sqlite3 deal_radar.db
    ALTER TABLE companies  ADD COLUMN signals_scored_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE companies  ADD COLUMN insufficient_data     INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE properties ADD COLUMN signals_scored_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE properties ADD COLUMN insufficient_data     INTEGER NOT NULL DEFAULT 0;
    .quit
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

NEW_COLS = {
    "companies": [
        ("signals_scored_count", "INTEGER NOT NULL DEFAULT 0"),
        ("insufficient_data",    "INTEGER NOT NULL DEFAULT 0"),
    ],
    "properties": [
        ("signals_scored_count", "INTEGER NOT NULL DEFAULT 0"),
        ("insufficient_data",    "INTEGER NOT NULL DEFAULT 0"),
    ],
}


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

    for table, cols in NEW_COLS.items():
        existing = _existing_columns(cur, table)
        for col_name, col_def in cols:
            if col_name in existing:
                print(f"  {table}.{col_name} already exists — skipping.")
            else:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                print(f"  Added {table}.{col_name}.")

    conn.commit()
    conn.close()
    print("Done — signal metadata columns added to companies and properties.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
