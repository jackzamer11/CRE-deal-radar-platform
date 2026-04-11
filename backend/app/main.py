from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.api.routes import properties, companies, opportunities, activity, dashboard
from app.ingestion.scheduler import start_scheduler, stop_scheduler
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Proprietary deal identification and scoring system for NoVA office assets",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(dashboard.router, prefix="/api")
    app.include_router(properties.router, prefix="/api")
    app.include_router(companies.router, prefix="/api")
    app.include_router(opportunities.router, prefix="/api")
    app.include_router(activity.router, prefix="/api")

    @app.on_event("startup")
    def on_startup():
        init_db()
        start_scheduler()

    @app.on_event("shutdown")
    def on_shutdown():
        stop_scheduler()

    @app.post("/api/pipeline/run", tags=["pipeline"])
    def run_pipeline_now():
        """Trigger the full data refresh pipeline on-demand."""
        from app.ingestion.pipeline import run_full_pipeline
        return run_full_pipeline()

    @app.get("/health")
    def health():
        return {"status": "ok", "system": settings.app_name}

    return app


app = create_app()
