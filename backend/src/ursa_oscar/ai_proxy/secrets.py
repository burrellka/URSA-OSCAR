"""Fernet-encrypted secret storage — Phase 5 Decision 7.

User-supplied API keys (Claude, OpenAI, Gemini, etc.) are encrypted at
rest with a per-instance Fernet key. The key itself comes from the
``URSA_OSCAR_SECRET_KEY`` env var; if absent on first startup the API
generates one and writes it to ``/data/secret_key.gen`` with a clear
startup-log instruction for the operator to copy it into compose env
and delete the file.

Storage layout:
  ``/data/secrets.enc`` — JSON blob of ``{key_name: encrypted_bytes_b64}``
                          mappings. Each secret is encrypted individually
                          so a corrupted entry doesn't lose all secrets.
  ``/data/secret_key.gen`` — Generated key (one-time, operator action
                             required). Permission 600 on POSIX. Removed
                             by the operator after they copy the value
                             into the compose env block.

The Settings UI never reads encrypted values — only a boolean
``api_key_set`` flag per provider. Replacement = call `set()` with the
new value; the old ciphertext is overwritten.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


SECRET_KEY_ENV = "URSA_OSCAR_SECRET_KEY"


class SecretStoreError(Exception):
    """Raised on encrypt/decrypt failures, key-loading issues, etc."""


class SecretStore:
    """Encrypted key/value store over a single JSON file.

    Reads + writes are simple — load → mutate → save. No concurrent-write
    handling needed because the API container is the sole writer and
    HTTP requests serialize at the FastAPI thread-pool level (no
    parallel POST /ai/config to one store)."""

    def __init__(self, key: bytes, store_path: Path) -> None:
        self._fernet = Fernet(key)
        self._path = store_path
        # In-memory cache populated lazily on first access.
        self._cache: dict[str, bytes] | None = None

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        """Decrypt and return the named secret, or None on miss / decrypt
        failure (a corrupted entry is treated as missing; the operator
        can re-enter the value)."""
        store = self._load()
        blob = store.get(key)
        if blob is None:
            return None
        try:
            return self._fernet.decrypt(blob).decode("utf-8")
        except InvalidToken:
            logger.exception("SecretStore.get: decrypt failed for %s", key)
            return None

    def set(self, key: str, value: str) -> None:
        """Encrypt and store a secret. Overwrites any prior value for
        the same key. Setting value="" deletes the entry — operators
        can clear a configured key via the Settings UI without a
        separate DELETE endpoint."""
        store = self._load()
        if not value:
            store.pop(key, None)
        else:
            store[key] = self._fernet.encrypt(value.encode("utf-8"))
        self._save(store)

    def delete(self, key: str) -> None:
        """Explicitly remove a secret. No-op when the key isn't set."""
        store = self._load()
        if key in store:
            store.pop(key)
            self._save(store)

    def list_keys(self) -> list[str]:
        """Return the names of stored secrets. NEVER returns values —
        the Settings UI uses this to render `api_key_set: bool` per
        provider in the masked config response."""
        return sorted(self._load().keys())

    def has(self, key: str) -> bool:
        """Lighter-weight existence probe."""
        return key in self._load()

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, bytes]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise SecretStoreError(
                f"secrets file at {self._path} is unreadable or corrupted: {e}"
            ) from e
        # JSON stores ciphertext as base64-encoded str; convert back to bytes.
        self._cache = {
            k: base64.b64decode(v.encode("ascii")) for k, v in raw.items()
        }
        return self._cache

    def _save(self, store: dict[str, bytes]) -> None:
        serializable = {
            k: base64.b64encode(v).decode("ascii") for k, v in store.items()
        }
        self._path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        # Restrict permissions on POSIX. On Windows we can't usefully
        # restrict, so we skip — the homelab data dir is already
        # operator-only there.
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        self._cache = dict(store)


# -------------------------------------------------------------------------
# Key resolution — handles env var + first-start key generation.
# -------------------------------------------------------------------------


def resolve_secret_key(data_dir: Path) -> bytes:
    """Load the Fernet key from the environment or — if unset — generate
    one and stage it for operator pickup.

    Returns the key bytes (urlsafe base64-encoded; what Fernet expects).

    First-start flow:
      1. ``URSA_OSCAR_SECRET_KEY`` unset
      2. Generate a Fernet key
      3. Write it to ``<data_dir>/secret_key.gen`` (mode 600)
      4. Log a prominent warning telling the operator to copy the key
         into their compose env and delete the file
      5. Use the key for this session — restarts before the operator
         action will re-generate (and overwrite) the file; harmless
         since no secrets have been stored yet
    """
    raw = os.environ.get(SECRET_KEY_ENV, "").strip()
    if raw:
        # Validate the key is well-formed Fernet (urlsafe-base64, 32 bytes).
        # Pass it through Fernet() — raises ValueError on bad input.
        try:
            Fernet(raw.encode("ascii"))
        except Exception as e:
            raise SecretStoreError(
                f"{SECRET_KEY_ENV} is set but not a valid Fernet key. "
                f"Expected urlsafe-base64-encoded 32 bytes. "
                f"Generate a fresh one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'. "
                f"Underlying error: {e}"
            ) from e
        return raw.encode("ascii")

    # No env key — generate and stage.
    key = Fernet.generate_key()
    data_dir.mkdir(parents=True, exist_ok=True)
    gen_path = data_dir / "secret_key.gen"
    gen_path.write_bytes(key)
    try:
        os.chmod(gen_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    logger.warning(
        "URSA_OSCAR_SECRET_KEY is unset. Generated a fresh Fernet key and "
        "wrote it to %s. Copy this value into your compose env as "
        "URSA_OSCAR_SECRET_KEY=<value>, then delete %s. Until you do, "
        "secrets stored this session will not survive a key rotation.",
        gen_path, gen_path,
    )
    return key
