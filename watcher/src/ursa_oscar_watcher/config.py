"""Env-driven configuration for the watcher daemon."""
from __future__ import annotations

import os
from dataclasses import dataclass


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
        )


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse a permissive boolean env var. Accepts 1/true/yes/on
    (case-insensitive) as truthy; everything else falls back to default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
