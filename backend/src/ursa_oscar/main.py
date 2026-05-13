"""FastAPI app factory.

The app holds a single DuckDBManager in `app.state.db` for the API surface
to share. The MCP server container opens its own read-only DuckDB connection
— it does NOT share state with this app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import events, exports, health, imports, manual_logs, nights, system, timeseries
from .config import get_settings
from .storage.db import DuckDBManager
from .storage.migrations import apply_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the DuckDB connection on startup; close on shutdown."""
    settings = get_settings()
    db = DuckDBManager(settings.db_path, read_only=False)
    apply_migrations(db)
    app.state.db = db
    try:
        yield
    finally:
        db.close()


def create_app() -> FastAPI:
    """Build a FastAPI app instance with the URSA-OSCAR API routes mounted."""
    app = FastAPI(
        title="URSA-OSCAR API",
        description=(
            "Backend for the URSA-OSCAR CPAP analytics platform. "
            "REST endpoints for nightly summaries, events, and import jobs. "
            "Phase 1 surface — manual logs and exports stubbed for Phase 3."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # Permissive CORS for the homelab. Frontend dev server (Vite at :5173)
    # and prod (whatever Cloudflare Tunnel hostname) both need to reach this.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(nights.router)
    app.include_router(events.router)
    app.include_router(timeseries.router)
    app.include_router(imports.router)
    app.include_router(manual_logs.router)
    app.include_router(exports.router)
    app.include_router(system.router)

    return app


# Module-level app for `uvicorn ursa_oscar.main:app`
app = create_app()
