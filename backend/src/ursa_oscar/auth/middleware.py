"""FastAPI dependency that enforces auth on protected endpoints — Phase 6.4.

The dependency reads a JWT from either:
  - ``ursa_oscar_session`` cookie (browser sessions)
  - ``Authorization: Bearer <token>`` header (MCP server, watcher,
    curl/script clients, etc.)

Cookie takes precedence when both are present. The cookie is httpOnly
+ Secure (relaxed for dev mode) per Decision 5.

Use as::

    from fastapi import APIRouter, Depends
    from ..auth import require_auth

    router = APIRouter()

    @router.get("/protected")
    def protected_endpoint(_: dict = Depends(require_auth)):
        ...

The dependency returns the decoded JWT claims dict on success. On
failure, it raises ``HTTPException(401)`` with a ``WWW-Authenticate``
header so non-browser clients see the expected challenge.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from .tokens import TokenError, decode_token


COOKIE_NAME = "ursa_oscar_session"


def require_auth(request: Request) -> dict:
    """FastAPI dependency. Reads the JWT from cookie or Bearer header,
    validates against the per-instance secret, returns the claims on
    success. Raises 401 on any failure."""
    secret = getattr(request.app.state, "jwt_secret", None)
    if not secret:
        # Misconfiguration: app didn't wire the secret into app.state.
        # Surface loudly rather than silently letting requests through.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Auth not initialized: app.state.jwt_secret is missing. "
                "This is a server configuration error."
            ),
        )

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode_token(secret, token)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )

    return claims
