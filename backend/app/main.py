import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db

from app.database import init_db
from app.api.routes import properties, companies, opportunities, activity, dashboard
from app.ingestion.scheduler import start_scheduler, stop_scheduler
from app.config import settings, NOVA_OFFICE_BENCHMARKS, SUBMARKET_BENCHMARKS

# Path to the built React frontend (relative to this file → ../../frontend/dist)
FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description="Proprietary deal identification and scoring system for NoVA office assets",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ────────────────────────────────────────────────────────
    app.include_router(dashboard.router,     prefix="/api")
    app.include_router(properties.router,    prefix="/api")
    app.include_router(companies.router,     prefix="/api")
    app.include_router(opportunities.router, prefix="/api")
    app.include_router(activity.router,      prefix="/api")

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

    @app.post("/api/pipeline/refresh-public-records", tags=["pipeline"])
    def refresh_public_records_now(db: Session = Depends(get_db)):
        """Pull latest data from Arlington County Open Data and Fairfax iCARE."""
        from app.ingestion.pipeline import refresh_public_records
        enriched = refresh_public_records(db)
        return {"status": "ok", "properties_enriched": enriched}

    @app.get("/api/benchmarks/nova", tags=["benchmarks"])
    def nova_benchmarks():
        """Return CBRE Q1 2026 NoVA office benchmarks (market-wide and per submarket)."""
        return {
            "nova": NOVA_OFFICE_BENCHMARKS,
            "submarkets": SUBMARKET_BENCHMARKS,
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "system": settings.app_name}

    # ── Serve built React frontend ────────────────────────────────────────
    if os.path.isdir(FRONTEND_DIST):
        # Serve static assets (JS, CSS, images)
        app.mount(
            "/assets",
            StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")),
            name="assets",
        )

        # Catch-all: serve index.html for any non-API route (React Router)
        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str):
            index = os.path.join(FRONTEND_DIST, "index.html")
            return FileResponse(index)

    return app


app = create_app()
