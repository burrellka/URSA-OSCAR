"""Minimal JWT verification — Phase 6.4.

Mirrors the decode path of ``backend/src/ursa_oscar/auth/tokens.py`` so
the MCP server can verify operator-issued JWTs without depending on
the backend package at runtime. We only need decode (the API container
is the sole issuer); no encode logic lives here.

Shared signing secret resolution:
  1. ``URSA_OSCAR_JWT_SECRET`` env var (operator override; explicit)
  2. ``<DB_DIR>/jwt_secret`` on the shared data volume (auto-shared
     with the API container when both mount /data)

If neither is configured, ``resolve_jwt_secret`` returns None and the
auth provider skips JWT verification — OAuth + static bearer still
work, preserving 0.10.x compatibility for setups that haven't bumped
the API.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from jose import JWTError, jwt

logger = logging.getLogger("ursa-oscar-mcp.jwt")


JWT_SECRET_ENV = "URSA_OSCAR_JWT_SECRET"
_ALGORITHM = "HS256"
_SUBJECT = "operator"  # single-user system


class TokenError(Exception):
    """Raised when a JWT can't be decoded or fails claim validation."""


def decode_token(secret: str, token: str) -> dict:
    """Verify + decode an operator JWT. Raises TokenError on any
    failure (expired, bad signature, wrong subject, unknown kind).
    Returns the claims dict on success."""
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


def resolve_jwt_secret() -> str | None:
    """Locate the shared signing secret. Returns None when neither the
    env var nor the persisted file is available — caller should treat
    that as "JWT verification disabled" and fall through to OAuth +
    static bearer."""
    raw = os.environ.get(JWT_SECRET_ENV, "").strip()
    if raw:
        logger.info("JWT signing secret loaded from %s", JWT_SECRET_ENV)
        return raw

    # Fall back to the data-volume file the API container auto-creates
    # on first boot. URSA_OSCAR_DB_PATH is already in env by virtue of
    # the Dockerfile defaulting it to /data/ursa-oscar.duckdb.
    db_path = os.environ.get("URSA_OSCAR_DB_PATH", "/data/ursa-oscar.duckdb")
    data_dir = Path(db_path).parent
    secret_path = data_dir / "jwt_secret"
    if secret_path.exists():
        try:
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                logger.info(
                    "JWT signing secret loaded from %s (shared with API)",
                    secret_path,
                )
                return existing
        except OSError as e:
            logger.warning(
                "jwt_secret exists at %s but is unreadable: %s", secret_path, e,
            )

    logger.warning(
        "No JWT signing secret configured (no %s env, no %s on disk). "
        "JWT bearer verification is disabled; OAuth + static bearer "
        "remain functional.",
        JWT_SECRET_ENV, secret_path,
    )
    return None
