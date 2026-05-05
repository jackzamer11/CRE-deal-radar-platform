"""
Migration: add CoStar enrichment fields, three property-side outreach scores,
in-place rent provenance, and a user-modification timestamp to the properties
table.

IMPORTANT: This migration does not modify any existing column values.
           It only adds new columns; every existing row is left exactly
           as-is. All new columns are nullable / default 0 so they cannot
           overwrite anything.

Columns added:
    Enrichment (Part 1):
      - star_rating              INTEGER
      - sf_avail                 INTEGER
      - landlord_representative  TEXT
      - landlord_rep_contact     TEXT
      - sales_company            TEXT
      - sales_contact            TEXT
      - tenancy                  TEXT
      - stories                  INTEGER
      - parking_ratio            REAL
    In-place rent enrichment (Part 7):
      - in_place_rent_source     TEXT
      - in_place_rent_last_verified TEXT
    Property-side outreach scores (Part 3):
      - tenant_match_score       REAL DEFAULT 0
      - listing_rep_score        REAL DEFAULT 0
      - acquisition_score        REAL DEFAULT 0
      - dominant_score_type      TEXT
    User-modification audit (consistency with companies):
      - last_modified_by_user    TEXT

Run from backend/ directory:
    python -m migrations.add_property_fields
    OR
    python migrations/add_property_fields.py

Safe to re-run — idempotent (each column is added only if it does not exist).
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "deal_radar.db")

NEW_COLUMNS = [
    # Part 1: CoStar enrichment
    ("star_rating",                  "INTEGER"),
    ("sf_avail",                     "INTEGER"),
    ("landlord_representative",      "TEXT"),
    ("landlord_rep_contact",         "TEXT"),
    ("sales_company",                "TEXT"),
    ("sales_contact",                "TEXT"),
    ("tenancy",                      "TEXT"),
    ("stories",                      "INTEGER"),
    ("parking_ratio",                "REAL"),
    # Part 7: in-place rent enrichment provenance
    ("in_place_rent_source",         "TEXT"),
    ("in_place_rent_last_verified",  "TEXT"),
    # Part 3: property-side outreach scores
    ("tenant_match_score",           "REAL DEFAULT 0"),
    ("listing_rep_score",            "REAL DEFAULT 0"),
    ("acquisition_score",            "REAL DEFAULT 0"),
    ("dominant_score_type",          "TEXT"),
    # Audit: tracks user-initiated edits (mirror of companies.last_modified_by_user)
    ("last_modified_by_user",        "TEXT"),
]


def run():
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"Database not found at {db} — nothing to migrate.")
        return

    conn = sqlite3.connect(db)
    cur  = conn.cursor()

    cur.execute("PRAGMA table_info(properties)")
    existing = {row[1] for row in cur.fetchall()}

    added = skipped = 0
    for name, decl in NEW_COLUMNS:
        if name in existing:
            print(f"  {name}: already exists, skipping.")
            skipped += 1
        else:
            cur.execute(f"ALTER TABLE properties ADD COLUMN {name} {decl}")
            print(f"  {name}: added ({decl}).")
            added += 1

    conn.commit()
    conn.close()
    print(f"\nDone — {added} added, {skipped} skipped.")


if __name__ == "__main__":
    import sys
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    run()
