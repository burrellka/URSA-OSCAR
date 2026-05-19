"""API client for the URSA-OSCAR backend.

Per the DuckDB concurrency model (a single writer locks the file even
against read-only opens from other processes), the MCP container no longer
opens DuckDB directly. Instead all reads + writes go through the API
container over the `kairos-net` Docker network. The API container is the
single owner of the DuckDB file.

Phase 6.4 — the backend now requires auth on every endpoint. The MCP
container attaches an operator JWT as a Bearer header on every backend
call.

Phase 6.4.1 — token resolution is auto-managed (no copy-paste UX):

  1. ``URSA_OSCAR_MCP_API_TOKEN`` env var — explicit override for
     operators who want manual control over service credentials.
  2. ``<DB_DIR>/service_tokens/mcp.jwt`` — auto-minted by the API
     container at startup, written to the shared /data volume that
     this container reads via :ro mount. Zero operator action.

If neither is available, calls go through anonymous and the backend
will 401 — surfaces as tool errors in the operator's MCP client.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("ursa-oscar-mcp.client")


API_BASE_URL = os.environ.get("URSA_OSCAR_API_URL", "http://ursa-oscar-api:8000")
API_TOKEN_ENV = "URSA_OSCAR_MCP_API_TOKEN"


def _resolve_api_token() -> str | None:
    """Phase 6.4.1 — try env var first, fall back to the shared service-
    token file. Returns None when neither is available (anonymous mode)."""
    raw = os.environ.get(API_TOKEN_ENV, "").strip()
    if raw:
        return raw

    # Auto-managed path. The API container writes this file on startup;
    # we mount /data read-only and pick it up at request time so a
    # token rotation (API re-mints, e.g. when nearing expiry) is
    # reflected on the very next request — no MCP restart needed.
    db_path = os.environ.get("URSA_OSCAR_DB_PATH", "/data/ursa-oscar.duckdb")
    token_path = Path(db_path).parent / "service_tokens" / "mcp.jwt"
    if token_path.exists():
        try:
            existing = token_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError as e:
            logger.warning(
                "service token %s exists but unreadable: %s", token_path, e,
            )
    return None


def _auth_headers() -> dict[str, str]:
    """Build the Authorization header for backend calls. Empty dict
    when no token resolves (the backend will then 401)."""
    token = _resolve_api_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def get_client(timeout: float = 30.0) -> httpx.Client:
    """Return a fresh sync httpx client targeting the API, with the
    operator API token attached when resolvable."""
    return httpx.Client(
        base_url=API_BASE_URL,
        timeout=timeout,
        headers=_auth_headers(),
    )


def api_get(path: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    """GET the API and return the parsed JSON body.

    Raises httpx.HTTPStatusError on non-2xx; tools wrap that in the
    {"ok": False, "code": "..."} envelope.
    """
    with get_client(timeout=timeout) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def api_post(path: str, json_body: dict[str, Any] | None = None, timeout: float = 300.0) -> Any:
    """POST to the API. Used by trigger_import."""
    with get_client(timeout=timeout) as c:
        r = c.post(path, json=json_body)
        r.raise_for_status()
        return r.json()
