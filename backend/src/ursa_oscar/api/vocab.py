"""Manual-logs vocabulary endpoints — Phase 3 Item 3C.

Backs the Manual Logs autocomplete dropdowns: medication names, symptom
names, environment-tag values. Storage in ``/data/vocab.json``; the
profile<->vocab sync service keeps ``medication_name`` aligned with
``profile.json.clinical.active_medications`` (see ``services/
profile_vocab_sync.py``).

Endpoints, mounted under ``/api/v1/manual-logs/vocab`` per Work Order v3
Item 3C reference:

    GET    /api/v1/manual-logs/vocab                 full vocab dict
    GET    /api/v1/manual-logs/vocab/{field}         single field's list
    POST   /api/v1/manual-logs/vocab                 add a value to a field

The POST shape is intentionally aligned with the autocomplete UX —
the user types into a typeahead, the UI fires POST {log_type, field,
value} on accept. If the field is ``medication_name``, the sync service
mirrors the new value back into Profile (vocab→profile direction).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import profile_vocab_sync
from ..storage import vocab_store

router = APIRouter(prefix="/api/v1/manual-logs/vocab", tags=["manual-logs-vocab"])


# Known vocab fields. Used to validate POST requests so a typo doesn't
# silently create a new top-level key in vocab.json.
_KNOWN_FIELDS = {
    "medication_name",
    "symptom_name",
    "noise_level",
    "light_level",
    "category",
}


class VocabAddRequest(BaseModel):
    """POST /api/v1/manual-logs/vocab body. log_type is informational —
    it's mostly there to make the autocomplete-side call self-describing
    in API logs (e.g., 'this medication was added during a medication
    quick-log'). The (field, value) pair drives the actual write."""

    log_type: str | None = Field(
        default=None,
        description="The manual-log type whose autocomplete fired this "
                    "addition. Not strictly required, but improves traceability.",
    )
    field: str = Field(
        description="Vocab field key (e.g., 'medication_name', 'symptom_name').",
    )
    value: str = Field(
        description="The new value to add. Whitespace-trimmed server-side; "
                    "case-insensitively deduped against existing entries.",
    )


def _vocab_path():
    return get_settings().db_path.parent / "vocab.json"


def _profile_path():
    return get_settings().db_path.parent / "profile.json"


@router.get("")
def get_full_vocab() -> dict[str, Any]:
    """Return the full vocab.json as a JSON document."""
    return vocab_store.read(_vocab_path())


@router.get("/{field}")
def get_vocab_field(field: str) -> list[str]:
    """Return a single field's autocomplete list. Returns an empty list
    if the field isn't present (rather than 404, so the UI can render
    an autocomplete with no suggestions without special-casing).
    """
    return vocab_store.get_field(_vocab_path(), field)


@router.post("")
def add_vocab_value(
    request: Request,
    body: Annotated[VocabAddRequest, Body(...)],
) -> dict[str, Any]:
    """Add a value to a vocab field. If the field is ``medication_name``,
    the value is also synced into ``profile.clinical.active_medications``
    via the bidirectional sync service (Decision 8).

    Returns the updated list for the field, plus a flag indicating
    whether the profile was also mutated.
    """
    if body.field not in _KNOWN_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown vocab field {body.field!r}. Known fields: {sorted(_KNOWN_FIELDS)}",
        )

    db = request.app.state.db
    try:
        updated = vocab_store.add_value(db, _vocab_path(), body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    profile_updated = False
    if body.field == "medication_name":
        result = profile_vocab_sync.sync_vocab_addition_to_profile(
            db, _profile_path(), body.field, body.value,
        )
        profile_updated = result is not None

    return {
        "field": body.field,
        "values": updated,
        "profile_active_medications_updated": profile_updated,
    }
