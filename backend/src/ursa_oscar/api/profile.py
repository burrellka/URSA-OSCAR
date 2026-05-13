"""User Profile CRUD — Phase 3 Item 3D.

Four endpoints backing the Profile UI (Item 4B) and the Tier-1 MCP tool
``get_user_profile`` (Item 5E):

    GET    /api/v1/profile          full profile
    PUT    /api/v1/profile          full replace (validates)
    PATCH  /api/v1/profile          partial deep-merge update
    GET    /api/v1/profile/schema   JSON Schema for UI form generation

Storage is a JSON file on the mounted volume (``/data/profile.json``).
See ``storage/profile_store.py`` for the file-I/O + locking discipline.

Bidirectional vocab sync. PUT/PATCH that touch
``clinical.active_medications`` trigger a sync into ``vocab.json`` so
the Manual Logs medication autocomplete stays current. The sync service
lives in ``services/profile_vocab_sync.py`` (Phase 3 Item 3C).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

from ..config import get_settings
from ..models.profile import UserProfile
from ..services import profile_vocab_sync
from ..storage import profile_store

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


def _profile_path():
    """Resolve the profile-file path lazily on each request.

    Settings.db_path points at /data/ursa-oscar.duckdb; the profile lives
    next to it as /data/profile.json. Pulling this at request time
    (rather than module import) keeps the test harness's
    monkeypatched URSA_OSCAR_DB_PATH effective.
    """
    return get_settings().db_path.parent / "profile.json"


def _vocab_path():
    """Mirror of _profile_path for the sister vocab.json file."""
    return get_settings().db_path.parent / "vocab.json"


@router.get("", response_model=UserProfile)
def get_profile(request: Request) -> UserProfile:
    """Return the current profile.

    First-start initialization (community-default copy) runs in the
    API lifespan hook, so by the time any request hits this endpoint
    the file is guaranteed to exist.
    """
    try:
        return profile_store.read(_profile_path())
    except FileNotFoundError:
        # Defensive — if lifespan init was bypassed (e.g., dev override
        # that points at a fresh path), surface a 503 rather than a 500.
        raise HTTPException(
            status_code=503,
            detail="Profile not initialized. API container's lifespan hook "
                   "must run ensure_initialized() before requests serve.",
        )


@router.put("", response_model=UserProfile)
def replace_profile(
    request: Request,
    profile: UserProfile,
) -> UserProfile:
    """Full replace. The incoming body must validate against UserProfile;
    last_updated is overwritten server-side regardless of what the caller
    sent. After write, profile→vocab sync ensures every active medication's
    name lives in ``vocab.medication_name``."""
    db = request.app.state.db
    written = profile_store.write(db, _profile_path(), profile)
    # Profile→Vocab sync (Decision 8). Idempotent; no-op if active_meds
    # is empty or every name is already in vocab.
    profile_vocab_sync.sync_profile_to_vocab(
        db, _profile_path(), _vocab_path(), written,
    )
    return written


@router.patch("", response_model=UserProfile)
def patch_profile(
    request: Request,
    diff: Annotated[dict[str, Any], Body(...)],
) -> UserProfile:
    """Partial update. Deep-merged into the current profile (nested dicts
    merge; lists replace). The merged result is validated against
    UserProfile before persisting. After write, profile→vocab sync runs
    if the diff touched ``clinical.active_medications``.

    Examples:
      {"display": {"timezone": "America/New_York"}}
      {"clinical": {"active_medications": [{"name": "Melatonin", "dose": 3, "dose_unit": "mg"}]}}
      {"personalization": {"active_concerns": ["Investigating AHI vs alcohol"]}}
    """
    db = request.app.state.db
    try:
        written = profile_store.patch(db, _profile_path(), diff)
    except Exception as e:
        # Pydantic ValidationError + JSON shape errors land here. Surface
        # the message to the caller — better UX than a generic 500.
        raise HTTPException(status_code=422, detail=f"Invalid profile patch: {e}")

    # Only fire the sync when the diff touched the section we mirror.
    # Keeps writes off vocab.json for the common case of timezone /
    # display-preference / personalization edits.
    if "clinical" in diff and "active_medications" in (diff.get("clinical") or {}):
        profile_vocab_sync.sync_profile_to_vocab(
            db, _profile_path(), _vocab_path(), written,
        )
    return written


@router.get("/schema")
def get_profile_schema() -> dict[str, Any]:
    """Return the JSON Schema for UserProfile.

    Used by the Profile UI (Item 4B) for form generation and field-level
    validation feedback. The schema is the same one Pydantic builds for
    request validation, so the UI and server agree on the shape.
    """
    return UserProfile.model_json_schema()
