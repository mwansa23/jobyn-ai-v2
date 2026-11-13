
"""FastAPI application entry point for Jobyn AI."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api.router import api_router
from backend.core.config import get_settings
from backend.core.errors import register_exception_handlers


# ---------------------------------------------------------------------------
# Application paths
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent

_DEMO_HTML = _BASE_DIR / "demo.html"
_ADMIN_HTML = _BASE_DIR / "admin.html"


# ---------------------------------------------------------------------------
# CORS configuration
# ---------------------------------------------------------------------------

# The production Cloudflare Worker hosting the Jobyn frontend.
_PRODUCTION_FRONTEND_ORIGINS = [
    "https://jobyn-landing-page.mwansamatimba.workers.dev",
]


def get_allowed_origins() -> list[str]:
    """Return normalized, explicitly permitted frontend origins."""

    current_settings = get_settings()

    configured_origins = getattr(
        current_settings,
        "BACKEND_CORS_ORIGINS",
        [],
    )

    # Support both list-based and comma-separated configuration.
    if isinstance(configured_origins, str):
        configured_origins = configured_origins.split(",")

    origins = {
        origin.strip().rstrip("/")
        for origin in configured_origins
        if isinstance(origin, str) and origin.strip()
    }

    origins.update(_PRODUCTION_FRONTEND_ORIGINS)

    return sorted(origins)


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize application resources during startup.

    SQLite:
        Automatically creates missing database tables for development
        and demo environments.

    PostgreSQL:
        Schema management remains the responsibility of Alembic.

    Demo jobs:
        Seeded idempotently when JOBYN_SEED_DEMO_JOBS is enabled.
        Seeding failures must not prevent the API from starting.
    """

    current_settings = get_settings()

    # ---------------------------------------------------------------
    # SQLite initialization
    # ---------------------------------------------------------------

    database_url = current_settings.DATABASE_URL

    if database_url.startswith("sqlite"):
        # Import models before create_all() so SQLAlchemy metadata
        # contains all registered model definitions.
        from backend.database.base import Base
        from backend.database.session import engine

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    # ---------------------------------------------------------------
    # Optional demo data seeding
    # ---------------------------------------------------------------

    seed_enabled = (
        os.getenv("JOBYN_SEED_DEMO_JOBS", "true")
        .strip()
        .lower()
        not in {"0", "false", "no", "off"}
    )

    if seed_enabled:
        try:
            from backend.database.session import async_session_maker
            from backend.services.demo_seed import seed_demo_jobs

            async with async_session_maker() as session:
                await seed_demo_jobs(session)

        except Exception:
            # Demo data must never prevent API startup.
            # Log the exception through the application's configured
            # logging system if available.
            import logging

            logging.getLogger(__name__).exception(
                "Demo job seeding failed; continuing API startup."
            )

    yield


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the Jobyn AI FastAPI application."""

    app = FastAPI(
        title="Jobyn AI",
        description="AI-powered career intelligence platform",
        version="2.0.0",
        lifespan=_lifespan,
    )

    # ---------------------------------------------------------------
    # CORS middleware
    # ---------------------------------------------------------------

    allowed_origins = get_allowed_origins()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "HEAD",
        ],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Origin",
            "X-Requested-With",
        ],
        expose_headers=[
            "Content-Disposition",
        ],
        max_age=600,
    )

    # ---------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------

    register_exception_handlers(app)

    # ---------------------------------------------------------------
    # API router
    # ---------------------------------------------------------------

    app.include_router(api_router)

    # ---------------------------------------------------------------
    # Root endpoint
    # ---------------------------------------------------------------

    @app.get(
        "/",
        tags=["system"],
    )
    async def root() -> dict[str, str]:
        """Return basic service information."""

        return {
            "service": "Jobyn AI",
            "status": "running",
            "version": "2.0.0",
        }

    # ---------------------------------------------------------------
    # Demo interface
    # ---------------------------------------------------------------

    @app.get(
        "/demo",
        tags=["demo"],
        include_in_schema=False,
    )
    async def demo():
        """Serve the single-page MVP demo interface."""

        return FileResponse(
            _DEMO_HTML,
            media_type="text/html",
        )

    # ---------------------------------------------------------------
    # Admin interface
    # ---------------------------------------------------------------

    @app.get(
        "/admin",
        tags=["admin"],
        include_in_schema=False,
    )
    async def admin_page():
        """Serve the admin login/import interface."""

        return FileResponse(
            _ADMIN_HTML,
            media_type="text/html",
        )

    # ---------------------------------------------------------------
    # Health checks
    # ---------------------------------------------------------------

    @app.get(
        "/api/v1/health",
        tags=["health"],
    )
    async def health_check() -> dict[str, str]:
        """Basic application health check."""

        return {
            "status": "ok",
            "service": "Jobyn AI",
        }

    @app.get(
        "/api/v1/health/ready",
        tags=["health"],
    )
    async def readiness_check() -> dict[str, str]:
        """Application readiness check."""

        return {
            "status": "ready",
            "service": "Jobyn AI",
        }

    # ---------------------------------------------------------------
    # LLM health check
    # ---------------------------------------------------------------

    @app.get(
        "/api/v1/health/llm",
        tags=["health"],
    )
    async def llm_health() -> dict[str, Any]:
        """Return the configured LLM provider and model."""

        from backend.ai.llm import get_llm_config

        config = get_llm_config()

        if config["active_provider"] == "freellmapi":
            configured = bool(
                config["freellmapi_api_key_configured"]
                and config["freellmapi_base_url"]
            )

            model = config["freellmapi_model"]
            provider = "freellmapi"

        else:
            configured = bool(config["api_key_configured"])
            model = config["model"]
            provider = config["provider"]

        return {
            "llm_configured": configured,
            "provider": provider,
            "model": model,
        }

    return app


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = create_app()
