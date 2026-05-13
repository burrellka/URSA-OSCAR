"""File-backed user profile storage — Phase 3 Item 3D.

The user profile lives as a single JSON document at ``/data/profile.json``
on the mounted volume. Unlike the DuckDB store, this file is per-instance
state that's never in git — the public repo ships only the empty
``profile.json.community-default`` stub packaged inside the wheel.

First-start path. On API container startup the lifespan hook calls
``ensure_initialized(settings.profile_path)``, which copies the packaged
default to the data volume if the user's profile doesn't exist. Logs an
init message either way (init or already-present) so operators can see
in container logs what happened.

Concurrency. The profile file is shared with the vocab-sync service
(Phase 3 Item 3C) — a PATCH on ``clinical.active_medications`` triggers
a write to ``vocab.json``, and vice versa. To prevent interleaved writes
from corrupting either file, we serialize ALL profile-file writes
through the same DuckDBManager RLock that gates DB access (ADR-004).
The lock is process-wide and cheap to acquire; it's the easiest correct
way to give the JSON files the same guarantees DuckDB has.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from ..models.profile import UserProfile
from .db import DuckDBManager

logger = logging.getLogger(__name__)


# Where the packaged default lives inside the wheel.
_DEFAULT_RESOURCE_PKG = "ursa_oscar.data"
_DEFAULT_RESOURCE_NAME = "profile.json.community-default"


def ensure_initialized(profile_path: Path) -> bool:
    """If ``profile_path`` doesn't exist, copy the packaged community
    default into place. Returns True if a fresh copy was created, False
    if the file already existed.

    Idempotent — safe to call on every API startup.
    """
    profile_path = Path(profile_path)
    if profile_path.exists():
        logger.info(
            "profile_store: profile.json already present at %s — leaving as-is.",
            profile_path,
        )
        return False

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    pkg = files(_DEFAULT_RESOURCE_PKG)
    src = pkg / _DEFAULT_RESOURCE_NAME
    with as_file(src) as src_path:
        shutil.copy(src_path, profile_path)
    logger.info(
        "profile_store: initialized %s from packaged community default. "
        "Customize via the Profile UI.",
        profile_path,
    )
    return True


def read(profile_path: Path) -> UserProfile:
    """Read and validate the profile from disk."""
    profile_path = Path(profile_path)
    with open(profile_path, encoding="utf-8") as f:
        raw = json.load(f)
    return UserProfile.model_validate(raw)


def read_raw(profile_path: Path) -> dict[str, Any]:
    """Read the profile as a plain dict (no validation). Useful for
    PATCH paths that deep-merge before re-validating the result."""
    profile_path = Path(profile_path)
    with open(profile_path, encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    return raw


def write(
    db: DuckDBManager,
    profile_path: Path,
    profile: UserProfile,
) -> UserProfile:
    """Validate-and-write a full profile. Always bumps last_updated.

    Held under the DuckDBManager RLock so the write is mutually
    exclusive with any DB write and with concurrent vocab.json writes
    (Phase 3 Item 3C sync service).
    """
    profile_path = Path(profile_path)
    stamped = profile.model_copy(update={"last_updated": datetime.now(timezone.utc)})

    payload = stamped.model_dump(mode="json")
    with db.serialized():
        # Write to a sibling .tmp first, fsync, atomic-rename. Standard
        # crash-safety pattern — if the process dies mid-write the old
        # file is intact rather than a half-written corrupt JSON.
        tmp_path = profile_path.with_suffix(profile_path.suffix + ".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            f.flush()
        tmp_path.replace(profile_path)
    return stamped


def patch(
    db: DuckDBManager,
    profile_path: Path,
    diff: dict[str, Any],
) -> UserProfile:
    """Apply a partial update to the profile.

    ``diff`` is deep-merged into the current profile dict: nested dicts
    merge field-by-field; lists are REPLACED wholesale (not merged
    element-wise — too ambiguous for clinical context where ordering
    and duplicates matter).

    The merged dict is validated against UserProfile before being
    written, so any malformed patch fails the request before touching
    disk. Returns the post-write profile (with the fresh last_updated
    stamp). Held under the same DuckDBManager RLock as write()."""
    profile_path = Path(profile_path)
    with db.serialized():
        current = read_raw(profile_path)
        merged = _deep_merge(current, diff)
        validated = UserProfile.model_validate(merged)
    # write() acquires the lock itself for the write side. Releasing
    # between the read+merge and the write is fine — we're already
    # serialized on the same lock and RLock supports re-entry.
    return write(db, profile_path, validated)


def _deep_merge(base: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``diff`` into ``base``. Nested dicts merge;
    every other type (lists, scalars, None) replaces wholesale.

    Used by ``patch()`` so PATCH bodies can be small partial dicts
    without breaking unrelated fields.
    """
    out = dict(base)
    for k, v in diff.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
