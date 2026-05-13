"""Manual-log schemas — Phase 3 Item 3A.

Five discriminated log types that all serialize into the same physical
``manual_logs`` table row shape. The discriminator is ``log_type``; the
union is the canonical wire shape for ``POST /api/v1/manual-logs``,
``GET /api/v1/manual-logs``, and the per-entry CRUD endpoints.

The five types:
  medication        — dose tracking, drives Profile's active_medications
                       sync (see Item 3D)
  symptom           — severity-scored symptom log
  alertness         — KSS/Karolinska-style 1-10 self-report
  sleep_environment — bedroom temperature / noise / light reading
  freeform          — title + body free-text note

Storage mapping. The ``manual_logs`` DDL has a generic row shape (one
DOUBLE, four VARCHARs). Each typed model declares a ``to_storage_dict()``
that flattens it into that shape; ``from_storage_row()`` reconstructs
the typed model on read. For ``sleep_environment``, the multi-field
record is packed as JSON inside ``value_text`` — a deliberate choice
to keep the storage schema simple in v1. If sleep-environment fields
get heavy use, a Phase 4 schema migration can split them into proper
columns.

Date / timestamp semantics. ``date`` is the night the entry applies
to (typically the night you slept ON, which lets Daily View / Trends
join logs to nightly_summary by date). ``timestamp`` is the
wall-clock moment of the event itself (when you took the meds, when
you noticed the symptom). For events spanning midnight, the convention
is: anything from "going to bed" through "waking up the next morning"
attaches to the prior date.
"""
from __future__ import annotations

import json
from datetime import date as date_t
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Shared base — fields every log entry carries.
# ----------------------------------------------------------------------

class _ManualLogBase(BaseModel):
    """Fields common to every typed log model.

    Not exposed directly via the API — every wire payload is one of the
    typed subclasses below. Holds id (DB-assigned), the date the entry
    applies to, the wall-clock timestamp of the event itself, optional
    free-text notes, and the last_updated audit field stamped server-side.
    """
    id: int | None = None
    date: date_t
    timestamp: datetime
    notes: str | None = None
    last_updated: datetime | None = None


# ----------------------------------------------------------------------
# Typed log entries — one per quick-log button.
# ----------------------------------------------------------------------

class MedicationLog(_ManualLogBase):
    """A medication dose taken. The ``name`` field is the canonical
    medication name and must match (case-insensitive) one of the entries
    in ``profile.json.clinical.active_medications`` after Phase 3 Item 3D
    lands. If the user types a name not yet in active_medications, the
    sync service (Item 3C/D) adds it to Profile with minimal data."""

    log_type: Literal["medication"] = "medication"
    name: str = Field(description='Canonical medication name (e.g., "Melatonin").')
    dose: float | None = Field(
        default=None,
        description="Dose value taken at this timestamp. Numeric so it can be aggregated.",
    )
    dose_unit: str | None = Field(
        default=None,
        description='Unit for `dose` (e.g., "mg", "mL", "puffs"). Free text in v1.',
    )

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "log_type": self.log_type,
            "timestamp": self.timestamp,
            "value_text": self.name,
            "value_numeric": self.dose,
            "unit": self.dose_unit,
            "category": None,
            "notes": self.notes,
        }


class SymptomLog(_ManualLogBase):
    """A symptom observation with an optional 1-10 severity score."""

    log_type: Literal["symptom"] = "symptom"
    name: str = Field(description='Symptom name (e.g., "headache", "fatigue").')
    severity: float | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Self-reported severity, 0–10. Optional — leave None for "
                    "presence-only logs.",
    )

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "log_type": self.log_type,
            "timestamp": self.timestamp,
            "value_text": self.name,
            "value_numeric": self.severity,
            "unit": None,
            "category": None,
            "notes": self.notes,
        }


class AlertnessLog(_ManualLogBase):
    """Self-reported alertness. KSS-like 1–10 score (lower = sleepier)."""

    log_type: Literal["alertness"] = "alertness"
    score: float = Field(
        ge=1,
        le=10,
        description="Alertness score 1–10. Convention: 1 = extremely sleepy, "
                    "10 = extremely alert. (Inverted from the Karolinska "
                    "Sleepiness Scale's 1–9 where 1 = alert; URSA-OSCAR's "
                    "convention keeps high = good across all subjective scales.)",
    )

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "log_type": self.log_type,
            "timestamp": self.timestamp,
            "value_text": None,
            "value_numeric": self.score,
            "unit": None,
            "category": None,
            "notes": self.notes,
        }


class SleepEnvironmentLog(_ManualLogBase):
    """Bedroom-environment reading. Multiple optional fields — temperature,
    noise level, light level, bed-partner presence — packed as a single
    record. Storage: JSON-encoded in ``value_text``. Future schema
    expansion (Phase 4+) can split these into proper columns if usage
    warrants."""

    log_type: Literal["sleep_environment"] = "sleep_environment"
    temperature_c: float | None = Field(
        default=None,
        description="Bedroom temperature, °C. UI converts based on profile preference.",
    )
    noise_level: Literal["quiet", "moderate", "loud"] | None = None
    light_level: Literal["dark", "dim", "bright"] | None = None
    bed_partner_present: bool | None = None

    def to_storage_dict(self) -> dict[str, Any]:
        payload = {
            "temperature_c": self.temperature_c,
            "noise_level": self.noise_level,
            "light_level": self.light_level,
            "bed_partner_present": self.bed_partner_present,
        }
        # Drop None fields so the stored JSON stays compact.
        payload = {k: v for k, v in payload.items() if v is not None}
        return {
            "date": self.date,
            "log_type": self.log_type,
            "timestamp": self.timestamp,
            "value_text": json.dumps(payload) if payload else None,
            "value_numeric": None,
            "unit": None,
            "category": None,
            "notes": self.notes,
        }


class FreeformLog(_ManualLogBase):
    """Free-text note — title + body. Catch-all for anything that doesn't
    fit the other four shapes."""

    log_type: Literal["freeform"] = "freeform"
    title: str | None = Field(default=None, description="Optional short title.")
    body: str = Field(description="Required note body.")

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "log_type": self.log_type,
            "timestamp": self.timestamp,
            "value_text": self.body,
            "value_numeric": None,
            "unit": None,
            "category": self.title,
            "notes": self.notes,
        }


# ----------------------------------------------------------------------
# Discriminated union — the canonical wire shape for the API.
# ----------------------------------------------------------------------

ManualLogEntry = Annotated[
    Union[MedicationLog, SymptomLog, AlertnessLog, SleepEnvironmentLog, FreeformLog],
    Field(discriminator="log_type"),
]


# Map of log_type value -> concrete model class. Used by from_storage_row()
# to dispatch.
_LOG_TYPE_TO_MODEL: dict[str, type[_ManualLogBase]] = {
    "medication": MedicationLog,
    "symptom": SymptomLog,
    "alertness": AlertnessLog,
    "sleep_environment": SleepEnvironmentLog,
    "freeform": FreeformLog,
}


def from_storage_row(row: dict[str, Any]) -> _ManualLogBase:
    """Reconstruct the appropriate typed log model from a manual_logs row.

    Inverse of each ``to_storage_dict()``. Raises ``ValueError`` if
    ``row['log_type']`` isn't one of the known five.
    """
    lt = row.get("log_type")
    if lt not in _LOG_TYPE_TO_MODEL:
        raise ValueError(f"Unknown log_type: {lt!r}")

    common = {
        "id": row.get("id"),
        "date": row.get("date"),
        "timestamp": row.get("timestamp"),
        "notes": row.get("notes"),
        "last_updated": row.get("last_updated"),
    }

    if lt == "medication":
        return MedicationLog(
            **common,
            name=row.get("value_text") or "",
            dose=row.get("value_numeric"),
            dose_unit=row.get("unit"),
        )
    if lt == "symptom":
        return SymptomLog(
            **common,
            name=row.get("value_text") or "",
            severity=row.get("value_numeric"),
        )
    if lt == "alertness":
        return AlertnessLog(
            **common,
            score=row.get("value_numeric") or 0.0,
        )
    if lt == "sleep_environment":
        packed = row.get("value_text")
        env = json.loads(packed) if packed else {}
        return SleepEnvironmentLog(
            **common,
            temperature_c=env.get("temperature_c"),
            noise_level=env.get("noise_level"),
            light_level=env.get("light_level"),
            bed_partner_present=env.get("bed_partner_present"),
        )
    if lt == "freeform":
        return FreeformLog(
            **common,
            title=row.get("category"),
            body=row.get("value_text") or "",
        )

    # Unreachable — _LOG_TYPE_TO_MODEL check above guards this.
    raise AssertionError(f"Unhandled log_type: {lt!r}")


__all__ = [
    "MedicationLog",
    "SymptomLog",
    "AlertnessLog",
    "SleepEnvironmentLog",
    "FreeformLog",
    "ManualLogEntry",
    "from_storage_row",
]
