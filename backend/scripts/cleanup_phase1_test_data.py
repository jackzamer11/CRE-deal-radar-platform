"""Delete the two contacts created while verifying phase one, and nothing else.

Phase-one verification left behind:
  - Jack Zamer   <jackzamer1@gmail.com>          (Jack testing against himself)
  - Test Person  <test.person@testco-example.com>
  - the auto-created company "Testco Example"
  - their entries, including a run of stage-change events from clicking between
    the stage pills

It deletes those and only those. The pre-existing entries are never touched:
the script counts them before and after and refuses to report success if the
number moved.

Usage — from backend/, with the venv active:

    python -m scripts.cleanup_phase1_test_data                      # dry run
    python -m scripts.cleanup_phase1_test_data --confirm            # writes
    python -m scripts.cleanup_phase1_test_data --db-path deal_radar.backup-20260911-174558.db

Without --confirm it prints exactly what it would delete and writes nothing.
It never runs on startup.
"""
import argparse
import sys
from pathlib import Path

# Importable as `python -m scripts.x` from backend/, and as a plain file path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._dbpath import (  # noqa: E402
    make_session, require_contact_schema, resolve_db_path,
)

# Identified by email address, which is the identity key everywhere else in the
# contact code. A name match would be the wrong tool: "Jack Zamer" is also the
# user, and a real contact could share a name with a test one.
TEST_CONTACT_EMAILS = [
    "jackzamer1@gmail.com",
    "test.person@testco-example.com",
]
# Matched on the email domain the auto-creation derived it from, so a
# hand-entered company that happens to be named similarly is left alone.
TEST_COMPANY_DOMAIN = "testco-example.com"
TEST_COMPANY_NAME = "Testco Example"

# What the database should hold once the test data is gone.
EXPECTED_REMAINING_ENTRIES = 355


def _plan(db):
    """Everything the script would delete, resolved but not yet deleted."""
    from app.models.activity import ActivityLog
    from app.models.company import Company
    from app.models.contact import Contact, ContactFact
    from app.services.contact_service import STAGE_CHANGE_ACTION

    contacts = (
        db.query(Contact).filter(Contact.email.in_(TEST_CONTACT_EMAILS)).all()
    )
    contact_ids = [c.id for c in contacts]

    entries = (
        db.query(ActivityLog).filter(ActivityLog.contact_id.in_(contact_ids)).all()
        if contact_ids else []
    )
    facts = (
        db.query(ContactFact).filter(ContactFact.contact_id.in_(contact_ids)).all()
        if contact_ids else []
    )
    company = (
        db.query(Company)
        .filter(Company.email_domain == TEST_COMPANY_DOMAIN)
        .filter(Company.name == TEST_COMPANY_NAME)
        .first()
    )

    return {
        "contacts": contacts,
        "entries": entries,
        "facts": facts,
        "company": company,
        "stage_events": [
            e for e in entries if (e.action_type or "") == STAGE_CHANGE_ACTION
        ],
        "legacy_stage_events": [
            # Pre-cleanup rows written before stage changes had their own type.
            e for e in entries
            if (e.action_type or "") == "SIGNAL_UPDATE"
            and (e.action_taken or "").startswith("Stage:")
        ],
    }


def _report(db, plan, total_entries):
    from app.models.activity import ActivityLog

    print("=" * 72)
    print("PHASE-ONE TEST DATA CLEANUP")
    print("=" * 72)
    print(f"Entries in the database right now : {total_entries}")

    attached = db.query(ActivityLog).filter(
        ActivityLog.contact_id.isnot(None)
    ).count()
    print(f"  attached to a contact           : {attached}")
    print(f"  unattached (the pre-existing set): {total_entries - attached}")
    print()

    if not plan["contacts"]:
        print("No test contacts found — nothing to delete.")
        return

    print("WILL DELETE")
    print("-" * 72)
    for c in plan["contacts"]:
        n = len([e for e in plan["entries"] if e.contact_id == c.id])
        f = len([x for x in plan["facts"] if x.contact_id == c.id])
        print(f"  contact #{c.id:<4} {c.name}  <{c.email}>")
        print(f"      stage={c.stage}  entries={n}  facts={f}")

    print()
    print(f"  {len(plan['entries'])} entries:")
    for e in plan["entries"]:
        text = (e.action_taken or "")[:58]
        print(f"      #{e.id:<5} {e.log_date}  {(e.action_type or ''):<13} {text}")

    if plan["facts"]:
        print()
        print(f"  {len(plan['facts'])} facts:")
        for f in plan["facts"]:
            print(f"      #{f.id:<5} {(f.fact_text or '')[:60]}")

    if plan["company"]:
        c = plan["company"]
        print()
        print(f"  company #{c.id} {c.name} ({c.company_id}, domain {c.email_domain})")
    else:
        print()
        print(f"  company: no '{TEST_COMPANY_NAME}' found — nothing to delete")

    stage_n = len(plan["stage_events"]) + len(plan["legacy_stage_events"])
    print()
    print(f"  of which {stage_n} are stage-change events "
          f"({len(plan['legacy_stage_events'])} written before stage changes "
          f"had their own type)")

    print()
    print(f"Entries remaining after this runs : "
          f"{total_entries - len(plan['entries'])}")
    print(f"Expected                          : {EXPECTED_REMAINING_ENTRIES}")
    print("=" * 72)


def _delete(db, plan):
    from app.models.contact import ContactFact
    from app.services.activity_intel_service import purge_log_intel

    entry_ids = [e.id for e in plan["entries"]]

    # A fact on another contact could cite one of these entries as its source.
    # Null the pointer rather than leaving a dangling id behind.
    if entry_ids:
        db.query(ContactFact).filter(
            ContactFact.source_entry_id.in_(entry_ids)
        ).update({"source_entry_id": None}, synchronize_session=False)
        for entry_id in entry_ids:
            try:
                purge_log_intel(db, entry_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! intel purge skipped for entry {entry_id}: {exc}")

    for fact in plan["facts"]:
        db.delete(fact)
    for entry in plan["entries"]:
        db.delete(entry)
    for contact in plan["contacts"]:
        db.delete(contact)
    if plan["company"]:
        db.delete(plan["company"])

    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="Actually delete. Without it nothing is written.")
    ap.add_argument("--db-path", default=None,
                    help="Operate on this SQLite file instead of the configured one.")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db_path)
    print(f"Database: {db_path}\n")
    require_contact_schema(db_path)
    db = make_session(db_path)

    try:
        from app.models.activity import ActivityLog

        total_before = db.query(ActivityLog).count()
        plan = _plan(db)
        _report(db, plan, total_before)

        if not args.confirm:
            print()
            print("DRY RUN — nothing was written.")
            print("Re-run with --confirm to delete the above.")
            return 0

        if not plan["contacts"] and not plan["company"]:
            print("\nNothing to delete. Already clean.")
            return 0

        print("\nDeleting…")
        _delete(db, plan)

        total_after = db.query(ActivityLog).count()
        print(f"Done. Entries remaining: {total_after}")

        if total_after != EXPECTED_REMAINING_ENTRIES:
            print()
            print(f"WARNING: expected {EXPECTED_REMAINING_ENTRIES} entries to "
                  f"remain, found {total_after}.")
            print("The deletion committed. Check the entry list above against "
                  "what you expected before running the backfill.")
            return 1

        print(f"Verified: {total_after} pre-existing entries intact.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
