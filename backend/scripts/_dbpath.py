"""Shared database-path resolution for the one-off scripts.

Same rule as migrations/ensure_schema.py: the path comes from
settings.database_url, never a hardcoded string, so a script always targets the
same file the app does. `--db-path` overrides it so a script can be rehearsed
against a backup copy before it is ever pointed at live data.
"""
import os
from typing import Optional


def resolve_db_path(override: Optional[str] = None) -> str:
    """Absolute path of the SQLite file to operate on."""
    if override:
        return os.path.abspath(override)

    try:
        from app.config import settings
        url = settings.database_url
    except Exception:
        url = os.environ.get("DATABASE_URL", "sqlite:///./deal_radar.db")

    if not url.startswith("sqlite:///"):
        raise ValueError(f"These scripts only support SQLite; got: {url!r}")
    # sqlite:////abs/path  -> /abs/path  (4 slashes = absolute)
    # sqlite:///./rel/path -> ./rel/path (3 slashes = relative to CWD)
    return os.path.abspath(url[len("sqlite:///"):])


def load_env() -> None:
    """Load backend/.env into the process environment.

    The app does this at app.config import time, so ANTHROPIC_API_KEY lives in
    backend/.env rather than the shell. A script invoked with --db-path never
    touches app.config, so without this the key would look missing and the
    prose step would silently fall back to address matching.

    override=False: a key already exported in the shell wins.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(
            os.path.join(os.path.dirname(__file__), "..", ".env"), override=False,
        )
    except Exception:  # noqa: BLE001 — a missing .env is not an error
        pass


def register_models() -> None:
    """Import every ORM model so SQLAlchemy can resolve its relationships.

    ActivityLog -> OutreachLog and friends are declared by string name, so a
    script that imports only the models it touches gets a mapper-configuration
    error on the first query. Same three imports the test suite does.
    """
    import app.models                 # noqa: F401
    import app.models.outreach_log    # noqa: F401
    import app.models.outreach_draft  # noqa: F401


def require_contact_schema(db_path: str) -> None:
    """Stop with an actionable message if this database predates phase one.

    The backups taken before contact threads shipped have no contacts table and
    no activity_logs.contact_id, so a script pointed at one would otherwise die
    on a raw SQL error. Migrations never run implicitly here — a script that
    silently reshaped a database Jack pointed it at would be worse than one
    that stops and says what to run.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'")
        has_contacts = cur.fetchone() is not None
        cur.execute("PRAGMA table_info(activity_logs)")
        cols = {row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    missing = []
    if not has_contacts:
        missing.append("the contacts table")
    for col in ("contact_id", "company_stamp_id", "source_message_id",
                "stage_from", "stage_to"):
        if col not in cols:
            missing.append(f"activity_logs.{col}")

    if not missing:
        return

    lines = [
        "",
        f"This database is missing {', '.join(missing)}.",
        "It predates the contact-threads schema, so nothing has been read or",
        "written. Apply the migration to it first, from backend/:",
        "",
        "  bash:",
        f'    DATABASE_URL="sqlite:///{db_path}" python -m migrations.ensure_schema',
        "",
        "  PowerShell:",
        f'    $env:DATABASE_URL = "sqlite:///{db_path}"',
        "    python -m migrations.ensure_schema",
        r"    Remove-Item Env:\DATABASE_URL",
        "",
        "Then re-run this script.",
        "",
    ]
    raise SystemExit("\n".join(lines))


def make_session(db_path: str):
    """A SQLAlchemy session bound to one specific database file.

    Built explicitly rather than reusing app.database.SessionLocal so
    `--db-path` genuinely redirects the script: the app's engine is bound at
    import time to whatever settings.database_url said.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    register_models()

    if not os.path.exists(db_path):
        raise SystemExit(f"No database at {db_path}")

    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False},
    )
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()
