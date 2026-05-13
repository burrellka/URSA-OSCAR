"""File-backed vocabulary store — Phase 3 Item 3C.

vocab.json holds the per-instance autocomplete candidate lists for the
Manual Logs UI: medication names, symptom names, environment-tag values,
free-text categories. One top-level key per field, value is a sorted
list of strings.

Same first-start + locking discipline as ``profile_store.py``: empty
generic stub ships in the wheel as ``vocab.json.community-default``;
the API's lifespan hook copies it to ``/data/vocab.json`` if absent.
All writes go through the DuckDBManager RLock so file updates can't
interleave with each other or with profile.json writes (the sync
service relies on this).

The `medication_name` field is special — it's the materialized view
of Profile's ``clinical.active_medications[].name`` plus any historical
names that have ever appeared (medications never get removed from
vocab so historical Manual Logs continue to render even after the user
discontinues a med). The bidirectional sync logic lives in
``services/profile_vocab_sync.py``.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from .db import DuckDBManager

logger = logging.getLogger(__name__)


_DEFAULT_RESOURCE_PKG = "ursa_oscar.data"
_DEFAULT_RESOURCE_NAME = "vocab.json.community-default"


def ensure_initialized(vocab_path: Path) -> bool:
    """If ``vocab_path`` doesn't exist, copy the packaged community
    default into place. Idempotent."""
    vocab_path = Path(vocab_path)
    if vocab_path.exists():
        logger.info(
            "vocab_store: vocab.json already present at %s — leaving as-is.",
            vocab_path,
        )
        return False
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    pkg = files(_DEFAULT_RESOURCE_PKG)
    src = pkg / _DEFAULT_RESOURCE_NAME
    with as_file(src) as src_path:
        shutil.copy(src_path, vocab_path)
    logger.info(
        "vocab_store: initialized %s from packaged community default.",
        vocab_path,
    )
    return True


def read(vocab_path: Path) -> dict[str, Any]:
    """Return the vocab dict. No Pydantic validation — vocab.json is a
    plain key → list-of-strings map plus version + last_updated."""
    vocab_path = Path(vocab_path)
    with open(vocab_path, encoding="utf-8") as f:
        return json.load(f)


def get_field(vocab_path: Path, field: str) -> list[str]:
    """Return one field's list, or [] if the field isn't present."""
    raw = read(vocab_path)
    val = raw.get(field, [])
    if not isinstance(val, list):
        return []
    return list(val)


def add_value(
    db: DuckDBManager,
    vocab_path: Path,
    field: str,
    value: str,
) -> list[str]:
    """Ensure ``value`` is present in ``field``'s list. Returns the updated
    list (sorted, lowercase-deduped).

    No-op if the value already exists (case-insensitive compare).
    Held under the DuckDBManager RLock so this serializes against profile
    writes — important for the bidirectional sync.
    """
    value = value.strip()
    if not value:
        raise ValueError("vocab value cannot be empty")

    vocab_path = Path(vocab_path)
    with db.serialized():
        raw = read(vocab_path)
        existing = raw.get(field, []) or []
        if not isinstance(existing, list):
            existing = []
        existing_lower = {v.lower() for v in existing if isinstance(v, str)}
        if value.lower() not in existing_lower:
            existing.append(value)
            existing = sorted(set(existing), key=lambda s: s.lower())
            raw[field] = existing
            raw["last_updated"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(vocab_path, raw)
        return list(existing)


def _atomic_write(vocab_path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to a .tmp sibling, then atomic-rename. Same crash-safety
    pattern as profile_store.write()."""
    tmp_path = vocab_path.with_suffix(vocab_path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
        f.flush()
    tmp_path.replace(vocab_path)


def ensure_values(
    db: DuckDBManager,
    vocab_path: Path,
    field: str,
    values: list[str],
) -> list[str]:
    """Ensure all of ``values`` appear under ``field``. No removals.

    Used by the profile→vocab sync direction: when active_medications
    changes, every active med's name must be in vocab.medication_name,
    but discontinued meds stay in vocab.
    """
    vocab_path = Path(vocab_path)
    with db.serialized():
        raw = read(vocab_path)
        existing = raw.get(field, []) or []
        if not isinstance(existing, list):
            existing = []
        existing_lower = {v.lower() for v in existing if isinstance(v, str)}

        added = False
        for v in values:
            v_clean = v.strip()
            if not v_clean:
                continue
            if v_clean.lower() not in existing_lower:
                existing.append(v_clean)
                existing_lower.add(v_clean.lower())
                added = True

        if added:
            existing = sorted(set(existing), key=lambda s: s.lower())
            raw[field] = existing
            raw["last_updated"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(vocab_path, raw)

        return list(existing)
