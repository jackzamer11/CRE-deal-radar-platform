"""
Migration: Idempotently add any columns that the ORM model defines but the
live database is missing.

Background
----------
SQLAlchemy's Base.metadata.create_all() only creates *new tables* — it never
adds columns to existing tables.  Across several development sprints (CoStar
enrichment, outreach scores, rent-source tracking) new columns were appended
to the ORM models without a corresponding ALTER TABLE.  This script closes
that gap.

It is safe to run multiple times.  Every ADD COLUMN is guarded by a PRAGMA
table_info check so a column that already exists is silently skipped.

Usage (run from the backend/ directory):
    python -m migrations.ensure_schema
    python migrations/ensure_schema.py

Wired into app startup via main.py so it runs automatically on every deploy.

DB path resolution
------------------
The path is derived from settings.database_url — the same source used by the
SQLAlchemy engine — so this migration always targets the correct file
regardless of deployment (dev, Docker, etc.).

  Dev default : sqlite:///./deal_radar.db  → <cwd>/deal_radar.db
  Docker      : sqlite:////app/data/deal_radar.db → /app/data/deal_radar.db
"""
import os
import sqlite3


def _resolve_db_path() -> str:
    """Return the absolute filesystem path of the configured SQLite database."""
    try:
        from app.config import settings
        url = settings.database_url
    except Exception:
        url = os.environ.get("DATABASE_URL", "sqlite:///./deal_radar.db")

    if not url.startswith("sqlite:///"):
        raise ValueError(
            f"ensure_schema only supports SQLite; got: {url!r}"
        )
    # sqlite:////abs/path  →  /abs/path  (4 slashes = absolute)
    # sqlite:///./rel/path →  ./rel/path (3 slashes = relative to CWD)
    raw_path = url[len("sqlite:///"):]
    return os.path.abspath(raw_path)


def _has_column(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def _add_column(cur: sqlite3.Cursor, table: str, col: str, col_def: str) -> bool:
    """Add column if absent. Returns True if column was added."""
    if _has_column(cur, table, col):
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
    print(f"  + {table}.{col}")
    return True


def ensure_properties(cur: sqlite3.Cursor) -> int:
    """Add every column the Property ORM model expects that may be missing."""
    added = 0

    # ── is_listed → listed_for_sale rename / add ─────────────────────────
    if not _has_column(cur, "properties", "listed_for_sale"):
        if _has_column(cur, "properties", "is_listed"):
            cur.execute(
                "ALTER TABLE properties RENAME COLUMN is_listed TO listed_for_sale"
            )
            print("  ~ properties.is_listed → listed_for_sale (renamed)")
            added += 1
        else:
            added += _add_column(
                cur, "properties", "listed_for_sale", "BOOLEAN DEFAULT 0"
            )

    # ── Rent-source tracking (Part 7) ────────────────────────────────────
    added += _add_column(cur, "properties", "in_place_rent_source", "TEXT")
    added += _add_column(cur, "properties", "in_place_rent_last_verified", "DATE")

    # ── CoStar enrichment fields (Part 2) ────────────────────────────────
    added += _add_column(cur, "properties", "star_rating", "INTEGER")
    added += _add_column(cur, "properties", "sf_avail", "INTEGER")
    added += _add_column(cur, "properties", "landlord_representative", "TEXT")
    added += _add_column(cur, "properties", "landlord_rep_contact", "TEXT")
    added += _add_column(cur, "properties", "sales_company", "TEXT")
    added += _add_column(cur, "properties", "sales_contact", "TEXT")
    added += _add_column(cur, "properties", "tenancy", "TEXT")
    added += _add_column(cur, "properties", "stories", "INTEGER")
    added += _add_column(cur, "properties", "parking_ratio", "REAL")

    # ── Outreach / match scores (Parts 3-4) ──────────────────────────────
    added += _add_column(cur, "properties", "tenant_match_score", "REAL DEFAULT 0.0")
    added += _add_column(cur, "properties", "listing_rep_score", "REAL DEFAULT 0.0")
    added += _add_column(cur, "properties", "acquisition_score", "REAL DEFAULT 0.0")
    added += _add_column(cur, "properties", "dominant_score_type", "TEXT")

    # ── User-edit guard (Part 7) ──────────────────────────────────────────
    added += _add_column(cur, "properties", "last_modified_by_user", "DATETIME")

    # ── Snooze fields ─────────────────────────────────────────────────────
    added += _add_column(cur, "properties", "snoozed_until",        "DATE")
    added += _add_column(cur, "properties", "snooze_reason",        "TEXT")
    added += _add_column(cur, "properties", "returned_from_snooze", "BOOLEAN")

    # ── Owner confirmed leasing ────────────────────────────────────────────
    added += _add_column(cur, "properties", "owner_confirmed_leasing",      "BOOLEAN DEFAULT 0")
    added += _add_column(cur, "properties", "owner_confirmed_leasing_date", "DATE")

    return added


def ensure_outreach_log(cur: sqlite3.Cursor) -> int:
    """Add property_id and outreach_type columns to outreach_log if missing."""
    added = 0
    added += _add_column(cur, "outreach_log", "property_id",   "INTEGER REFERENCES properties(id)")
    added += _add_column(cur, "outreach_log", "outreach_type", "TEXT DEFAULT 'tenant'")
    return added


def ensure_activity_logs(cur: sqlite3.Cursor) -> int:
    """Add outreach-specific columns to activity_logs if missing."""
    added = 0
    added += _add_column(cur, "activity_logs", "outreach_type",  "TEXT")
    added += _add_column(cur, "activity_logs", "target_type",    "TEXT")
    added += _add_column(cur, "activity_logs", "contact_method", "TEXT")
    added += _add_column(cur, "activity_logs", "subject",        "TEXT")
    added += _add_column(cur, "activity_logs", "notes",          "TEXT")
    return added


def ensure_outreach_drafts(cur: sqlite3.Cursor) -> int:
    """Create the outreach_drafts table if it does not exist (idempotent)."""
    added = 0
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='outreach_drafts'")
    if cur.fetchone():
        added += _add_column(cur, "outreach_drafts", "direction", "TEXT DEFAULT 'property_side'")
        added += _add_column(cur, "outreach_drafts", "intelligence_findings", "TEXT")
        return added
    cur.execute("""
        CREATE TABLE outreach_drafts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id      TEXT NOT NULL,
            company_id       TEXT,
            outreach_type    TEXT NOT NULL,
            direction        TEXT DEFAULT 'property_side',
            subject          TEXT NOT NULL,
            body             TEXT NOT NULL,
            call_script_opening    TEXT,
            call_script_core       TEXT,
            call_script_pain_probe TEXT,
            call_script_close      TEXT,
            target_type      TEXT NOT NULL,
            recipient_name   TEXT,
            recipient_email  TEXT,
            internal_context TEXT,
            intelligence_findings TEXT,
            score            REAL,
            priority         TEXT,
            created_at       DATETIME,
            last_viewed_at   DATETIME
        )
    """)
    cur.execute("CREATE INDEX idx_outreach_drafts_property ON outreach_drafts(property_id)")
    cur.execute("CREATE INDEX idx_outreach_drafts_pair ON outreach_drafts(property_id, company_id)")
    print("  + created table outreach_drafts")
    return 1



def ensure_companies(cur: sqlite3.Cursor) -> int:
    """Add any columns the Company ORM model expects that may be missing."""
    added = 0
    added += _add_column(cur, "companies", "last_modified_by_user", "DATETIME")
    added += _add_column(
        cur, "companies", "lease_trajectory", "TEXT DEFAULT 'AUTO' NOT NULL"
    )
    added += _add_column(cur, "companies", "lease_expiry_date", "DATE")
    return added


def backfill_lease_expiry_dates(cur: sqlite3.Cursor) -> int:
    """For companies with lease_expiry_months but no lease_expiry_date, compute and store the date."""
    try:
        from dateutil.relativedelta import relativedelta
        from datetime import date
    except ImportError:
        return 0
    cur.execute("""
        SELECT id, lease_expiry_months FROM companies
        WHERE lease_expiry_date IS NULL AND lease_expiry_months IS NOT NULL AND lease_expiry_months > 0
    """)
    rows = cur.fetchall()
    today = date.today()
    for row_id, months in rows:
        expiry = today + relativedelta(months=int(months))
        cur.execute(
            "UPDATE companies SET lease_expiry_date = ? WHERE id = ?",
            (expiry.isoformat(), row_id),
        )
    if rows:
        print(f"  + backfilled lease_expiry_date for {len(rows)} companies")
    return len(rows)


def run() -> None:
    db = _resolve_db_path()
    if not os.path.exists(db):
        print(f"ensure_schema: database not found at {db} — skipping.")
        return

    conn = sqlite3.connect(db)
    cur = conn.cursor()

    prop_added   = ensure_properties(cur)
    comp_added   = ensure_companies(cur)
    olog_added   = ensure_outreach_log(cur)
    act_added    = ensure_activity_logs(cur)
    draft_added  = ensure_outreach_drafts(cur)
    bf_added     = backfill_lease_expiry_dates(cur)

    conn.commit()
    conn.close()

    total = prop_added + comp_added + olog_added + act_added + draft_added + bf_added
    if total:
        print(f"ensure_schema: applied {total} column addition(s)/backfill(s).")
    else:
        print("ensure_schema: schema is up-to-date.")


if __name__ == "__main__":
    import sys

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
