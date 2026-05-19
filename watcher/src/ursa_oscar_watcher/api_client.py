"""Thin HTTP client for the URSA-OSCAR API.

Phase 4 Ticket 3 — the watcher only needs three endpoints:
  - POST /api/v1/imports         to enqueue a job
  - GET  /api/v1/imports/jobs/{id} to poll for completion
  - POST <webhook url>           to notify downstream automation

Phase 6.4 — the API now requires auth on every endpoint. The watcher
authenticates with an operator-generated 90d JWT (URSA_OSCAR_WATCHER_TOKEN
env var). When the token is set, every call to enqueue_import / get_job
carries an ``Authorization: Bearer <token>`` header. Webhooks DO NOT
get the bearer — those are operator-configured external URLs (Home
Assistant, n8n, etc.) and should not see our internal credential.

All errors are caught at the caller (the watcher loop) so a transient
API hiccup doesn't tank the daemon.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        api_token: str | None = None,
    ) -> None:
        # Normalize: strip trailing slashes so URL concatenation is
        # predictable regardless of how the env var is set.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = (api_token or "").strip() or None
        if self.api_token:
            logger.info("api_client: operator JWT configured; auth header active")
        else:
            logger.warning(
                "api_client: URSA_OSCAR_WATCHER_TOKEN is unset — calls to the "
                "API will be anonymous and will 401 against Phase 6.4+ "
                "backends. Generate a token via the URSA-OSCAR web UI: "
                "Settings → Account → Generate API Token."
            )

    def _auth_headers(self) -> dict[str, str]:
        """Build the bearer header for API calls. Empty when no token
        is configured."""
        if self.api_token:
            return {"Authorization": f"Bearer {self.api_token}"}
        return {}

    def enqueue_import(self, source_path: str, *, force: bool = False) -> dict[str, Any]:
        """POST /api/v1/imports with a path-based body. Returns the
        enqueued ImportJob (dict). Raises on HTTP error so the caller
        can decide whether to retry."""
        url = f"{self.base_url}/api/v1/imports"
        params = {"force": "true"} if force else {}
        r = httpx.post(
            url,
            json={"source_path": source_path},
            params=params,
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    def get_job(self, job_id: int) -> dict[str, Any]:
        """GET /api/v1/imports/jobs/{id}. Raises on 4xx/5xx."""
        url = f"{self.base_url}/api/v1/imports/jobs/{job_id}"
        r = httpx.get(url, timeout=self.timeout, headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    def fire_webhook(self, webhook_url: str, payload: dict[str, Any]) -> None:
        """POST a JSON payload to the operator-configured webhook URL.
        Best-effort — logs but doesn't raise so a bad webhook URL
        doesn't break the watcher's main loop.

        Webhooks are EXTERNAL endpoints (Home Assistant, n8n, etc.) so
        we deliberately do NOT forward the operator's API token —
        keeping the watcher's bearer scoped to the URSA-OSCAR API."""
        try:
            r = httpx.post(webhook_url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            logger.info("webhook delivered: status=%d", r.status_code)
        except Exception:
            logger.exception("webhook POST to %s failed", webhook_url)
