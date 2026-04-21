"""
One-time migration: make occupancy_pct and vacancy_pct nullable.

SQLite does not support ALTER COLUMN, so this script recreates the
properties table while preserving all existing rows and indexes.

Usage (from the backend/ directory):
    python -m migrations.make_occupancy_nullable
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")
NULLABLE_COLS = {"occupancy_pct", "vacancy_pct"}


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Check current NOT NULL state
    cur.execute("PRAGMA table_info(properties)")
    cols = list(cur.fetchall())
    already_nullable = all(
        not row["notnull"]
        for row in cols if row["name"] in NULLABLE_COLS
    )
    if already_nullable:
        print("Migration already applied — occupancy_pct/vacancy_pct are nullable.")
        conn.close()
        return

    print("Migrating properties table to make occupancy_pct/vacancy_pct nullable…")

    # Build new CREATE TABLE statement from PRAGMA output
    col_defs = []
    for row in cols:
        name    = row["name"]
        typ     = row["type"] or "TEXT"
        notnull = row["notnull"]
        dflt    = row["dflt_value"]
        pk      = row["pk"]

        if name in NULLABLE_COLS:
            notnull = 0  # drop NOT NULL constraint

        defn = f'"{name}" {typ}'
        if pk:
            defn += " PRIMARY KEY"
        if notnull:
            defn += " NOT NULL"
        if dflt is not None:
            defn += f" DEFAULT {dflt}"
        col_defs.append(defn)

    # Capture existing indexes before we drop the table
    cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name='properties' AND sql IS NOT NULL"
    )
    indexes = cur.fetchall()

    # Recreate table
    cur.execute("ALTER TABLE properties RENAME TO _properties_old")
    cur.execute(f"CREATE TABLE properties ({', '.join(col_defs)})")
    col_names = ", ".join(f'"{r["name"]}"' for r in cols)
    cur.execute(f"INSERT INTO properties ({col_names}) SELECT {col_names} FROM _properties_old")
    cur.execute("DROP TABLE _properties_old")

    # Recreate indexes
    for idx in indexes:
        cur.execute(idx["sql"])

    conn.commit()
    conn.close()
    print("Done — occupancy_pct and vacancy_pct are now nullable in the live database.")
    print("Existing rows are unchanged; only new CoStar imports may have NULL occupancy.")


if __name__ == "__main__":
    run()
