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

    # ── Medical/non-medical classification (soft match penalty) ────────────
    # Defaults to 0 (false) for all existing rows. Guarded so a partially-migrated
    # DB can never abort startup on this single column.
    try:
        added += _add_column(cur, "properties", "is_medical", "BOOLEAN NOT NULL DEFAULT 0")
    except Exception as _exc:
        print(f"  ! properties.is_medical add skipped: {_exc}")

    return added


def ensure_outreach_log(cur: sqlite3.Cursor) -> int:
    """Create the outreach_log table if absent, or add any missing columns.

    On a fresh local DB (or any instance that pre-dates the outreach_log model)
    the table may not exist yet.  Attempting ALTER TABLE on a non-existent table
    raises OperationalError: no such table.  Check first and CREATE when needed.
    """
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='outreach_log'"
    )
    if not cur.fetchone():
        # Table does not exist — create it with the full current schema so that
        # both the _add_column guards below and fix_outreach_log_company_id_nullable
        # find a well-formed table to work with.
        cur.execute("""
            CREATE TABLE outreach_log (
                id                     INTEGER NOT NULL,
                company_id             INTEGER,
                property_id            INTEGER,
                outreach_type          VARCHAR NOT NULL,
                generated_at           DATETIME,
                email_subject          TEXT,
                email_body             TEXT,
                call_script_opening    TEXT,
                call_script_core       TEXT,
                call_script_pain_probe TEXT,
                call_script_close      TEXT,
                projected_sf           INTEGER,
                score_at_generation    FLOAT,
                priority_at_generation VARCHAR,
                marked_contacted       BOOLEAN,
                email_sent             BOOLEAN,
                call_made              BOOLEAN,
                outcome_notes          TEXT,
                contacted_at           DATETIME,
                PRIMARY KEY (id),
                FOREIGN KEY(company_id)  REFERENCES companies (id),
                FOREIGN KEY(property_id) REFERENCES properties (id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_outreach_log_id "
            "ON outreach_log (id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_outreach_log_company_id "
            "ON outreach_log (company_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_outreach_log_property_id "
            "ON outreach_log (property_id)"
        )
        print("  + created table outreach_log")
        return 1

    # Table exists — add any columns that may be missing from older schemas.
    added = 0
    added += _add_column(cur, "outreach_log", "property_id",   "INTEGER REFERENCES properties(id)")
    added += _add_column(cur, "outreach_log", "outreach_type", "TEXT DEFAULT 'tenant'")
    return added


def _add_activity_column(cur: sqlite3.Cursor, col: str, col_def: str) -> int:
    """Add an activity_logs column, guarded against duplicate-column errors.

    Belt-and-suspenders: a PRAGMA check skips columns that already exist, and the
    ALTER is additionally wrapped in try/except so a concurrent/duplicate add
    (e.g. column created between the check and the ALTER) never aborts startup.
    """
    if _has_column(cur, "activity_logs", col):
        return 0
    try:
        cur.execute(f"ALTER TABLE activity_logs ADD COLUMN {col} {col_def}")
        print(f"  + activity_logs.{col}")
        return 1
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return 0
        raise


def ensure_activity_logs(cur: sqlite3.Cursor) -> int:
    """Add outreach + stage-tracking columns to activity_logs if missing."""
    added = 0
    added += _add_column(cur, "activity_logs", "outreach_type",  "TEXT")
    added += _add_column(cur, "activity_logs", "target_type",    "TEXT")
    added += _add_column(cur, "activity_logs", "contact_method", "TEXT")
    added += _add_column(cur, "activity_logs", "subject",        "TEXT")
    added += _add_column(cur, "activity_logs", "notes",          "TEXT")

    # ── Stage pipeline (current state) + revisit reminder ──────────────────────
    # 'stage' defaults to 'Sent', which backfills every existing row on ADD COLUMN.
    # 'next_touch_date' is the optional revisit date (Dormant / Not Interested).
    added += _add_activity_column(cur, "stage", "TEXT DEFAULT 'Sent'")
    added += _add_activity_column(cur, "next_touch_date", "DATE")

    # Backfill any legacy rows whose stage is still NULL/empty → 'Sent'.
    try:
        cur.execute(
            "UPDATE activity_logs SET stage = 'Sent' "
            "WHERE stage IS NULL OR stage = ''"
        )
    except sqlite3.OperationalError:
        pass

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



def fix_outreach_log_company_id_nullable(cur: sqlite3.Cursor) -> int:
    """Drop the NOT NULL constraint on outreach_log.company_id if it is present.

    Background
    ----------
    The original outreach_log table was created when outreach was tenant-only.
    company_id was NOT NULL because every log row belonged to a company.
    When property-side outreach (tenant_match, listing_rep, etc.) was added,
    the ORM model changed company_id to nullable=True — but SQLAlchemy's
    create_all() never alters *existing* tables.  The Docker-volume database
    therefore retained the NOT NULL constraint, causing every property-side
    outreach save to 500 with IntegrityError.

    Fix
    ---
    SQLite cannot DROP a NOT NULL constraint via ALTER TABLE.  This function
    uses the standard SQLite pattern: rename → recreate (without NOT NULL on
    company_id) → copy all rows → drop the old table.

    The new CREATE TABLE uses a hardcoded DDL that matches the current ORM model
    exactly (matching the SQLAlchemy-generated style with table-level PRIMARY KEY
    and FOREIGN KEY constraints).  Dynamic reconstruction from PRAGMA is avoided
    because column-type strings returned by PRAGMA can vary across SQLite
    versions and schema histories, producing malformed SQL.

    Idempotent: if company_id is already nullable (or missing), returns 0.
    No data loss: rows are copied column-by-column using only columns that exist
    in the old table, so older schema versions without every column are safe.
    """
    cur.execute("PRAGMA table_info(outreach_log)")
    col_rows = cur.fetchall()  # (cid, name, type, notnull, dflt_value, pk)
    if not col_rows:
        return 0  # Table doesn't exist yet — nothing to fix

    company_col = next((r for r in col_rows if r[1] == "company_id"), None)
    if company_col is None or company_col[3] == 0:
        return 0  # Already nullable or column absent — no-op

    # Columns defined in the current ORM model (canonical order).
    # INSERT/SELECT uses only the intersection with what actually exists in the
    # old table so that older Docker volumes missing some columns still copy cleanly.
    canonical_cols = [
        "id", "company_id", "property_id", "outreach_type", "generated_at",
        "email_subject", "email_body",
        "call_script_opening", "call_script_core",
        "call_script_pain_probe", "call_script_close",
        "projected_sf", "score_at_generation", "priority_at_generation",
        "marked_contacted", "email_sent", "call_made",
        "outcome_notes", "contacted_at",
    ]
    existing = {r[1] for r in col_rows}
    copy_cols = [c for c in canonical_cols if c in existing]
    cols_csv = ", ".join(copy_cols)

    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute("ALTER TABLE outreach_log RENAME TO _outreach_log_pre_fix")

    # Hardcoded DDL — matches the SQLAlchemy-generated schema exactly.
    # company_id has no NOT NULL (the fix); all other constraints are preserved.
    cur.execute("""
        CREATE TABLE outreach_log (
            id                     INTEGER NOT NULL,
            company_id             INTEGER,
            property_id            INTEGER,
            outreach_type          VARCHAR NOT NULL,
            generated_at           DATETIME,
            email_subject          TEXT,
            email_body             TEXT,
            call_script_opening    TEXT,
            call_script_core       TEXT,
            call_script_pain_probe TEXT,
            call_script_close      TEXT,
            projected_sf           INTEGER,
            score_at_generation    FLOAT,
            priority_at_generation VARCHAR,
            marked_contacted       BOOLEAN,
            email_sent             BOOLEAN,
            call_made              BOOLEAN,
            outcome_notes          TEXT,
            contacted_at           DATETIME,
            PRIMARY KEY (id),
            FOREIGN KEY(company_id) REFERENCES companies (id),
            FOREIGN KEY(property_id) REFERENCES properties (id)
        )
    """)

    cur.execute(
        f"INSERT INTO outreach_log ({cols_csv}) "
        f"SELECT {cols_csv} FROM _outreach_log_pre_fix"
    )
    cur.execute("DROP TABLE _outreach_log_pre_fix")

    # Recreate the indexes SQLAlchemy originally built on this table.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_log_id "
        "ON outreach_log (id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_log_company_id "
        "ON outreach_log (company_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_outreach_log_property_id "
        "ON outreach_log (property_id)"
    )

    cur.execute("PRAGMA foreign_keys = ON")

    print("  ~ outreach_log.company_id: removed NOT NULL constraint")
    return 1


def ensure_companies(cur: sqlite3.Cursor) -> int:
    """Add any columns the Company ORM model expects that may be missing."""
    added = 0
    added += _add_column(cur, "companies", "last_modified_by_user", "DATETIME")
    added += _add_column(
        cur, "companies", "lease_trajectory", "TEXT DEFAULT 'AUTO' NOT NULL"
    )
    added += _add_column(cur, "companies", "lease_expiry_date", "DATE")
    # ── Snooze fields (mirror of properties) ──────────────────────────────────
    added += _add_column(cur, "companies", "snoozed_until",        "DATE")
    added += _add_column(cur, "companies", "snooze_reason",        "TEXT")
    added += _add_column(cur, "companies", "returned_from_snooze", "BOOLEAN")
    added += _add_column(cur, "companies", "expiry_priority_override", "BOOLEAN DEFAULT 0")
    # ── Building class (composite Match Score class-fit factor) ───────────────
    added += _add_column(cur, "companies", "current_building_class", "TEXT")

    # ── Medical/non-medical classification (soft match penalty) ────────────
    # Defaults to 0 (false) for all existing rows. Guarded so a partially-migrated
    # DB can never abort startup on this single column.
    try:
        added += _add_column(cur, "companies", "is_medical", "BOOLEAN NOT NULL DEFAULT 0")
    except Exception as _exc:
        print(f"  ! companies.is_medical add skipped: {_exc}")

    # ── Single SF field: current_sf_occupied (real occupied SF, never calculated) ──
    # Replaces the legacy current_sf / estimated_sf_needed pair. Add idempotently,
    # then port any existing legacy value across so no real SF data is lost on the
    # live DB. The whole block is guarded so a partially-migrated DB never aborts
    # startup. Backfill copies the IDENTICAL CoStar value under the new name — it
    # changes no business state (contacted records keep their SF unchanged).
    try:
        added += _add_column(cur, "companies", "current_sf_occupied", "INTEGER")
        # Backfill from legacy columns only where the new field is still empty.
        for legacy in ("current_sf", "estimated_sf_needed"):
            if _has_column(cur, "companies", legacy):
                cur.execute(
                    f"UPDATE companies SET current_sf_occupied = {legacy} "
                    f"WHERE current_sf_occupied IS NULL AND {legacy} IS NOT NULL"
                )
    except Exception as _exc:
        print(f"  ! companies.current_sf_occupied add/backfill skipped: {_exc}")

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
    try:
        olog_added = ensure_outreach_log(cur)
    except Exception as _exc:
        import logging as _log
        _log.getLogger(__name__).error(
            "ensure_schema: ensure_outreach_log failed (%s) — "
            "startup will continue",
            _exc,
        )
        try:
            conn.rollback()
            cur = conn.cursor()
        except Exception:
            pass
        olog_added = 0
    try:
        olog_fixed = fix_outreach_log_company_id_nullable(cur)
    except Exception as _exc:
        import logging as _log
        _log.getLogger(__name__).error(
            "ensure_schema: fix_outreach_log_company_id_nullable failed (%s) — "
            "startup will continue; re-run after investigating the error",
            _exc,
        )
        # Roll back any partial work from the failed migration so subsequent
        # migrations run against a clean transaction state.
        try:
            conn.rollback()
            # Re-open the cursor on the same connection so the rest of run() works.
            cur = conn.cursor()
        except Exception:
            pass
        olog_fixed = 0
    act_added    = ensure_activity_logs(cur)
    draft_added  = ensure_outreach_drafts(cur)
    bf_added     = backfill_lease_expiry_dates(cur)

    conn.commit()
    conn.close()

    total = prop_added + comp_added + olog_added + olog_fixed + act_added + draft_added + bf_added
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
