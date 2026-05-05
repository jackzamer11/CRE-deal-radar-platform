"""
Migration: extend outreach_log to support property-side outreach.

Background:
-----------
The original outreach_log table was tenant-only: company_id NOT NULL.
Property-side outreach (tenant_match / listing_rep / acquisition / broker)
needs to log against properties as well. We add property_id (nullable FK
to properties) and outreach_type, then relax company_id to NULL so the
same table can serve both sides.

SQLite cannot ALTER COLUMN nullability in place, so this migration:
  1. Renames outreach_log -> outreach_log_old
  2. Creates outreach_log with company_id NULL, property_id FK, outreach_type
  3. Copies every existing row from outreach_log_old, defaulting
     outreach_type='tenant' (these are all tenant outreach by definition)
  4. Drops outreach_log_old
  5. Recreates the company_id index and adds a property_id index

IMPORTANT: This migration does not modify any existing column values
           except to set outreach_type='tenant' on legacy rows that have
           no outreach_type column at all. Every other field on every
           existing row is preserved verbatim.

Run from backend/ directory:
    python -m migrations.add_property_outreach_fields
    OR
    python migrations/add_property_outreach_fields.py

Safe to re-run — checks the schema first and skips when already migrated.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")


def _has_column(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    cur  = conn.cursor()

    if not _table_exists(cur, "outreach_log"):
        print("outreach_log table does not exist — run add_outreach_log first.")
        conn.close()
        return

    already_migrated = (
        _has_column(cur, "outreach_log", "property_id")
        and _has_column(cur, "outreach_log", "outreach_type")
    )

    if already_migrated:
        print("  outreach_log already has property_id + outreach_type — nothing to do.")
        conn.close()
        return

    print("  Recreating outreach_log with company_id nullable + property_id + outreach_type...")
    cur.execute("ALTER TABLE outreach_log RENAME TO outreach_log_old")

    cur.execute("""
        CREATE TABLE outreach_log (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id             INTEGER REFERENCES companies(id),
            property_id            INTEGER REFERENCES properties(id),
            outreach_type          TEXT NOT NULL DEFAULT 'tenant',
            generated_at           TEXT NOT NULL DEFAULT (datetime('now')),
            email_subject          TEXT,
            email_body             TEXT,
            call_script_opening    TEXT,
            call_script_core       TEXT,
            call_script_pain_probe TEXT,
            call_script_close      TEXT,
            projected_sf           INTEGER,
            score_at_generation    REAL,
            priority_at_generation TEXT,
            marked_contacted       INTEGER NOT NULL DEFAULT 0,
            email_sent             INTEGER NOT NULL DEFAULT 0,
            call_made              INTEGER NOT NULL DEFAULT 0,
            outcome_notes          TEXT,
            contacted_at           TEXT
        )
    """)

    cur.execute("""
        INSERT INTO outreach_log (
            id, company_id, property_id, outreach_type, generated_at,
            email_subject, email_body,
            call_script_opening, call_script_core, call_script_pain_probe, call_script_close,
            projected_sf, score_at_generation, priority_at_generation,
            marked_contacted, email_sent, call_made, outcome_notes, contacted_at
        )
        SELECT
            id, company_id, NULL, 'tenant', generated_at,
            email_subject, email_body,
            call_script_opening, call_script_core, call_script_pain_probe, call_script_close,
            projected_sf, score_at_generation, priority_at_generation,
            marked_contacted, email_sent, call_made, outcome_notes, contacted_at
        FROM outreach_log_old
    """)
    moved = cur.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
    print(f"  Copied {moved} existing rows (all tagged outreach_type='tenant').")

    cur.execute("DROP TABLE outreach_log_old")

    cur.execute("CREATE INDEX IF NOT EXISTS ix_outreach_log_company_id  ON outreach_log(company_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_outreach_log_property_id ON outreach_log(property_id)")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
