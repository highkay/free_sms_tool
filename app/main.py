from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.config import get_settings
from app.db.bootstrap import initialize_database
from app.db.core import Database
from app.db.repository import Repository
from app.logging import configure_logging, install_request_logging
from app.services.jobs import JobService
from app.services.sync import SyncService
from app.web.auth import install_web_ui_auth
from app.web.routes.apps import register_app_routes
from app.web.routes.auth import register_auth_routes
from app.web.routes.claims import register_claim_routes
from app.web.routes.dashboard import register_dashboard_routes
from app.web.routes.numbers import register_number_routes
from app.web.routes.providers import register_provider_routes
from app.web.routes.sync import register_sync_routes


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    database = Database(settings.database_path)
    initialize_database(database, settings)
    repository = Repository(database=database, settings=settings)
    sync_service = SyncService(database=database, settings=settings)
    job_service = JobService(database=database, settings=settings, sync_service=sync_service)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        description="Evidence-first free SMS pool manager with FastAPI, HTMX, SQLite, and API key-protected JSON APIs.",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.job_service = job_service
    install_request_logging(app)
    install_web_ui_auth(app, settings)

    @app.get("/healthz", include_in_schema=False)
    def healthcheck() -> JSONResponse:
        with database.connection() as conn:
            conn.execute("SELECT 1")
        return JSONResponse(
            {
                "status": "ok",
                "database_path": str(settings.database_path),
                "flaresolverr_url": settings.flaresolverr_url,
            }
        )

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))
    app.include_router(register_dashboard_routes(templates))
    app.include_router(register_number_routes(templates))
    app.include_router(register_auth_routes(templates))
    app.include_router(register_claim_routes(templates))
    app.include_router(register_app_routes(templates))
    app.include_router(register_provider_routes(templates))
    app.include_router(register_sync_routes(templates))
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
    return app


app = create_app()
