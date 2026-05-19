"""JWT token issuance and verification — Phase 6.4 Decision 2.

HS256-signed JWTs over the per-instance ``URSA_OSCAR_JWT_SECRET``.

Two token lifetimes:
  - Session (24h): browser cookie, set on /auth/login + /auth/bootstrap
  - API     (90d): operator-generated via /auth/generate-api-token,
                   used by the MCP server, watcher, and any future
                   service that calls the API without a browser

Both lifetimes share the same secret + signing algorithm so the
``require_auth`` middleware accepts either uniformly. A ``token_kind``
claim ("session" or "api") lets the audit log distinguish them.
"""
from __future__ import annotations

import logging
import os
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from jose import JWTError, jwt

logger = logging.getLogger(__name__)


JWT_SECRET_ENV = "URSA_OSCAR_JWT_SECRET"

SESSION_LIFETIME = timedelta(hours=24)
API_TOKEN_LIFETIME = timedelta(days=90)

_ALGORITHM = "HS256"
_SUBJECT = "operator"  # single-user system; the subject is always "operator"

TokenKind = Literal["session", "api"]


class TokenError(Exception):
    """Raised when a token can't be decoded or fails validation."""


def encode_token(
    secret: str,
    *,
    kind: TokenKind = "session",
    now: datetime | None = None,
) -> str:
    """Mint a new JWT for the operator. ``kind="session"`` issues a 24h
    cookie token; ``kind="api"`` issues a 90d service token."""
    if not secret:
        raise TokenError("JWT secret is not configured")
    issued = now or datetime.now(timezone.utc)
    lifetime = SESSION_LIFETIME if kind == "session" else API_TOKEN_LIFETIME
    payload = {
        "sub": _SUBJECT,
        "kind": kind,
        "iat": int(issued.timestamp()),
        "exp": int((issued + lifetime).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(secret: str, token: str) -> dict:
    """Verify + decode a token. Raises TokenError on any issue
    (expired, signature mismatch, malformed). On success returns the
    claims dict with sub/kind/iat/exp populated."""
    if not secret:
        raise TokenError("JWT secret is not configured")
    if not token:
        raise TokenError("No token provided")
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except JWTError as e:
        raise TokenError(f"Token rejected: {e}") from e
    if claims.get("sub") != _SUBJECT:
        raise TokenError(f"Unexpected subject in token: {claims.get('sub')!r}")
    if claims.get("kind") not in ("session", "api"):
        raise TokenError(f"Unknown token kind: {claims.get('kind')!r}")
    return claims


# -----------------------------------------------------------------------
# Per-instance JWT secret resolution.
#
# Mirrors the auto-persistent master.key pattern from Phase 5 secrets:
#   1. URSA_OSCAR_JWT_SECRET env  — operator override, used when set
#   2. <data_dir>/jwt_secret      — canonical persistent location
#   3. First boot — generate, write to <data_dir>/jwt_secret (mode 0600),
#      log the path, no operator action required.
# -----------------------------------------------------------------------


def resolve_jwt_secret(data_dir: Path) -> str:
    """Return the JWT signing secret. Loads from env if set; else
    reads (or auto-generates) the persisted file at
    ``<data_dir>/jwt_secret``.

    The secret is a 32-byte token rendered as a 64-char hex string.
    Re-using HS256, not RS256 — single-instance system, no key
    distribution problem to solve."""
    raw = os.environ.get(JWT_SECRET_ENV, "").strip()
    if raw:
        return raw

    data_dir.mkdir(parents=True, exist_ok=True)
    secret_path = data_dir / "jwt_secret"

    if secret_path.exists():
        try:
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError as e:
            logger.exception(
                "jwt_secret exists at %s but is unreadable: %s — regenerating",
                secret_path, e,
            )

    # First boot: generate a fresh secret.
    secret = secrets.token_hex(32)
    secret_path.write_text(secret, encoding="utf-8")
    try:
        os.chmod(secret_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows / non-POSIX FS — chmod is a no-op. The homelab data
        # dir is already operator-only on those platforms.
        pass
    logger.info(
        "Generated initial JWT signing secret at %s (mode 0600). "
        "Persisted on the data volume; future restarts reuse this "
        "secret transparently. No operator action required. To manage "
        "the secret yourself (rotation, backup), set %s in env and "
        "delete the file.",
        secret_path, JWT_SECRET_ENV,
    )
    return secret
