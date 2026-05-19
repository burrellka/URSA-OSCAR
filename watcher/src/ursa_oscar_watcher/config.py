"""Env-driven configuration for the watcher daemon."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatcherConfig:
    """Phase 4 Ticket 3 — knobs the operator can tune via Dockge env vars.

    Defaults match the docker-compose layout: /cpap-import is the
    bind-mounted SD-card source on TrueNAS, and ursa-oscar-api is
    reachable by service name over the kairos-net Docker network.
    """

    api_url: str
    watch_path: str
    poll_interval_seconds: float
    quiescence_seconds: float
    webhook_url: str | None
    force_reimport: bool
    # Cap how long we'll wait for a single import job to reach a terminal
    # state before giving up on webhook delivery. Imports of full SD
    # cards take a few seconds; this is defensive against a stuck job
    # holding the watcher in a polling loop forever.
    job_wait_timeout_seconds: float
    # Phase 6.4 — operator-generated 90d JWT used as the watcher's bearer
    # when calling the API. Generated via the URSA-OSCAR web UI:
    # Settings → Account → Generate API Token. None means anonymous
    # calls (will 401 against Phase 6.4+ backends).
    api_token: str | None

    @classmethod
    def from_env(cls) -> "WatcherConfig":
        return cls(
            api_url=os.environ.get("URSA_OSCAR_API_URL", "http://ursa-oscar-api:8000"),
            watch_path=os.environ.get("URSA_OSCAR_WATCH_PATH", "/cpap-import"),
            poll_interval_seconds=float(os.environ.get("URSA_OSCAR_POLL_INTERVAL", "30")),
            quiescence_seconds=float(os.environ.get("URSA_OSCAR_QUIESCENCE_SECONDS", "30")),
            webhook_url=(os.environ.get("URSA_OSCAR_IMPORT_WEBHOOK_URL") or None),
            force_reimport=_env_bool("URSA_OSCAR_FORCE_REIMPORT", default=False),
            job_wait_timeout_seconds=float(os.environ.get("URSA_OSCAR_JOB_WAIT_TIMEOUT", "600")),
            api_token=_resolve_api_token(),
        )


# Phase 6.4.1 — auto-managed service tokens. The watcher follows the
# exact same resolution chain as the MCP container:
#   1. ``URSA_OSCAR_WATCHER_TOKEN`` env (explicit override)
#   2. ``<DB_DIR>/service_tokens/watcher.jwt`` (auto-minted by the API
#      container; shared via the /data volume the watcher already
#      mounts)
# Neither configured → anonymous calls → 401 → log + retry on next tick.

_WATCHER_TOKEN_ENV = "URSA_OSCAR_WATCHER_TOKEN"


def _resolve_api_token() -> str | None:
    raw = os.environ.get(_WATCHER_TOKEN_ENV, "").strip()
    if raw:
        return raw
    db_path = os.environ.get("URSA_OSCAR_DB_PATH", "/data/ursa-oscar.duckdb")
    token_path = Path(db_path).parent / "service_tokens" / "watcher.jwt"
    if token_path.exists():
        try:
            existing = token_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError as e:
            logger.warning(
                "watcher service token %s exists but unreadable: %s",
                token_path, e,
            )
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse a permissive boolean env var. Accepts 1/true/yes/on
    (case-insensitive) as truthy; everything else falls back to default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
