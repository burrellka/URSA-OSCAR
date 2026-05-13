"""Defensive helpers — lifted verbatim from APEX template §6.5 / §6.6 / §6.9.

Kept here even when not used yet, so future tools (e.g., the Phase 3
add_manual_log, the export tool) drop into the same patterns.
"""
from __future__ import annotations

import os
from datetime import date as date_t
from datetime import datetime
from pathlib import Path
from typing import Any


# --- Datetime defenses ---------------------------------------------------

def _iso(v: Any) -> str | None:
    """Coerce datetime / ISO-string / None to ISO-8601 string (or None).

    Tolerant: str passes through unchanged; None stays None; unknown types
    return None rather than raising. Matches APEX template §6.5.
    """
    if v is None:
        return None
    if isinstance(v, (datetime, date_t)):
        return v.isoformat()
    if isinstance(v, str):
        return v
    return None


# Field names that should be coerced to a real datetime / date before write.
# URSA-OSCAR mostly handles dates server-side, but the Phase 3 add_manual_log
# tool will accept dict patches from the URSA agent and want this protection.
DATE_PATCH_FIELDS = frozenset({
    "date", "timestamp", "import_timestamp", "last_updated",
    "start_time", "end_time", "applied_at",
})


def _coerce_datetime_fields_in_patch(patch: dict) -> dict:
    """Convert ISO strings → datetime for fields in DATE_PATCH_FIELDS.

    Mutates and returns `patch`. ValueErrors are swallowed; downstream
    Pydantic validation will surface a clear error.
    """
    for k in list(patch.keys()):
        if k not in DATE_PATCH_FIELDS:
            continue
        v = patch[k]
        if isinstance(v, str):
            try:
                patch[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
    return patch


# --- Path-traversal defense for the export tool --------------------------

def _safe_path(*parts: str, root_env: str = "URSA_OSCAR_EXPORTS_PATH") -> Path:
    """Resolve a path within a configured root and reject any traversal.

    Matches APEX template §6.9 exactly. The export tool is the only place
    this is currently load-bearing, but lifted into helpers for
    consistency with the template.
    """
    base = Path(os.environ.get(root_env, "/data/exports")).resolve()
    for p in parts:
        if not isinstance(p, str):
            raise ValueError(f"Path components must be strings, got {type(p).__name__}")
        if p.startswith("/") or p.startswith("\\"):
            raise ValueError(f"Absolute path component rejected: {p!r}")
        if ".." in Path(p).parts:
            raise ValueError(f"Parent-traversal rejected: {p!r}")
    candidate = base.joinpath(*parts).resolve()
    candidate.relative_to(base)  # raises ValueError if escape
    return candidate
