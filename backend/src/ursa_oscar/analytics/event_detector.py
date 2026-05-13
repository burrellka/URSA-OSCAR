"""Event extraction from EDF data.

The AirSense 11 detects respiratory events in real-time during the session and
records them in EVE.edf via TAL annotations. We trust those device-emitted
events as the primary source for Phase 1 — this matches OSCAR's behavior,
which also reads the device's onboard log rather than re-detecting from raw
flow on these specific ResMed machines.

This module is a normalization pass over `edf_parser.parse_events`:
- Maps ParsedEvent → NightlyEvent (the domain model that lands in DuckDB)
- Attaches the session_id (positional index within the night, 1-based)
- Optionally enriches each event with at-event signal values via
  `enrich_with_signals` when waveform data is available

Future enhancements (not Phase 1):
- Re-detection from BRP.edf flow waveform per OSCAR's calcs.cpp algorithms,
  for cross-validation or filling gaps when EVE.edf is corrupt.
"""
from __future__ import annotations

from datetime import datetime, date as date_t

import numpy as np

from ..models.domain import NightlyEvent
from .edf_parser import ParsedEvent, SessionEDFs, WaveformSignal, parse_events


def events_for_session(session: SessionEDFs, session_id: int) -> list[NightlyEvent]:
    """Extract NightlyEvent records from one SessionEDFs bundle.

    `session_id` is the 1-based ordinal of this session within its night.
    Empty / corrupt EVE.edf yields an empty list (not an error).
    """
    if session.eve_path is None:
        return []

    parsed = parse_events(session.eve_path)
    out: list[NightlyEvent] = []
    for ev in parsed:
        out.append(
            NightlyEvent(
                date=ev.timestamp.date(),
                timestamp=ev.timestamp,
                session_id=session_id,
                event_type=ev.event_type,
                duration_seconds=ev.duration_seconds,
            )
        )
    return out


def enrich_with_signals(
    events: list[NightlyEvent],
    *,
    pressure: WaveformSignal | None = None,
    epap: WaveformSignal | None = None,
    leak: WaveformSignal | None = None,
    flow: WaveformSignal | None = None,
) -> list[NightlyEvent]:
    """Annotate each event with the contemporaneous signal value.

    Lookups use nearest-sample matching (most-recent past-or-equal). Out-of-
    range timestamps yield None on that field. Returns a NEW list — does not
    mutate the input.
    """
    pressure_ts = pressure.timestamps() if pressure is not None else None
    epap_ts = epap.timestamps() if epap is not None else None
    leak_ts = leak.timestamps() if leak is not None else None
    flow_ts = flow.timestamps() if flow is not None else None

    enriched: list[NightlyEvent] = []
    for ev in events:
        target = np.datetime64(ev.timestamp).astype("datetime64[ms]")
        update = {}
        if pressure is not None and pressure_ts is not None and pressure.values.size:
            idx = _nearest_past_idx(pressure_ts, target)
            update["pressure_at_event"] = (
                float(pressure.values[idx]) if idx is not None else None
            )
        if epap is not None and epap_ts is not None and epap.values.size:
            idx = _nearest_past_idx(epap_ts, target)
            update["epap_at_event"] = (
                float(epap.values[idx]) if idx is not None else None
            )
        if leak is not None and leak_ts is not None and leak.values.size:
            idx = _nearest_past_idx(leak_ts, target)
            update["leak_at_event"] = (
                float(leak.values[idx]) if idx is not None else None
            )
        if flow is not None and flow_ts is not None and flow.values.size:
            idx = _nearest_past_idx(flow_ts, target)
            update["flow_at_event"] = (
                float(flow.values[idx]) if idx is not None else None
            )
        enriched.append(ev.model_copy(update=update))
    return enriched


def _nearest_past_idx(timestamps: np.ndarray, target: np.datetime64) -> int | None:
    """Find the index of the latest timestamp ≤ target. None if out of range."""
    if timestamps.size == 0:
        return None
    pos = int(np.searchsorted(timestamps, target, side="right")) - 1
    if pos < 0:
        return None
    return pos
