from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={
        "check_same_thread": False,  # SQLite only
        # How long a save waits for another writer before failing. SQLite's
        # default is 5 seconds; a busy moment (the evening email check while a
        # mining run saves) could outlast that and fail with "database is
        # locked". Writers wait their turn instead.
        "timeout": 30,
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import property, company, opportunity, activity, contact, outreach_log, observation, document, lease, submarket  # noqa: F401
    Base.metadata.create_all(bind=engine)
