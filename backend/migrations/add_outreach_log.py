"""
Migration: create outreach_log table for tracking AI-generated outreach packages.

IMPORTANT: This migration does not modify any existing column values.
           It only creates a new table — all existing tables are untouched.

Run from backend/ directory:
    python -m migrations.add_outreach_log
    OR
    python migrations/add_outreach_log.py
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS outreach_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id             INTEGER NOT NULL REFERENCES companies(id),
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
);
"""

IDX_SQL = "CREATE INDEX IF NOT EXISTS ix_outreach_log_company_id ON outreach_log(company_id);"


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    cur  = conn.cursor()
    cur.execute(CREATE_SQL)
    cur.execute(IDX_SQL)
    conn.commit()
    conn.close()
    print("Done — outreach_log table created (or already existed).")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
