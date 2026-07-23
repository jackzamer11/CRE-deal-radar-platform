"""Backfill: turn freeform activity-log text into structured observations.

Reads every activity log, extracts stated tenant requirements with Anthropic,
and writes them to the `observations` table so they show up in the Review queue.

ACTIVITY LOGS ARE NEVER MODIFIED OR DELETED by this script — it only reads them
and writes new rows to `observations` / `intel_activity_extractions`.

Usage (from backend/, with venv active and ANTHROPIC_API_KEY set):

    python mine_activity_logs.py              # mine everything not yet mined
    python mine_activity_logs.py --limit 5    # just the next 5 (good for a trial)
    python mine_activity_logs.py --status     # show progress, mine nothing
    python mine_activity_logs.py --force      # re-mine logs already processed
"""

import argparse
import sys

from app.database import SessionLocal
from app.models import (  # noqa: F401 — register all mappers
    property, company, opportunity, activity, outreach_log, tenant_class_feedback,
)
from app.models.activity import ActivityLog
from app.models.intel import IntelActivityExtraction
from app.services.activity_intel_service import mine_all_activity_logs
from app.services.document_extraction_service import MissingAPIKeyError


def show_status(db) -> None:
    total = db.query(ActivityLog).count()
    rows = db.query(IntelActivityExtraction).all()
    mined = len({r.activity_log_id for r in rows})
    facts = sum(r.fields_found for r in rows)
    failed = sum(1 for r in rows if r.status == "failed")
    print(f"activity logs   : {total}")
    print(f"mined           : {mined}")
    print(f"remaining       : {max(0, total - mined)}")
    print(f"facts extracted : {facts}")
    print(f"failed          : {failed}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.status:
            show_status(db)
            return 0

        def progress(i: int, total: int) -> None:
            print(f"\r  mining {i}/{total} …", end="", flush=True)

        try:
            result = mine_all_activity_logs(
                db, limit=args.limit, force=args.force, progress=progress,
            )
        except MissingAPIKeyError as exc:
            print(f"\n{exc}")
            return 2

        print()
        print(f"processed : {result['processed']}")
        print(f"facts     : {result['facts']}")
        print(f"skipped   : {result['skipped']} (already mined)")
        print(f"failed    : {result['failed']}")
        print()
        show_status(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
