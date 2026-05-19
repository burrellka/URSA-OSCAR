"""Auth-store reader/writer — Phase 6.4 Decision 3.

Persists the single-user credential at ``<data_dir>/auth.json``:

    {
      "version": 1,
      "user": "operator",
      "password_hash": "$argon2id$v=19$m=...$<salt>$<hash>",
      "created_at": "2026-05-18T13:00:00Z",
      "last_changed_at": "2026-05-18T13:00:00Z"
    }

File written with mode 0600 on POSIX (read/write owner only). On
Windows we can't usefully restrict — homelab data dir is already
operator-only there.

Recovery story (Decision 4): no password reset via email. If the
operator forgets the password, they delete ``auth.json`` and the API
goes back into the bootstrap state on next startup.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


_SCHEMA_VERSION = 1
USER_NAME = "operator"


class AuthStore:
    """Read/write surface for /data/auth.json. Stateless; one
    instance per app lifecycle but holds nothing other than the
    path."""

    def __init__(self, store_path: Path) -> None:
        self._path = store_path

    @property
    def path(self) -> Path:
        return self._path

    def is_bootstrapped(self) -> bool:
        """True iff the auth file exists and parses. Used by the
        /auth/bootstrap-status endpoint to drive the first-run UI."""
        return self._read_safe() is not None

    def read_password_hash(self) -> str | None:
        """Return the stored hash, or None if no auth file yet."""
        data = self._read_safe()
        return data.get("password_hash") if data else None

    def write_initial(self, password_hash: str) -> None:
        """Create auth.json on first bootstrap. Refuses if a file
        already exists — bootstrap is one-time."""
        if self._path.exists():
            raise AuthStoreAlreadyBootstrapped(
                f"{self._path} already exists. Bootstrap is one-time. "
                f"Recover by deleting the file and restarting the API."
            )
        now = _now_iso()
        self._write({
            "version": _SCHEMA_VERSION,
            "user": USER_NAME,
            "password_hash": password_hash,
            "created_at": now,
            "last_changed_at": now,
        })

    def update_password_hash(self, password_hash: str) -> None:
        """Replace the stored hash. Requires that bootstrap has
        already happened; callers verify the current password before
        invoking this."""
        data = self._read_safe()
        if not data:
            raise AuthStoreNotBootstrapped(
                f"{self._path} doesn't exist. Cannot change password "
                f"before bootstrap. Run the bootstrap flow first."
            )
        data["password_hash"] = password_hash
        data["last_changed_at"] = _now_iso()
        self._write(data)

    def metadata(self) -> dict | None:
        """Return non-secret metadata (created_at, last_changed_at)
        for the /auth/session endpoint."""
        data = self._read_safe()
        if not data:
            return None
        return {
            "user": data.get("user", USER_NAME),
            "created_at": data.get("created_at"),
            "last_changed_at": data.get("last_changed_at"),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_safe(self) -> dict | None:
        """Return the parsed contents, or None on any failure
        (missing, malformed, permission error). Treating malformed
        as 'not bootstrapped' is intentional — the operator can
        recover by deleting and re-bootstrapping."""
        if not self._path.exists():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning(
                    "auth.json at %s is not a JSON object; treating as not bootstrapped",
                    self._path,
                )
                return None
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "auth.json at %s is unreadable / malformed: %s — "
                "treating as not bootstrapped",
                self._path, e,
            )
            return None

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        # Restrict permissions on POSIX. Windows: skipped (homelab dir
        # is operator-only).
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AuthStoreAlreadyBootstrapped(RuntimeError):
    """Bootstrap attempted but auth.json already present."""


class AuthStoreNotBootstrapped(RuntimeError):
    """Password change attempted before bootstrap."""
