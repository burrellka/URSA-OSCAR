"""GET /api/v1/system/config + POST /api/v1/system/verify-mcp.

Phase 2 polish work-order Item 5 — the Settings page's data source.

Architect decision in the work order: the Settings page is READ-ONLY. This
module surfaces masked configuration + a button-driven health check; it does
NOT expose any secret-mutation surface. Full secrets management is deferred
to Phase 4+ where it needs its own web-UI auth layer + secrets-persistence
backing store + container-orchestration story.

Masking rules (mirror the work-order's table):
- Bearer token / OAuth client ID: first 3 + last 2 chars (e.g. ``Anv…UI``)
- OAuth client secret: never any portion — display ``set`` / ``not set``
- Other config (URLs, paths, image versions): plain text

Image versions are read from the API container's own env (baked at build
time) plus the MCP/web/watcher image versions which the API receives via
docker-compose env. Defaults to ``"dev"`` for unbuilt local images.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import get_settings

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _mask_partial(value: str | None, head: int = 3, tail: int = 2) -> str | None:
    """``"abcdefghij"`` -> ``"abc…ij"``. None and empty pass through unchanged."""
    if not value:
        return None
    if len(value) <= head + tail:
        # Too short to meaningfully mask — return all bullets.
        return "•" * len(value)
    return f"{value[:head]}…{value[-tail:]}"


def _mask_full(value: str | None) -> dict:
    """Returns ``{set: bool}`` — never echoes the secret. For client secret etc."""
    return {"set": bool(value)}


def _db_size_bytes(path: str) -> int | None:
    try:
        return os.path.getsize(path) if os.path.exists(path) else None
    except OSError:
        return None


@router.get("/config")
def get_system_config(request: Request) -> dict:
    """Masked operational config for the Settings page.

    Every secret value is masked server-side BEFORE returning. Bearer + OAuth
    client ID show first-3 + last-2 chars; OAuth client secret shows only
    ``{"set": true/false}``. Plain values (URLs, paths, sizes, image
    versions) pass through.

    All four image versions are surfaced. The API knows its own version
    directly (env var baked at build time) plus what was passed for the
    MCP/web/watcher containers via docker-compose. When unset, the
    response reports ``"dev"`` / ``null`` — never raises.
    """
    settings = get_settings()
    db: object = request.app.state.db
    db_path = str(getattr(db, "path", settings.db_path))

    return {
        "mcp": {
            "base_url": settings.mcp_base_url,
            "bearer_token_masked": _mask_partial(settings.mcp_bearer_token),
            "oauth_client_id_masked": _mask_partial(settings.mcp_oauth_client_id),
            "oauth_client_secret": _mask_full(settings.mcp_oauth_client_secret),
            "internal_url": settings.mcp_internal_url,
        },
        "api": {
            "internal_url": f"http://ursa-oscar-api:{settings.api_port}",
            "db_path": db_path,
            "db_size_bytes": _db_size_bytes(db_path),
            "dev_bypass_enabled": settings.dev_bypass_enabled,
        },
        "images": {
            "api": settings.api_image_version,
            "mcp": settings.mcp_image_version,
            "web": settings.web_image_version,
            "watcher": settings.watcher_image_version,
        },
    }


@router.post("/verify-mcp")
async def verify_mcp(request: Request) -> dict:
    """Run the four checks from infra/verify-mcp-live.sh against the MCP
    container and return structured results for the Settings-page UI.

    Same semantics as the shell script:
      1. ``/.well-known/oauth-authorization-server`` reachable + DCR off
      2. POST /register returns non-2xx
      3. Bogus bearer returns 401
      4. Real bearer returns non-401 (auth-gate cleared)

    All four checks always run — we don't fail-fast — so the UI can show
    a per-check pass/fail. Aggregate ``all_passed`` is the AND of the four.

    If the MCP container isn't reachable at all, every check fails with
    ``status: "error"`` and the response still returns 200 (UI handles
    rendering). Surfaces the connection error in each check's ``detail``.
    """
    settings = get_settings()
    base = settings.mcp_internal_url.rstrip("/")
    bearer = settings.mcp_bearer_token

    if not bearer:
        raise HTTPException(
            status_code=503,
            detail="URSA_OSCAR_MCP_BEARER_TOKEN not configured on the API container",
        )

    checks: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1. Discovery
        try:
            r = await client.get(f"{base}/.well-known/oauth-authorization-server")
            disco_ok = (
                r.status_code == 200
                and "authorization_endpoint" in r.text
                and "registration_endpoint" not in r.text
            )
            checks.append({
                "name": "OAuth discovery reachable + DCR off",
                "status": "pass" if disco_ok else "fail",
                "detail": (
                    f"HTTP {r.status_code}; DCR field absent"
                    if disco_ok
                    else f"HTTP {r.status_code}; body: {r.text[:200]}"
                ),
            })
        except Exception as e:
            checks.append({
                "name": "OAuth discovery reachable + DCR off",
                "status": "error",
                "detail": f"{type(e).__name__}: {e}",
            })

        # 2. POST /register rejects (DCR off)
        try:
            r = await client.post(f"{base}/register", json={})
            reg_ok = r.status_code not in (200, 201)
            checks.append({
                "name": "POST /register rejected (DCR off)",
                "status": "pass" if reg_ok else "fail",
                "detail": f"HTTP {r.status_code}",
            })
        except Exception as e:
            checks.append({
                "name": "POST /register rejected (DCR off)",
                "status": "error",
                "detail": f"{type(e).__name__}: {e}",
            })

        # 3. Bogus bearer -> 401
        try:
            r = await client.post(
                f"{base}/messages/",
                headers={"Authorization": "Bearer absolutely-not-a-real-token"},
            )
            bogus_ok = r.status_code == 401
            checks.append({
                "name": "Bogus bearer rejected (HTTP 401)",
                "status": "pass" if bogus_ok else "fail",
                "detail": f"HTTP {r.status_code}",
            })
        except Exception as e:
            checks.append({
                "name": "Bogus bearer rejected (HTTP 401)",
                "status": "error",
                "detail": f"{type(e).__name__}: {e}",
            })

        # 4. Real bearer != 401 (auth gate passed; downstream may 4xx without an SSE session)
        try:
            r = await client.post(
                f"{base}/messages/",
                headers={"Authorization": f"Bearer {bearer}"},
            )
            real_ok = r.status_code != 401
            checks.append({
                "name": "Static bearer passes auth gate",
                "status": "pass" if real_ok else "fail",
                "detail": f"HTTP {r.status_code} (any non-401 is auth-passed)",
            })
        except Exception as e:
            checks.append({
                "name": "Static bearer passes auth gate",
                "status": "error",
                "detail": f"{type(e).__name__}: {e}",
            })

    return {
        "checks": checks,
        "all_passed": all(c["status"] == "pass" for c in checks),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
