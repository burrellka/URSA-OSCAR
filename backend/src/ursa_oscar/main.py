"""FastAPI app factory.

The app holds a single DuckDBManager in `app.state.db` for the API surface
to share. The MCP server container opens its own read-only DuckDB connection
— it does NOT share state with this app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    analytics, events, exports, health, imports, manual_logs, nights, profile,
    system, timeseries, vocab,
)
from .config import get_settings
from .services.import_worker import ImportWorker
from .storage import profile_store, vocab_store
from .storage.db import DuckDBManager
from .storage.migrations import apply_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the DuckDB connection on startup; close on shutdown.

    Also runs Phase 3 first-start initialization for the file-backed
    profile store (``/data/profile.json``). If the file is absent the
    packaged community default is copied into place; if it's present we
    leave it untouched.

    Phase 4 Ticket 2 — also spawns the async ImportWorker that drains
    the import_jobs queue. The worker runs as an asyncio task in this
    process; it's stopped on shutdown so the container can exit
    cleanly without leaking the task.
    """
    settings = get_settings()
    db = DuckDBManager(settings.db_path, read_only=False)
    apply_migrations(db)
    # First-start: ensure /data/profile.json and /data/vocab.json exist.
    # Both sit next to the DuckDB file on the same mounted volume; the
    # bidirectional sync service (Phase 3 Item 3C/D) keeps them in
    # alignment for the medication_name autocomplete field.
    profile_path = settings.db_path.parent / "profile.json"
    vocab_path = settings.db_path.parent / "vocab.json"
    profile_store.ensure_initialized(profile_path)
    vocab_store.ensure_initialized(vocab_path)
    app.state.db = db

    worker = ImportWorker(db)
    worker.start()
    app.state.import_worker = worker
    try:
        yield
    finally:
        await worker.stop()
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
    # vocab MUST be registered before manual_logs so its more-specific
    # /api/v1/manual-logs/vocab path wins over manual_logs's /{log_id}
    # pattern, which would otherwise match 'vocab' as a stringified id
    # and fail validation with 422.
    app.include_router(vocab.router)
    app.include_router(manual_logs.router)
    app.include_router(exports.router)
    app.include_router(system.router)
    app.include_router(profile.router)
    app.include_router(analytics.router)

    return app


# Module-level app for `uvicorn ursa_oscar.main:app`
app = create_app()
