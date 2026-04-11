"""
APScheduler — Daily Pipeline Automation
=========================================
Schedule:
  Daily at 06:00 EST — full data refresh + signal recalculation
  Weekly at 07:00 EST Sundays — deep ownership research refresh

To extend: add additional jobs for real-time CoStar webhook events,
LinkedIn API calls, or public records scraping.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/New_York")


def _daily_pipeline_job():
    from app.ingestion.pipeline import run_full_pipeline
    logger.info("[Scheduler] Triggering daily pipeline")
    try:
        result = run_full_pipeline()
        logger.info(f"[Scheduler] Daily pipeline complete: {result}")
    except Exception as e:
        logger.error(f"[Scheduler] Daily pipeline failed: {e}", exc_info=True)


def start_scheduler():
    # Daily at 06:00 EST
    scheduler.add_job(
        _daily_pipeline_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="America/New_York"),
        id="daily_pipeline",
        replace_existing=True,
        name="Daily Deal Radar Pipeline",
    )
    scheduler.start()
    logger.info("[Scheduler] APScheduler started — daily pipeline at 06:00 EST")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler stopped")
