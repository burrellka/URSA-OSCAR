"""Manual-log CRUD endpoints — Phase 3 Item 3B.

Five typed log shapes (medication, symptom, alertness, sleep_environment,
freeform) defined in ``models.manual_logs``. Discriminated by log_type
at the request body level so FastAPI / Pydantic validate the per-type
shape automatically.

Endpoints:
    GET    /api/v1/manual-logs?start=&end=&log_type=&category=
    POST   /api/v1/manual-logs
    GET    /api/v1/manual-logs/{log_id}
    PATCH  /api/v1/manual-logs/{log_id}
    DELETE /api/v1/manual-logs/{log_id}

The list endpoint defaults to the last 30 days when no start/end provided
so the Manual Logs UI's "recent entries" table works without explicit
date params.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from ..models.manual_logs import (
    AlertnessLog,
    FreeformLog,
    ManualLogEntry,
    MedicationLog,
    SleepEnvironmentLog,
    SymptomLog,
    _ManualLogBase,
)
from ..storage.repositories import manual_logs as manual_logs_repo

router = APIRouter(prefix="/api/v1/manual-logs", tags=["manual-logs"])


_VALID_LOG_TYPES = {
    "medication", "symptom", "alertness", "sleep_environment", "freeform",
}


@router.get("")
def list_manual_logs(
    request: Request,
    start: date_t | None = Query(
        default=None,
        description="Inclusive lower-bound date. Defaults to 30 days ago.",
    ),
    end: date_t | None = Query(
        default=None,
        description="Inclusive upper-bound date. Defaults to today.",
    ),
    log_type: str | None = Query(
        default=None,
        description="Filter to a single log_type (medication / symptom / alertness / "
                    "sleep_environment / freeform).",
    ),
    category: str | None = Query(
        default=None,
        description="Filter by the legacy `category` column. Mostly useful for the "
                    "freeform 'title' surface today.",
    ),
) -> list[dict[str, Any]]:
    """List typed log entries in the date range, optionally filtered by
    type or category. Returns a list of discriminated-union entries
    (FastAPI serializes each per its `log_type`)."""
    db = request.app.state.db

    if log_type is not None and log_type not in _VALID_LOG_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"log_type must be one of {sorted(_VALID_LOG_TYPES)}",
        )

    if end is None:
        end = date_t.today()
    if start is None:
        start = end - timedelta(days=30)
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    entries = manual_logs_repo.list_for_range(db, start, end, log_type, category)
    return [e.model_dump(mode="json") for e in entries]


@router.post("", response_model=None, status_code=201)
def create_manual_log(
    request: Request,
    entry: Annotated[ManualLogEntry, Body(discriminator="log_type")],
) -> dict[str, Any]:
    """Create a typed manual-log entry. Request body shape is determined
    by the `log_type` discriminator.

    Examples:
      {"log_type": "medication", "date": "2026-05-13",
       "timestamp": "2026-05-13T21:00:00", "name": "Melatonin",
       "dose": 3, "dose_unit": "mg"}

      {"log_type": "alertness", "date": "2026-05-13",
       "timestamp": "2026-05-13T08:00:00", "score": 7}
    """
    db = request.app.state.db
    saved = manual_logs_repo.insert(db, entry)
    return saved.model_dump(mode="json")


@router.get("/{log_id}")
def get_manual_log(log_id: int, request: Request) -> dict[str, Any]:
    db = request.app.state.db
    entry = manual_logs_repo.get_by_id(db, log_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No manual_log with id={log_id}")
    return entry.model_dump(mode="json")


@router.patch("/{log_id}")
def update_manual_log(
    log_id: int,
    request: Request,
    patch: Annotated[dict[str, Any], Body(...)],
) -> dict[str, Any]:
    """Partial update. The patch body is a flat dict; field names map to
    the typed entry's fields (the repo translates to row-shape columns).

    For now only a fixed set of fields are patchable per type:
      medication:        name, dose, dose_unit, notes, timestamp, date
      symptom:           name, severity, notes, timestamp, date
      alertness:         score, notes, timestamp, date
      sleep_environment: temperature_c, noise_level, light_level,
                         bed_partner_present, notes, timestamp, date
                         (sleep_environment values rewrite the packed
                         JSON blob — partial-field updates aren't yet
                         supported within the blob)
      freeform:          title, body, notes, timestamp, date
    """
    db = request.app.state.db
    existing = manual_logs_repo.get_by_id(db, log_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No manual_log with id={log_id}")

    row_patch = _patch_to_row_columns(existing, patch)
    saved = manual_logs_repo.update_partial(db, log_id, row_patch)
    if saved is None:
        # Race — the row got deleted between our get_by_id and update.
        raise HTTPException(status_code=404, detail=f"No manual_log with id={log_id}")
    return saved.model_dump(mode="json")


@router.delete("/{log_id}", status_code=204)
def delete_manual_log(log_id: int, request: Request) -> None:
    db = request.app.state.db
    deleted = manual_logs_repo.delete_by_id(db, log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No manual_log with id={log_id}")
    return None


def _patch_to_row_columns(
    existing: _ManualLogBase,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Translate a typed-field patch dict into row-shape column updates.

    The translation depends on the existing entry's log_type because each
    type maps differently into value_text / value_numeric / unit / category.
    Unknown keys are dropped silently — better UX than failing the PATCH.
    Common fields (notes, timestamp, date) pass through unchanged.
    """
    out: dict[str, Any] = {}

    # Common pass-through fields.
    for k in ("notes", "timestamp", "date"):
        if k in patch:
            out[k] = patch[k]

    if isinstance(existing, MedicationLog):
        if "name" in patch:
            out["value_text"] = patch["name"]
        if "dose" in patch:
            out["value_numeric"] = patch["dose"]
        if "dose_unit" in patch:
            out["unit"] = patch["dose_unit"]
    elif isinstance(existing, SymptomLog):
        if "name" in patch:
            out["value_text"] = patch["name"]
        if "severity" in patch:
            out["value_numeric"] = patch["severity"]
    elif isinstance(existing, AlertnessLog):
        if "score" in patch:
            out["value_numeric"] = patch["score"]
    elif isinstance(existing, SleepEnvironmentLog):
        # Reconstruct the entry with the patched fields then re-serialize
        # the JSON blob. Partial-field update within the blob this way
        # rather than parse/edit/repack inline.
        updated_kwargs = {
            "temperature_c": existing.temperature_c,
            "noise_level": existing.noise_level,
            "light_level": existing.light_level,
            "bed_partner_present": existing.bed_partner_present,
        }
        for k in updated_kwargs:
            if k in patch:
                updated_kwargs[k] = patch[k]
        rebuilt = existing.model_copy(update=updated_kwargs)
        out["value_text"] = rebuilt.to_storage_dict()["value_text"]
    elif isinstance(existing, FreeformLog):
        if "title" in patch:
            out["category"] = patch["title"]
        if "body" in patch:
            out["value_text"] = patch["body"]

    return out
