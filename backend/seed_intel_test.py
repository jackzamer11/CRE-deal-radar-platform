"""Seed sample lease facts so Jack can watch the Intel signal engine work.

Creates three companies' worth of observations:
  - #901: verified lease expiring in 90 days  -> top-ranked "Lease Expiring"
  - #902: verified lease expiring in 400 days -> filtered out (beyond 1 year)
  - #903: UNVERIFIED lease expiring in 200 days -> lower-ranked "Verify First"

Run this, then click "Generate Opportunities" on the Intel page.
"""

from datetime import date, timedelta

from app.database import SessionLocal
from app.models import property, company, opportunity, activity, outreach_log, tenant_class_feedback  # noqa: F401
from app.models.observation import Observation


def _lease(db, entity_id, expiration, *, expiration_verified):
    fields = {
        "tenant_name": f"Sample Tenant {entity_id}",
        "premises_sqft": "12000",
        "commencement_date": "2020-06-01",
        "base_rent_annual": "480000",
    }
    for field, value in fields.items():
        db.add(Observation(entity_type="company", entity_id=entity_id, field=field,
                           value=value, confidence=0.92, human_verified=True,
                           source_doc="sample_lease.pdf", source_page=1))
    db.add(Observation(entity_type="company", entity_id=entity_id, field="expiration_date",
                       value=expiration.isoformat(), confidence=0.92,
                       human_verified=expiration_verified,
                       source_doc="sample_lease.pdf", source_page=2))


def seed_intel_test():
    db = SessionLocal()
    try:
        existing = (
            db.query(Observation)
            .filter(Observation.entity_id.in_([901, 902, 903]))
            .count()
        )
        if existing:
            print(f"intel test facts already seeded ({existing} rows)")
            return
        today = date.today()
        _lease(db, 901, today + timedelta(days=90), expiration_verified=True)
        _lease(db, 902, today + timedelta(days=400), expiration_verified=True)
        _lease(db, 903, today + timedelta(days=200), expiration_verified=False)
        db.commit()
        print("seeded intel test facts for companies 901, 902, 903")
    finally:
        db.close()


if __name__ == "__main__":
    seed_intel_test()
