from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.dashboard import DailyBriefing
from app.services.output_engine import generate_daily_briefing

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/briefing", response_model=DailyBriefing)
def get_daily_briefing(db: Session = Depends(get_db)):
    """
    Returns the full daily briefing:
      - Stats snapshot
      - Top 10 immediate deals
      - Top 5 pre-market predictions
      - Top 5 tenant-driven opportunities
    """
    return generate_daily_briefing(db)
