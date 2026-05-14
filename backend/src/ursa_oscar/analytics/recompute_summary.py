"""Recompute nightly_summary from the DB, respecting excluded_sessions.

Phase 4 Ticket 1. Called by the session-toggle endpoint after a state
flip, and by the importer after a re-import when the night has existing
exclusions (so the on-disk summary reflects "what the operator currently
believes about this night" rather than "what the importer just parsed
straight off the SD card").

This is intentionally a re-aggregation of already-stored database state
— NOT a re-parse of EDF files. The importer is the only place that
touches raw EDF; from there on, the database is canonical. That lets
the toggle endpoint run in ~50ms even on a busy night, and lets us
ship session exclusion without having to keep the SD-card snapshot
around indefinitely.

Math correctness invariant: when zero sessions are excluded for a date,
recompute_summary() MUST produce the same NightlySummary as the
importer originally wrote (modulo `last_updated`). The handful of
tests in test_session_exclusion.py lock that invariant down against
the canonical 4-night fixture.
"""
from __future__ import annotations

import logging
from datetime import date as date_t
from typing import Optional

import numpy as np

from ..models.domain import NightlySummary
from ..storage.db import DuckDBManager
from ..storage.repositories import nights as nights_repo
from ..storage.repositories import sessions as sessions_repo
from .leak_detector import LEAK_REDLINE_LMIN
from .summary_builder import AHI_EVENT_TYPES


logger = logging.getLogger(__name__)


# Events whose duration sums into minutes_in_apnea. Matches the
# importer's set in summary_builder — RERA / Hypopnea don't qualify
# as "in apnea" minutes per the AASM clock.
_APNEA_EVENT_TYPES: frozenset[str] = frozenset({"ClearAirway", "Obstructive", "Apnea"})


def recompute_for_date(db: DuckDBManager, date: date_t) -> Optional[NightlySummary]:
    """Re-aggregate nightly_summary for ``date`` from the database,
    excluding any sessions present in excluded_sessions.

    Returns the updated NightlySummary, or None if no sessions exist
    for this date (importer never wrote rows, or every session has
    been deleted). The "all sessions excluded" case is distinct —
    that returns a NightlySummary with session_count=0, NULL AHI/
    percentile fields, and total_time_minutes=0. Architect note:
    excluding all sessions should yield NULL rather than 0 for AHI.

    Equipment-settings fields (machine_model, mode, all v2 device-
    setting columns) are preserved from the existing nightly_summary
    row — they describe the device, not the recording, and shouldn't
    change because the operator excluded a session.

    Idempotent: calling twice with no exclusion change is a no-op
    (same values, only last_updated bumps).
    """
    sessions = sessions_repo.list_for_date(db, date)
    if not sessions:
        # No session records at all — either the night was never imported,
        # or the importer's session-row writes haven't reached the night
        # yet (a pre-0.7.0 row with no v4-backfill match). Leave the
        # existing summary alone; the caller's check is to look at our
        # return value.
        logger.info("recompute_summary: no sessions for %s — skipping", date)
        return None

    non_excluded = [s for s in sessions if not s.excluded]
    non_excluded_ids = [s.session_id for s in non_excluded]
    existing = nights_repo.get_by_date(db, date)

    # If every session is excluded, write a "nothing to count" summary —
    # preserves the row's existence (so list_nights still shows the date)
    # but zeros / NULLs every aggregate. Equipment fields preserved.
    if not non_excluded:
        return _write_empty_summary(db, date, existing)

    # --- Time window across non-excluded sessions ---
    earliest = min(s.start_ts for s in non_excluded)
    latest = max(s.end_ts for s in non_excluded)
    total_minutes = sum(s.mask_on_minutes for s in non_excluded)
    total_hours = total_minutes / 60.0

    # --- Events, filtered to non-excluded session_ids ---
    counts, minutes_in_apnea = _event_aggregates(db, date, non_excluded_ids)

    def per_hour(event_type: str) -> Optional[float]:
        if total_hours <= 0:
            return None
        return counts.get(event_type, 0) / total_hours

    ahi_event_count = sum(counts.get(t, 0) for t in AHI_EVENT_TYPES)
    total_ahi = (ahi_event_count / total_hours) if total_hours > 0 else None

    # --- Time-series percentiles, filtered to non-excluded session ranges ---
    ranges = [(s.start_ts, s.end_ts) for s in non_excluded]
    p_med, p_95, p_995, e_med, e_95, e_995 = _pressure_percentiles(db, date, ranges)
    l_med, l_95, l_995, minutes_over_redline, large_leak_pct = _leak_stats(db, date, ranges)

    summary = NightlySummary(
        date=date,
        session_count=len(non_excluded),
        start_time=earliest,
        end_time=latest,
        total_time_minutes=int(round(total_minutes)),
        total_ahi=total_ahi,
        obstructive_ahi=per_hour("Obstructive"),
        central_ahi=per_hour("ClearAirway"),
        hypopnea_index=per_hour("Hypopnea"),
        rera_index=per_hour("RERA"),
        median_pressure=p_med, p95_pressure=p_95, p995_pressure=p_995,
        median_epap=e_med, p95_epap=e_95, p995_epap=e_995,
        median_leak=l_med, p95_leak=l_95, p995_leak=l_995,
        minutes_in_apnea=int(round(minutes_in_apnea)),
        minutes_over_leak_redline=minutes_over_redline,
        large_leak_pct=large_leak_pct,
        **_preserved_equipment_fields(existing),
    )
    nights_repo.upsert(db, summary)
    return summary


def _write_empty_summary(
    db: DuckDBManager,
    date: date_t,
    existing: Optional[NightlySummary],
) -> NightlySummary:
    """All sessions for the night are excluded — write a NULL-aggregate
    summary so the row stays in nightly_summary (operator can still see
    it on the Overview) but the stats reflect "no data counted." This
    is also what the importer writes for a night with no non-empty
    sessions."""
    summary = NightlySummary(
        date=date,
        session_count=0,
        start_time=None,
        end_time=None,
        total_time_minutes=0,
        # Every aggregate is None — architect spec: NULL not 0.
        total_ahi=None,
        obstructive_ahi=None,
        central_ahi=None,
        hypopnea_index=None,
        rera_index=None,
        median_pressure=None, p95_pressure=None, p995_pressure=None,
        median_epap=None, p95_epap=None, p995_epap=None,
        median_leak=None, p95_leak=None, p995_leak=None,
        minutes_in_apnea=0,
        minutes_over_leak_redline=0.0,
        large_leak_pct=0.0,
        **_preserved_equipment_fields(existing),
    )
    nights_repo.upsert(db, summary)
    return summary


def _preserved_equipment_fields(existing: Optional[NightlySummary]) -> dict:
    """Equipment / device-settings columns ride along with the night
    regardless of session exclusion. The recompute strictly touches
    recording-derived aggregates."""
    if existing is None:
        return {}
    return {
        "machine_model": existing.machine_model,
        "mode": existing.mode,
        "min_pressure_setting": existing.min_pressure_setting,
        "max_pressure_setting": existing.max_pressure_setting,
        "epr_level": existing.epr_level,
        "ramp_time_minutes": existing.ramp_time_minutes,
        "humidity_level": existing.humidity_level,
        "mask_type": existing.mask_type,
        "antibacterial_filter": existing.antibacterial_filter,
        "climate_control": existing.climate_control,
        "epr_mode": existing.epr_mode,
        "humidifier_status": existing.humidifier_status,
        "patient_view": existing.patient_view,
        "response_mode": existing.response_mode,
        "smart_start": existing.smart_start,
        "temperature_celsius": existing.temperature_celsius,
        "temperature_enable": existing.temperature_enable,
    }


def _event_aggregates(
    db: DuckDBManager, date: date_t, non_excluded_ids: list[int],
) -> tuple[dict[str, int], float]:
    """Return (event_type -> count, minutes_in_apnea) for the subset of
    events whose session_id is in ``non_excluded_ids``."""
    if not non_excluded_ids:
        return {}, 0.0
    placeholders = ",".join("?" for _ in non_excluded_ids)
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT event_type, COALESCE(duration_seconds, 0) AS dur
              FROM nightly_events
             WHERE date = ?
               AND session_id IN ({placeholders})
            """,
            (date, *non_excluded_ids),
        ).fetchall()
    counts: dict[str, int] = {}
    apnea_seconds = 0.0
    for event_type, dur in rows:
        counts[event_type] = counts.get(event_type, 0) + 1
        if event_type in _APNEA_EVENT_TYPES:
            apnea_seconds += float(dur or 0.0)
    return counts, apnea_seconds / 60.0


def _range_filter_sql(ranges: list[tuple]) -> tuple[str, list]:
    """Build a `(timestamp BETWEEN ? AND ? OR ...)` WHERE clause for
    filtering timeseries rows to the union of session [start, end]
    intervals. Returns the SQL fragment + bound parameters."""
    if not ranges:
        return "FALSE", []
    parts = []
    params: list = []
    for start, end in ranges:
        parts.append("(timestamp BETWEEN ? AND ?)")
        params.extend([start, end])
    return "(" + " OR ".join(parts) + ")", params


def _pressure_percentiles(
    db: DuckDBManager, date: date_t, ranges: list[tuple],
) -> tuple[Optional[float], Optional[float], Optional[float],
           Optional[float], Optional[float], Optional[float]]:
    """Median / p95 / p99.5 of pressure + EPAP across non-excluded sessions."""
    range_sql, range_params = _range_filter_sql(ranges)
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT pressure, epap
              FROM pressure_timeseries
             WHERE date = ? AND {range_sql}
            """,
            (date, *range_params),
        ).fetchall()
    if not rows:
        return (None, None, None, None, None, None)
    pressures = np.array([r[0] for r in rows if r[0] is not None], dtype=np.float64)
    epaps = np.array([r[1] for r in rows if r[1] is not None], dtype=np.float64)
    return (*_percentiles(pressures), *_percentiles(epaps))


def _leak_stats(
    db: DuckDBManager, date: date_t, ranges: list[tuple],
) -> tuple[Optional[float], Optional[float], Optional[float], float, float]:
    """Median / p95 / p99.5 of leak (L/min) + minutes_over_redline +
    large_leak_pct across non-excluded sessions."""
    range_sql, range_params = _range_filter_sql(ranges)
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT leak_rate
              FROM leak_timeseries
             WHERE date = ? AND {range_sql}
            """,
            (date, *range_params),
        ).fetchall()
    if not rows:
        return (None, None, None, 0.0, 0.0)
    values = np.array([r[0] for r in rows if r[0] is not None], dtype=np.float64)
    if values.size == 0:
        return (None, None, None, 0.0, 0.0)
    med, p95, p995 = _percentiles(values)
    # Leak.2s is 0.5 Hz — one sample every 2 seconds.
    sample_period_s = 2.0
    over = values > LEAK_REDLINE_LMIN
    seconds_over = float(over.sum()) * sample_period_s
    total_seconds = float(values.size) * sample_period_s
    minutes_over_redline = seconds_over / 60.0
    large_leak_pct = (seconds_over / total_seconds * 100.0) if total_seconds > 0 else 0.0
    return (med, p95, p995, minutes_over_redline, large_leak_pct)


def _percentiles(values: np.ndarray) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if values.size == 0:
        return (None, None, None)
    return (
        float(np.median(values)),
        float(np.percentile(values, 95)),
        float(np.percentile(values, 99.5)),
    )
