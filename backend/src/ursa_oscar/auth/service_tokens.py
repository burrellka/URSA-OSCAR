"""Auto-managed service tokens — Phase 6.4.1.

Background: Phase 6.4 introduced the requirement that MCP + watcher
both authenticate to the API with operator-generated JWTs. The
original UX was "operator generates two tokens via the web UI, pastes
each into compose env, recreates the containers." Operator pushback
(rightly): that's fragile, the operator shouldn't have to babysit
service credentials.

Phase 6.4.1 design (mirrors the master.key auto-resolve pattern):

  - At API startup, after resolving the JWT signing secret, the API
    checks ``/data/service_tokens/mcp.jwt`` and ``/data/service_tokens/watcher.jwt``.
  - If a file is missing, expired, or expires within 7 days, the API
    mints a fresh 90-day operator JWT and writes it atomically with
    mode 0600.
  - The MCP container reads its token from the same path (mounted
    read-only into /data). Watcher does the same.
  - URSA_OSCAR_MCP_API_TOKEN / URSA_OSCAR_WATCHER_TOKEN env vars are
    preserved as explicit overrides — operators who want manual
    control over service credentials still have that path.

Trust boundary: same as master.key + jwt_secret + secrets.enc — anyone
with host file access to /data can act as the operator. That's already
the threat model for the rest of the stored state.
"""
from __future__ import annotations

import logging
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .tokens import (
    API_TOKEN_LIFETIME,
    TokenError,
    decode_token,
    encode_token,
)

logger = logging.getLogger(__name__)


# Re-mint when the existing token is within this much of its
# expiration — gives operators a comfortable buffer without making
# rotation overly aggressive.
RENEW_BEFORE = timedelta(days=7)

ServiceName = Literal["mcp", "watcher"]

_SERVICE_NAMES: tuple[ServiceName, ...] = ("mcp", "watcher")


class ServiceTokenError(Exception):
    """Raised on unrecoverable issues — missing JWT secret, can't write
    to /data, etc. Won't be raised for "file missing" or "expired" —
    those are handled silently by re-minting."""


def _service_token_path(data_dir: Path, service: ServiceName) -> Path:
    return data_dir / "service_tokens" / f"{service}.jwt"


def _is_token_usable(secret: str, token: str, *, now: datetime) -> bool:
    """Return True if the token decodes cleanly and isn't within
    RENEW_BEFORE of expiring."""
    try:
        claims = decode_token(secret, token)
    except TokenError:
        return False
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
    return expires_at - now > RENEW_BEFORE


def _read_token(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        existing = path.read_text(encoding="utf-8").strip()
        return existing or None
    except OSError as e:
        logger.warning("service token %s exists but unreadable: %s", path, e)
        return None


def _write_token(path: Path, token: str) -> None:
    """Atomic write: temp file + os.replace. Mode 0600 where supported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token, encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows / non-POSIX FS — chmod is a no-op. Container's data
        # dir is already operator-only on those hosts.
        pass
    os.replace(tmp, path)


def ensure_service_token(
    data_dir: Path,
    secret: str,
    service: ServiceName,
    *,
    now: datetime | None = None,
) -> str:
    """Resolve the service token for ``service``. Reads the existing
    file if usable; otherwise mints a fresh 90-day JWT and writes it
    atomically.

    Returns the token string. Raises ServiceTokenError on issues that
    prevent minting (missing secret, /data unwritable).
    """
    if not secret:
        raise ServiceTokenError(
            "JWT signing secret is not configured — cannot mint service tokens"
        )

    now = now or datetime.now(timezone.utc)
    path = _service_token_path(data_dir, service)
    existing = _read_token(path)

    if existing and _is_token_usable(secret, existing, now=now):
        logger.debug("service token %s: reused existing", service)
        return existing

    # Either missing, corrupted, or within the renewal window — mint a
    # fresh one.
    reason = "missing" if existing is None else "expired-or-expiring-soon"
    fresh = encode_token(secret, kind="api", now=now)
    try:
        _write_token(path, fresh)
    except OSError as e:
        raise ServiceTokenError(
            f"failed to write {path}: {e}"
        ) from e
    expires_at = (now + API_TOKEN_LIFETIME).replace(microsecond=0).isoformat()
    logger.info(
        "service token %s: minted fresh (reason=%s); valid until %s; path=%s",
        service, reason, expires_at, path,
    )
    return fresh


def ensure_all_service_tokens(
    data_dir: Path,
    secret: str,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Resolve every known service token. Called once at API startup
    from the FastAPI lifespan. Returns a {service_name: token} map so
    the caller can park it on app.state if it wants — though the
    canonical store is the file, not in-memory."""
    result: dict[str, str] = {}
    for service in _SERVICE_NAMES:
        result[service] = ensure_service_token(data_dir, secret, service, now=now)
    return result


def read_service_token(data_dir: Path, service: ServiceName) -> str | None:
    """Pure read — used by MCP + watcher containers to pick up their
    token at startup. Returns None when the file isn't present. Does
    NOT validate the JWT signature (the MCP/watcher containers don't
    have the signing secret); validation happens server-side when the
    API receives the bearer header."""
    return _read_token(_service_token_path(data_dir, service))
