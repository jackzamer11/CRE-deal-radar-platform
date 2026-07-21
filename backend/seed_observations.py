from app.database import SessionLocal
from app.models import property, company, opportunity, activity, outreach_log, tenant_class_feedback  # noqa: F401
from app.models.observation import Observation


def seed_observations():
    db = SessionLocal()
    try:
        existing = db.query(Observation).count()
        if existing:
            print(f"observations already seeded: {existing}")
            return

        rows = [
            Observation(
                entity_type="company",
                entity_id=1,
                field="expiration_date",
                value="2026-10-01",
                confidence=0.95,
                source_doc="sample_lease.pdf",
                source_page=2,
                source_snippet="Lease expires on October 1, 2026.",
                human_verified=False,
            ),
            Observation(
                entity_type="company",
                entity_id=1,
                field="premises_sqft",
                value="18000",
                confidence=0.4,
                source_doc="sample_lease.pdf",
                source_page=1,
                source_snippet="The premises contain approximately 18,000 SF.",
                human_verified=False,
            ),
            Observation(
                entity_type="company",
                entity_id=2,
                field="base_rent_annual",
                value=None,
                confidence=0.2,
                source_doc="sample_lease.pdf",
                source_page=3,
                source_snippet="No annual rent figure appears in the excerpt.",
                human_verified=False,
            ),
        ]
        db.add_all(rows)
        db.commit()
        print("seeded 3 observations")
    finally:
        db.close()


if __name__ == "__main__":
    seed_observations()
