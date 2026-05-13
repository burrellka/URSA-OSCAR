"""Bidirectional sync between profile.json and vocab.json — Phase 3 Item 3C/3D.

Decision 8 (Work Order v3): ``profile.json.clinical.active_medications`` is
the authoritative list of medications the user is currently taking, and
``vocab.json.medication_name`` is the Manual Logs autocomplete catalog
of medication names ever-seen.

Sync rules:

- **Profile → Vocab.** On any write that updates
  ``clinical.active_medications``, ensure every active med's name is
  present in ``vocab.medication_name``. We never remove from vocab —
  discontinued medications keep their slot so historical Manual Logs
  render their value cleanly.

- **Vocab → Profile.** When ``POST /api/v1/manual-logs/vocab`` adds a
  new name under ``medication_name`` (triggered when the user types a
  novel medication into the Manual Logs autocomplete), add a minimal
  entry to ``profile.clinical.active_medications`` (name only; dose,
  schedule, etc. left None for the user to fill in via the Profile UI).

- **Conflict resolution.** If a PUT /profile or PATCH /profile removes
  a medication, the removal is honored. Vocab retains the name; the
  Profile's active list drops it. User-initiated removal wins over the
  implicit-add path.

Locking: all sync operations execute inside the DuckDBManager RLock
(via the underlying store functions). This serializes against API
requests touching either file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models.profile import ActiveMedication, UserProfile
from ..storage import profile_store, vocab_store
from ..storage.db import DuckDBManager

logger = logging.getLogger(__name__)


def sync_profile_to_vocab(
    db: DuckDBManager,
    profile_path: Path,
    vocab_path: Path,
    profile: UserProfile,
) -> None:
    """Profile → Vocab direction. Ensures every active medication's name
    appears in vocab.medication_name. Never removes names from vocab.

    Idempotent — no-op if every active med is already in vocab.
    """
    names = [m.name for m in profile.clinical.active_medications if m.name]
    if not names:
        return
    vocab_store.ensure_values(db, vocab_path, "medication_name", names)


def sync_vocab_addition_to_profile(
    db: DuckDBManager,
    profile_path: Path,
    field: str,
    value: str,
) -> UserProfile | None:
    """Vocab → Profile direction.

    Only fires for ``field='medication_name'``; other fields are
    autocomplete-only and don't have a Profile mirror.

    If the value is already an active medication (case-insensitive),
    no-op. Otherwise appends a minimal ActiveMedication (name only) to
    ``profile.clinical.active_medications`` and writes the profile.

    Returns the post-sync profile, or None if no profile update was
    needed (value already active, or field isn't medication_name).
    """
    if field != "medication_name":
        return None
    value = value.strip()
    if not value:
        return None

    current = profile_store.read(profile_path)
    existing = {m.name.lower() for m in current.clinical.active_medications}
    if value.lower() in existing:
        return None

    updated_meds = list(current.clinical.active_medications)
    updated_meds.append(ActiveMedication(name=value))
    patched = current.model_copy(
        update={
            "clinical": current.clinical.model_copy(
                update={"active_medications": updated_meds},
            ),
        },
    )
    written = profile_store.write(db, profile_path, patched)
    logger.info(
        "profile_vocab_sync: added %r to profile.clinical.active_medications "
        "via vocab→profile direction.",
        value,
    )
    return written
