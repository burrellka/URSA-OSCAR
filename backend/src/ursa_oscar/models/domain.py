"""Pydantic domain models.

These are the wire types FastAPI returns and the MCP server wraps in the
{"ok": True, "data": ...} envelope (Design v1.1 Decision 10 / ADR-002).
Repositories also coerce DuckDB rows into these where convenient.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


EventType = Literal[
    "ClearAirway",   # CA — central apnea
    "Obstructive",   # OA
    "Apnea",         # A — unclassified apnea
    "Hypopnea",      # H
    "RERA",          # respiratory effort related arousal
    "LargeLeak",     # leak > redline for sustained period
    "FlowLimit",     # flow limitation episode (sub-hypopnea)
    "PeriodicBreathing",
    "CheyneStokes",
]


class NightlySummary(BaseModel):
    """One row of the nightly_summary table.

    All optional fields default to None — equipment-setting columns come from
    STR.edf which is not always present in fixture data (see Phase 0 V1 notes).
    """
    date: date_t
    session_count: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_time_minutes: int | None = None

    total_ahi: float | None = None
    obstructive_ahi: float | None = None
    central_ahi: float | None = None
    hypopnea_index: float | None = None
    rera_index: float | None = None

    median_pressure: float | None = None
    p95_pressure: float | None = None
    p995_pressure: float | None = None
    median_epap: float | None = None
    p95_epap: float | None = None
    p995_epap: float | None = None
    median_leak: float | None = None
    p95_leak: float | None = None
    p995_leak: float | None = None

    minutes_in_apnea: int | None = None
    minutes_over_leak_redline: float | None = None
    cheyne_stokes_pct: float | None = None
    large_leak_pct: float | None = None

    machine_model: str | None = None
    mode: str | None = None
    min_pressure_setting: float | None = None
    max_pressure_setting: float | None = None
    epr_level: int | None = None
    ramp_time_minutes: int | None = None
    humidity_level: str | None = None
    mask_type: str | None = None

    # Schema v2 — Device-Settings expansion (Phase 2 polish)
    antibacterial_filter: str | None = None
    climate_control: str | None = None
    epr_mode: str | None = None
    humidifier_status: str | None = None
    patient_view: str | None = None
    response_mode: str | None = None
    smart_start: str | None = None
    temperature_celsius: float | None = None
    temperature_enable: str | None = None

    last_updated: datetime | None = None


class NightlyEvent(BaseModel):
    id: int | None = None
    date: date_t
    timestamp: datetime
    session_id: int | None = None
    event_type: EventType
    duration_seconds: float | None = None
    pressure_at_event: float | None = None
    epap_at_event: float | None = None
    flow_at_event: float | None = None
    leak_at_event: float | None = None


class TimeseriesPoint(BaseModel):
    """One row of any *_timeseries table.

    Repositories return TimeseriesPoint[]; the `value` field is interpreted
    per-table (pressure / leak_rate / flow_rate / etc).
    """
    date: date_t
    timestamp: datetime
    value: float
    secondary_value: float | None = None  # e.g., EPAP alongside Pressure


class SkippedNight(BaseModel):
    """A night dir the importer chose not to ingest. Surfaced inline on
    ImportLogEntry so the UI can show partial-success details."""
    date: date_t
    reason: str


class ImportLogEntry(BaseModel):
    id: int | None = None
    import_timestamp: datetime | None = None
    source_path: str
    nights_imported: int
    earliest_date: date_t | None = None
    latest_date: date_t | None = None
    # Phase 3 Item 1C: tri-state discriminator derived in the importer.
    #   completed — every night dir landed cleanly; nights_skipped == 0
    #   partial   — some nights landed, some were skipped with reasons
    #   failed    — no nights imported; either every dir errored or a
    #               fatal pre-loop error (path missing, etc.)
    # `pending` and `running` are retained for the Phase 4 async-job
    # surface where an import is in flight.
    status: Literal["pending", "running", "completed", "partial", "failed"]
    error_message: str | None = None
    # Phase 2 polish 0.4.2 — per-night resilient import. Empty / corrupt
    # night dirs are skipped individually rather than failing the whole
    # import. nights_skipped is len(skipped); they're carried together for
    # UI ergonomics. Default empty/0 for back-compat with v1 callers.
    nights_skipped: int = 0
    skipped: list[SkippedNight] = []


class ManualLog(BaseModel):
    id: int | None = None
    date: date_t
    log_type: str = Field(description="medication / symptom / mood / alertness / note")
    timestamp: datetime
    value_text: str | None = None
    value_numeric: float | None = None
    unit: str | None = None
    category: str | None = None
    notes: str | None = None
    last_updated: datetime | None = None
