"""/api/v1/auth/* routes — Phase 6.4.

Endpoints:
  GET  /auth/bootstrap-status     — open: drives first-run UI
  POST /auth/bootstrap            — open: one-time setup
  POST /auth/login                — open: password → session JWT
  POST /auth/logout               — protected: clears cookie
  GET  /auth/session              — protected: returns session info
  POST /auth/change-password      — protected: requires current password
  POST /auth/generate-api-token   — protected: returns 90d JWT for services

Cookie behavior driven by ``URSA_OSCAR_DEV_MODE`` env:
  - Production (default): httponly=True, secure=True, samesite=strict
  - Dev: httponly=True, secure=False, samesite=lax (HTTPS not required)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .hashing import hash_password, verify_password
from .middleware import COOKIE_NAME, require_auth
from .store import (
    AuthStoreAlreadyBootstrapped,
    AuthStoreNotBootstrapped,
    USER_NAME,
)
from .tokens import API_TOKEN_LIFETIME, SESSION_LIFETIME, encode_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# Minimum password length per Decision 4. No complexity rules beyond
# length — the work order is explicit: "forced complexity isn't
# actually stronger; length matters more."
MIN_PASSWORD_LENGTH = 12


# ---------------------------------------------------------------------------
# Pydantic shapes
# ---------------------------------------------------------------------------


class BootstrapRequest(BaseModel):
    password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        description=(
            "Password for the URSA-OSCAR operator user. Minimum "
            f"{MIN_PASSWORD_LENGTH} characters. There is no password "
            "recovery — choose something durable."
        ),
    )


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH)


class TokenResponse(BaseModel):
    ok: bool = True
    token: str
    token_kind: str
    expires_at_iso: str


class BootstrapStatusResponse(BaseModel):
    bootstrapped: bool


class SessionResponse(BaseModel):
    user: str
    token_kind: str
    issued_at_iso: str
    expires_at_iso: str
    expires_in_seconds: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort source IP for rate-limit bucketing. Prefers the
    standard X-Forwarded-For from reverse proxies; falls back to the
    direct peer. Localhost auth scenarios all collapse to one bucket
    which is fine."""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    dev_mode = os.environ.get("URSA_OSCAR_DEV_MODE", "false").lower() == "true"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=not dev_mode,
        samesite="lax" if dev_mode else "strict",
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _state(request: Request):
    """Convenience: app.state with the auth bits typed as plain objects.
    We're not using a pydantic state model — these are just attribute
    accesses for clarity."""
    return request.app.state


# ---------------------------------------------------------------------------
# Open endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.get("/bootstrap-status", response_model=BootstrapStatusResponse)
def bootstrap_status(request: Request) -> BootstrapStatusResponse:
    """Drives the first-run UI. Returns ``bootstrapped: false`` when
    /data/auth.json doesn't exist yet — the web UI then renders the
    /setup page instead of /login."""
    store = _state(request).auth_store
    return BootstrapStatusResponse(bootstrapped=store.is_bootstrapped())


@router.post("/bootstrap")
def bootstrap(
    body: BootstrapRequest, request: Request, response: Response,
) -> TokenResponse:
    """One-time setup. Creates /data/auth.json with the operator's
    Argon2id-hashed password and returns the first session JWT.

    Refuses if the store is already bootstrapped — recovery is "delete
    the file and try again," not "call this endpoint again."
    """
    store = _state(request).auth_store
    if store.is_bootstrapped():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "URSA-OSCAR is already bootstrapped. To reset the "
                "password, delete /data/auth.json and restart the API "
                "container."
            ),
        )

    try:
        store.write_initial(hash_password(body.password))
    except AuthStoreAlreadyBootstrapped:
        # Race against another bootstrap call — same UX as the
        # is_bootstrapped check above.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already bootstrapped.")

    secret = _state(request).jwt_secret
    now = datetime.now(timezone.utc)
    token = encode_token(secret, kind="session", now=now)
    _set_session_cookie(response, token)
    logger.info("auth: bootstrap completed; operator session issued")
    return TokenResponse(
        token=token,
        token_kind="session",
        expires_at_iso=(now + SESSION_LIFETIME).replace(microsecond=0).isoformat(),
    )


@router.post("/login")
def login(
    body: LoginRequest, request: Request, response: Response,
) -> TokenResponse:
    """Verify the operator's password and return a fresh 24h session
    JWT (also set as an httpOnly cookie). Brute-force protection per
    Decision 9: 5 failures per IP per 15 min → 429."""
    store = _state(request).auth_store
    limiter = _state(request).auth_limiter
    ip = _client_ip(request)

    allowed, retry_after = limiter.check(ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed attempts. Try again in "
                f"{retry_after // 60}m {retry_after % 60}s."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    stored_hash = store.read_password_hash()
    if not stored_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "URSA-OSCAR is not bootstrapped yet. Run the /setup "
                "flow first."
            ),
        )

    if not verify_password(body.password, stored_hash):
        limiter.record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    limiter.reset(ip)
    secret = _state(request).jwt_secret
    now = datetime.now(timezone.utc)
    token = encode_token(secret, kind="session", now=now)
    _set_session_cookie(response, token)
    logger.info("auth: login successful for operator (ip=%s)", ip)
    return TokenResponse(
        token=token,
        token_kind="session",
        expires_at_iso=(now + SESSION_LIFETIME).replace(microsecond=0).isoformat(),
    )


# ---------------------------------------------------------------------------
# Protected endpoints (Depends(require_auth))
# ---------------------------------------------------------------------------


@router.post("/logout")
def logout(
    response: Response, _claims: dict = Depends(require_auth),
) -> dict:
    """Clear the session cookie. Stateless tokens stay valid until
    their natural expiration — but the browser side drops the cookie,
    so subsequent requests from the same browser get 401 and route
    back to /login."""
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/session", response_model=SessionResponse)
def session_info(
    request: Request, claims: dict = Depends(require_auth),
) -> SessionResponse:
    """Return the current JWT's claims in a UI-friendly shape.
    Surfaced on Settings → Account so the operator can see when
    their session expires."""
    iat = int(claims.get("iat", 0))
    exp = int(claims.get("exp", 0))
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return SessionResponse(
        user=claims.get("sub", USER_NAME),
        token_kind=claims.get("kind", "session"),
        issued_at_iso=datetime.fromtimestamp(iat, tz=timezone.utc).isoformat(),
        expires_at_iso=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
        expires_in_seconds=max(0, exp - now_ts),
    )


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    _claims: dict = Depends(require_auth),
) -> TokenResponse:
    """Verify the current password, then rewrite the hash with the new
    password. Issues a fresh session JWT (refreshes the cookie) so the
    operator stays logged in."""
    store = _state(request).auth_store
    stored_hash = store.read_password_hash()
    if not stored_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Not bootstrapped.",
        )
    if not verify_password(body.current_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )
    try:
        store.update_password_hash(hash_password(body.new_password))
    except AuthStoreNotBootstrapped:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not bootstrapped.")

    secret = _state(request).jwt_secret
    now = datetime.now(timezone.utc)
    token = encode_token(secret, kind="session", now=now)
    _set_session_cookie(response, token)
    logger.info("auth: password changed for operator")
    return TokenResponse(
        token=token,
        token_kind="session",
        expires_at_iso=(now + SESSION_LIFETIME).replace(microsecond=0).isoformat(),
    )


@router.post("/generate-api-token", response_model=TokenResponse)
def generate_api_token(
    request: Request, _claims: dict = Depends(require_auth),
) -> TokenResponse:
    """Issue a 90-day API token suitable for MCP server config + the
    watcher's URSA_OSCAR_WATCHER_TOKEN + any other service that calls
    the API without a browser. The token is shown to the operator once
    in the UI and not stored server-side — its validity is established
    by signature against URSA_OSCAR_JWT_SECRET."""
    secret = _state(request).jwt_secret
    now = datetime.now(timezone.utc)
    token = encode_token(secret, kind="api", now=now)
    logger.info("auth: new API token issued (90d) for operator")
    return TokenResponse(
        token=token,
        token_kind="api",
        expires_at_iso=(now + API_TOKEN_LIFETIME).replace(microsecond=0).isoformat(),
    )
