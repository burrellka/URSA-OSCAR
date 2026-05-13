"""Per-session aggregation from a SessionEDFs bundle.

One session = one mask-on period during the night. ResMed records each
session as up to five EDF files sharing a timestamp prefix:
- CSL.edf — annotation only (sometimes CSR Start / CSR End markers)
- EVE.edf — annotation only (respiratory events)
- BRP.edf — high-resolution flow + pressure waveform (25 Hz)
- PLD.edf — 2-second mask/leak/resp metrics
- SA2.edf — 1-second SpO2 + pulse (absent without pulse oximeter)

Some sessions are placeholders: trial mask-on with the device that never
captured anything meaningful. These have empty / zero-record EDF files. We
treat them as `is_empty == True` so the summary builder can drop them
without counting toward session_count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from ..models.domain import NightlyEvent
from .edf_parser import (
    SessionEDFs,
    WaveformSignal,
    parse_events,
    parse_header,
    parse_pld_signal,
    parse_waveform,
)
from .event_detector import enrich_with_signals, events_for_session
from .leak_detector import LeakStats, compute_leak_stats


@dataclass
class SessionAggregate:
    """All the per-session signals + events the summary builder needs."""
    session_id: int                          # 1-based ordinal within its night
    session_prefix: str                      # e.g., '20260508_220523'
    start: datetime
    end: datetime
    duration_minutes: float
    events: list[NightlyEvent]
    pressure: WaveformSignal | None
    epap: WaveformSignal | None
    leak: WaveformSignal | None
    leak_stats: LeakStats | None
    # All available raw waveforms keyed by PLD label, for the summary builder
    # to compute percentiles without re-parsing the file.
    pld_signals: dict[str, WaveformSignal] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """A session with no events AND no waveform data is a placeholder."""
        has_events = bool(self.events)
        has_waveform = self.pressure is not None and self.pressure.values.size > 0
        return not (has_events or has_waveform)


def analyze_session(session: SessionEDFs, session_id: int) -> SessionAggregate:
    """Build a SessionAggregate from one SessionEDFs bundle.

    Always returns a SessionAggregate (never None). Callers should check
    `.is_empty` to decide whether to keep it.
    """
    start = session.session_start
    duration_minutes = 0.0
    end = start

    # Discover the longest non-empty EDF to anchor session end time. Prefer
    # PLD (2-second cadence, full session); fall back to BRP (25 Hz); fall
    # back to EVE's header n_records (less reliable for empty/short files).
    duration_source = None
    for path in (session.pld_path, session.brp_path, session.sa2_path):
        if path is None:
            continue
        try:
            hdr = parse_header(path)
        except Exception:
            continue
        if hdr.n_records == 0:
            continue
        # Each record covers record_duration_seconds; multi-rate signals
        # share the record cadence so any rate × samples_per_record works.
        # ResMed PLD declares record_duration_seconds = 1.0 in our fixtures
        # but actual seconds covered = n_records * record_duration. For
        # consistency we use the file_duration from MNE when possible.
        duration_seconds = hdr.n_records * hdr.record_duration_seconds
        if duration_seconds > duration_minutes * 60.0:
            duration_minutes = duration_seconds / 60.0
            end = start + timedelta(seconds=duration_seconds)
            duration_source = path

    # Parse PLD signals once and cache them
    pld_signals: dict[str, WaveformSignal] = {}
    if session.pld_path is not None:
        try:
            for sig in parse_waveform(session.pld_path):
                pld_signals[sig.label] = sig
        except Exception:
            pld_signals = {}

    pressure = pld_signals.get("Press.2s")
    epap = pld_signals.get("EprPress.2s")
    leak = pld_signals.get("Leak.2s")

    # Events from EVE.edf, enriched with at-event signal values
    raw_events = events_for_session(session, session_id=session_id)
    events = enrich_with_signals(raw_events, pressure=pressure, epap=epap, leak=leak)

    # Leak metrics + synthetic LargeLeak events
    leak_stats: LeakStats | None = None
    if leak is not None and leak.values.size > 0:
        leak_stats = compute_leak_stats(leak, session_id=session_id)
        # Merge LL events into the session's event list so the summary
        # builder sees them in one stream.
        events = events + leak_stats.large_leak_events
        events.sort(key=lambda e: e.timestamp)

    # If we didn't get a duration from a waveform header but we have events,
    # use the last event as a lower bound for session end.
    if duration_minutes == 0.0 and events:
        last_ts = max(e.timestamp for e in events)
        end = last_ts
        duration_minutes = (end - start).total_seconds() / 60.0

    return SessionAggregate(
        session_id=session_id,
        session_prefix=session.session_timestamp_str,
        start=start,
        end=end,
        duration_minutes=duration_minutes,
        events=events,
        pressure=pressure,
        epap=epap,
        leak=leak,
        leak_stats=leak_stats,
        pld_signals=pld_signals,
    )
