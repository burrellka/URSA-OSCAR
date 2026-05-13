"""Assemble per-night NightlySummary + the event/timeseries rows it implies.

Inputs: a list of non-empty SessionAggregate objects belonging to the same
night, and the canonical `night_date` that identifies the night.

Outputs:
- One NightlySummary record
- One flat list of NightlyEvent records (with session_id assigned per-session)
- Per-series time-series tuples ready for `timeseries.bulk_insert`

Night-assignment (Decision 8 / noon-split):
- The "night" identifier is the date of the DATALOG directory that contained
  the source EDF files. The AirSense 11 files morning-after sessions (e.g.,
  the 02:55 5/8 session) under the previous evening's DATALOG dir (20260507/).
- This means the summary_builder's caller — the importer / ingestion service
  — must pass the DATALOG dir date as `night_date`, NOT each session's start
  date.

The 5/8 OSCAR-export quirk (Decision 8) where events get duplicated under two
synthetic session IDs is NOT replicated here. We count distinct events from
EVE.edf exactly once. canonical_targets.py contains the corrected counts.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime, timedelta

import numpy as np

from ..models.domain import NightlyEvent, NightlySummary
from .leak_detector import LEAK_REDLINE_LMIN
from .session_analyzer import SessionAggregate
from .settings_parser import EquipmentSettings


# Event types that count toward AHI per AASM convention.
# OSCAR-aligned: CA + A + OA + H (RERA is NOT counted; that's the RDI metric).
AHI_EVENT_TYPES: frozenset[str] = frozenset({
    "ClearAirway", "Apnea", "Obstructive", "Hypopnea",
})


def build_summary(
    night_date: date_t,
    sessions: list[SessionAggregate],
    *,
    equipment_settings: EquipmentSettings | None = None,
) -> tuple[NightlySummary, list[NightlyEvent]]:
    """Aggregate non-empty sessions into a NightlySummary + flat event list.

    Empty sessions should be filtered by the caller before this is invoked.
    `night_date` is the OSCAR night-attribution date (typically the DATALOG
    dir name converted to a date).

    `equipment_settings` (optional) populates the 7 device-setting columns
    on the resulting NightlySummary. Phase 1.5 caveat: these reflect the
    *most recent* settings at SD-card-export time — not the per-night
    prescription history. STR.edf-based per-night parsing is deferred.
    """
    if not sessions:
        return (
            NightlySummary(date=night_date, session_count=0, total_time_minutes=0),
            [],
        )

    # --- Time window across all sessions ---
    earliest = min(s.start for s in sessions)
    latest = max(s.end for s in sessions)
    total_seconds = sum(s.duration_minutes * 60.0 for s in sessions)
    total_minutes = int(round(total_seconds / 60.0))
    total_hours = total_seconds / 3600.0

    # --- Events ---
    all_events: list[NightlyEvent] = []
    for s in sessions:
        all_events.extend(s.events)
    all_events.sort(key=lambda e: e.timestamp)

    # Per-type counts (used for AHI components)
    counts: dict[str, int] = {}
    for ev in all_events:
        counts[ev.event_type] = counts.get(ev.event_type, 0) + 1

    def per_hour(event_type: str) -> float | None:
        if total_hours <= 0:
            return None
        return counts.get(event_type, 0) / total_hours

    ahi_event_count = sum(counts.get(t, 0) for t in AHI_EVENT_TYPES)
    total_ahi = (ahi_event_count / total_hours) if total_hours > 0 else None

    # --- Pressure / EPAP / Leak stats ---
    pressure_samples = _concat_waveform_values(s.pressure for s in sessions)
    epap_samples = _concat_waveform_values(s.epap for s in sessions)
    # The Leak.2s channel is in L/s; convert to L/min for clinical units.
    leak_samples_lmin = _concat_waveform_values(
        (s.leak for s in sessions),
        scale=60.0,
    )

    p_med, p_95, p_995 = _percentiles(pressure_samples)
    e_med, e_95, e_995 = _percentiles(epap_samples)
    l_med, l_95, l_995 = _percentiles(leak_samples_lmin)

    minutes_over_redline = 0.0
    large_leak_pct = 0.0
    if leak_samples_lmin.size > 0:
        # Aggregate across sessions; weights differ if sample rates differ,
        # but PLD's Leak.2s is uniformly 0.5 Hz so we use a constant 2s/sample.
        sample_period_s = 2.0
        over = leak_samples_lmin > LEAK_REDLINE_LMIN
        seconds_over = float(over.sum()) * sample_period_s
        total_leak_seconds = float(leak_samples_lmin.size) * sample_period_s
        minutes_over_redline = seconds_over / 60.0
        if total_leak_seconds > 0:
            large_leak_pct = seconds_over / total_leak_seconds * 100.0

    # --- minutes_in_apnea: sum of all apnea event durations ---
    minutes_in_apnea = sum(
        (e.duration_seconds or 0.0)
        for e in all_events
        if e.event_type in {"ClearAirway", "Obstructive", "Apnea"}
    ) / 60.0

    summary = NightlySummary(
        date=night_date,
        session_count=len(sessions),
        start_time=earliest,
        end_time=latest,
        total_time_minutes=total_minutes,
        total_ahi=total_ahi,
        obstructive_ahi=per_hour("Obstructive"),
        central_ahi=per_hour("ClearAirway"),
        hypopnea_index=per_hour("Hypopnea"),
        rera_index=per_hour("RERA"),
        median_pressure=p_med,
        p95_pressure=p_95,
        p995_pressure=p_995,
        median_epap=e_med,
        p95_epap=e_95,
        p995_epap=e_995,
        median_leak=l_med,
        p95_leak=l_95,
        p995_leak=l_995,
        minutes_in_apnea=int(round(minutes_in_apnea)),
        minutes_over_leak_redline=minutes_over_redline,
        large_leak_pct=large_leak_pct,
        # Equipment fields populated from Identification.json +
        # SETTINGS/CurrentSettings.json per Phase 1.5 / Decision 16.
        # Schema v2 (Phase 2 polish) added the second block — Antibacterial
        # Filter through Temperature Enable.
        machine_model=(equipment_settings.machine_model if equipment_settings else None),
        mode=(equipment_settings.mode if equipment_settings else None),
        min_pressure_setting=(equipment_settings.min_pressure_setting if equipment_settings else None),
        max_pressure_setting=(equipment_settings.max_pressure_setting if equipment_settings else None),
        epr_level=(equipment_settings.epr_level if equipment_settings else None),
        ramp_time_minutes=(equipment_settings.ramp_time_minutes if equipment_settings else None),
        humidity_level=(equipment_settings.humidity_level if equipment_settings else None),
        mask_type=(equipment_settings.mask_type if equipment_settings else None),
        antibacterial_filter=(equipment_settings.antibacterial_filter if equipment_settings else None),
        climate_control=(equipment_settings.climate_control if equipment_settings else None),
        epr_mode=(equipment_settings.epr_mode if equipment_settings else None),
        humidifier_status=(equipment_settings.humidifier_status if equipment_settings else None),
        patient_view=(equipment_settings.patient_view if equipment_settings else None),
        response_mode=(equipment_settings.response_mode if equipment_settings else None),
        smart_start=(equipment_settings.smart_start if equipment_settings else None),
        temperature_celsius=(equipment_settings.temperature_celsius if equipment_settings else None),
        temperature_enable=(equipment_settings.temperature_enable if equipment_settings else None),
    )

    # Tag events with the night date (overrides per-session date when the
    # session straddles midnight — every event belongs to its night, not its
    # wall-clock date).
    dated_events = [e.model_copy(update={"date": night_date}) for e in all_events]
    return summary, dated_events


def _concat_waveform_values(signal_iter, *, scale: float = 1.0) -> np.ndarray:
    """Flatten signals from multiple sessions into one numpy array. Skips Nones."""
    parts: list[np.ndarray] = []
    for sig in signal_iter:
        if sig is None or sig.values.size == 0:
            continue
        parts.append(sig.values.astype(np.float64) * scale)
    if not parts:
        return np.array([], dtype=np.float64)
    return np.concatenate(parts)


def _percentiles(values: np.ndarray) -> tuple[float | None, float | None, float | None]:
    if values.size == 0:
        return (None, None, None)
    return (
        float(np.median(values)),
        float(np.percentile(values, 95)),
        float(np.percentile(values, 99.5)),
    )
