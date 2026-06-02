"""Package entry point — invoked via `python -m ursa_oscar_mcp`.

Importing `mcp` from `.server` ensures the server module loads exactly once
(under the canonical `ursa_oscar_mcp.server` name), so tool modules that do
`from ..server import mcp` register against the same FastMCP instance the
running uvicorn loop is serving.

If we ran `python -m ursa_oscar_mcp.server` instead, Python would create a
second instance of the server module (one as __main__, one as the package
module), and tools would register against the package-module copy while
uvicorn served the __main__ copy — claude.ai's tools/list would return [].
"""
from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version as _pkg_version

import uvicorn
from starlette.responses import JSONResponse
from starlette.routing import Route

from .server import mcp


async def _version_endpoint(request):  # noqa: ANN001 (Starlette handler)
    """1.1.3 — public version endpoint. Returns the MCP container's own
    packaged version (from importlib.metadata). The API container's
    Settings page queries this to populate the MCP image-version chip,
    eliminating the operator's need to keep image tags and display env
    vars in sync.

    Unauthenticated by design: the version string is not sensitive and
    requiring auth on this endpoint would defeat its purpose (the API
    container needs to call it during a normal /system/config request).
    """
    try:
        v = _pkg_version("ursa-oscar-mcp")
    except PackageNotFoundError:
        v = "dev"
    return JSONResponse({"version": v})


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = mcp.http_app(transport="sse")

    # 1.1.3 — append the /version route to the FastMCP-returned Starlette
    # app. Placed AFTER FastMCP builds its routes so FastMCP's lifespan
    # events, middleware, and OAuth routes are preserved intact. Starlette
    # walks routes in order; /version is a distinct path so there's no
    # collision risk with FastMCP's surface.
    app.routes.append(
        Route("/version", endpoint=_version_endpoint, methods=["GET"]),
    )

    print(
        "ursa-oscar-mcp: SSE listening on :8000 "
        "(oauth=ready, dcr=ENABLED, pre_registered_client=required, "
        "static_bearer=enabled, version_endpoint=/version, "
        "proxy_headers=trusted)"
    )
    # 1.1.6 — honor X-Forwarded-Proto from the reverse proxy (Cloudflare
    # Tunnel, nginx, Traefik, etc.) so Starlette generates redirect URLs
    # (e.g. trailing-slash 307s on /messages → /messages/) using the
    # client's original scheme (HTTPS) instead of the proxy-to-container
    # hop's scheme (HTTP). Without this, a POST to /messages (no slash)
    # got a 307 → http://..., the client followed HTTPS→HTTP→HTTPS, and
    # the redirect cascade silently dropped the POST body and downgraded
    # the method, surfacing as a confusing 405 at the MCP client.
    # forwarded_allow_ips="*" because the container only ever receives
    # traffic from a trusted reverse proxy on the docker network — the
    # operator's network boundary is the host's firewall, not uvicorn's.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
