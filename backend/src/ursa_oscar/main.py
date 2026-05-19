"""FastAPI app factory.

The app holds a single DuckDBManager in `app.state.db` for the API surface
to share. The MCP server container opens its own read-only DuckDB connection
— it does NOT share state with this app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ai_proxy.config_store import ConfigStore as AiConfigStore
from .ai_proxy.prompt import TemplateStore as AiTemplateStore
from .ai_proxy.secrets import SecretStore, resolve_secret_key
from .auth import require_auth
from .auth.rate_limit import LoginRateLimiter
from .auth.routes import router as auth_router
from .auth.store import AuthStore
from .auth.service_tokens import (
    ServiceTokenError,
    ensure_all_service_tokens,
)
from .auth.tokens import resolve_jwt_secret
from .api import (
    ai, analytics, events, exports, exports_oscar, health, imports,
    manual_logs, nights, profile, reports, system, timeseries, vocab,
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

    # Phase 5 — AI proxy state. Secret key resolution may write a
    # generated key to /data/secret_key.gen on first start; that's
    # logged loudly so the operator notices.
    data_dir = settings.db_path.parent
    secret_key = resolve_secret_key(data_dir)
    app.state.ai_secrets = SecretStore(
        key=secret_key,
        store_path=data_dir / "secrets.enc",
    )
    app.state.ai_config_store = AiConfigStore(
        store_path=data_dir / "ai_config.json",
    )
    # 0.9.10 — file-backed editable system-prompt template. First read
    # returns the in-code DEFAULT_TEMPLATE; first "Save to template" via
    # the Settings UI writes the file, after which the operator's edits
    # are durable.
    app.state.ai_template_store = AiTemplateStore(
        store_path=data_dir / "system_prompt_template.txt",
    )

    # Phase 6.4 — single-user auth. JWT secret resolution mirrors
    # Phase 5's master.key flow: env override > persistent file >
    # first-boot generation. AuthStore manages /data/auth.json
    # (bootstrap state + password hash). LoginRateLimiter is in-memory.
    app.state.jwt_secret = resolve_jwt_secret(data_dir)
    app.state.auth_store = AuthStore(store_path=data_dir / "auth.json")
    app.state.auth_limiter = LoginRateLimiter()

    # Phase 6.4.1 — auto-mint service tokens for the MCP + watcher
    # containers. Mirrors the master.key auto-resolve pattern: missing
    # or expiring tokens are silently re-minted at startup, so the
    # operator never has to babysit copy-paste flows.
    try:
        ensure_all_service_tokens(data_dir, app.state.jwt_secret)
    except ServiceTokenError:
        # Logged at ERROR level inside the resolver; we don't want to
        # take down the API over a service-token issue (the operator
        # can still set the explicit env-var overrides as a fallback).
        # But we DO surface it loudly.
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "Phase 6.4.1 service-token bootstrap failed — MCP and watcher "
            "will need explicit URSA_OSCAR_MCP_API_TOKEN / "
            "URSA_OSCAR_WATCHER_TOKEN env vars to function."
        )

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

    # Phase 6.4 — Auth boundary.
    #
    # Two routers are AUTH-OPEN per Decision 7:
    #   - health.router      — `/healthz` (Docker healthcheck, monitoring)
    #   - auth_router        — `/api/v1/auth/*` (the auth endpoints
    #                          themselves; they handle their own
    #                          credential checks internally)
    #
    # Every other router gets `Depends(require_auth)` applied at the
    # include_router level. One line, all routes on the router
    # protected. Decision 7 wins by construction: a developer who adds
    # a new endpoint to an existing protected router can't forget to
    # auth-guard it.
    app.include_router(health.router)
    app.include_router(auth_router)

    _AUTH_REQUIRED = [Depends(require_auth)]
    app.include_router(nights.router, dependencies=_AUTH_REQUIRED)
    app.include_router(events.router, dependencies=_AUTH_REQUIRED)
    app.include_router(timeseries.router, dependencies=_AUTH_REQUIRED)
    app.include_router(imports.router, dependencies=_AUTH_REQUIRED)
    # vocab MUST be registered before manual_logs so its more-specific
    # /api/v1/manual-logs/vocab path wins over manual_logs's /{log_id}
    # pattern, which would otherwise match 'vocab' as a stringified id
    # and fail validation with 422.
    app.include_router(vocab.router, dependencies=_AUTH_REQUIRED)
    app.include_router(manual_logs.router, dependencies=_AUTH_REQUIRED)
    app.include_router(exports.router, dependencies=_AUTH_REQUIRED)
    app.include_router(exports_oscar.router, dependencies=_AUTH_REQUIRED)
    app.include_router(system.router, dependencies=_AUTH_REQUIRED)
    app.include_router(profile.router, dependencies=_AUTH_REQUIRED)
    app.include_router(analytics.router, dependencies=_AUTH_REQUIRED)
    app.include_router(ai.router, dependencies=_AUTH_REQUIRED)
    # Phase 6 Ticket 6.3 — provider PDF reports.
    app.include_router(reports.router, dependencies=_AUTH_REQUIRED)

    return app


# Module-level app for `uvicorn ursa_oscar.main:app`
app = create_app()
