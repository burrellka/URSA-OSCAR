"""Leak metrics computed from PLD.edf Leak.2s channel.

ResMed reports leak in L/s; we convert to L/min (×60) for clinical-conventional
units. The 24 L/min "redline" threshold is the AirSense 11 spec for
unacceptable mask seal. Sustained periods over redline (≥10 seconds) are the
basis for the LargeLeak event class.

Outputs land in nightly_summary as:
- median_leak / p95_leak / p995_leak (L/min)
- minutes_over_leak_redline (float minutes)
- large_leak_pct (% of mask-on time over redline)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from ..models.domain import NightlyEvent
from .edf_parser import WaveformSignal


LEAK_REDLINE_LMIN: float = 24.0           # AirSense 11 spec threshold
LARGE_LEAK_MIN_DURATION_S: float = 10.0   # OSCAR's threshold for emitting LL events


@dataclass
class LeakStats:
    median_lmin: float
    p95_lmin: float
    p995_lmin: float
    minutes_over_redline: float
    large_leak_pct: float                  # 0..100
    large_leak_events: list[NightlyEvent]


def leak_signal_to_lmin(leak_signal: WaveformSignal) -> np.ndarray:
    """Convert a PLD Leak.2s channel from its declared unit to L/min.

    PLD records leak in L/s. Multiplying by 60 converts to L/min, which is
    the clinical convention OSCAR and the device's onboard report use.
    """
    if leak_signal.unit.lower() == "l/min":
        return leak_signal.values.astype(np.float64)
    # Default assumption (matches observed ResMed PLD output)
    return leak_signal.values.astype(np.float64) * 60.0


def compute_leak_stats(
    leak_signal: WaveformSignal,
    *,
    session_id: int | None = None,
) -> LeakStats:
    """Compute the full leak summary from a session's Leak.2s channel.

    Returns zeros / empty list on an empty signal (e.g., zero-record PLD).
    """
    if leak_signal.values.size == 0:
        return LeakStats(0.0, 0.0, 0.0, 0.0, 0.0, [])

    lmin = leak_signal_to_lmin(leak_signal)
    sample_rate = leak_signal.sample_rate_hz
    sample_period_s = 1.0 / sample_rate if sample_rate > 0 else 2.0

    median = float(np.median(lmin))
    p95 = float(np.percentile(lmin, 95))
    p995 = float(np.percentile(lmin, 99.5))

    over_redline_mask = lmin > LEAK_REDLINE_LMIN
    seconds_over = float(over_redline_mask.sum()) * sample_period_s
    minutes_over = seconds_over / 60.0
    total_seconds = float(lmin.size) * sample_period_s
    pct_over = (seconds_over / total_seconds * 100.0) if total_seconds > 0 else 0.0

    large_leak_events = _detect_large_leak_events(
        lmin,
        sample_period_s=sample_period_s,
        start_time=leak_signal.start_datetime,
        session_id=session_id,
    )

    return LeakStats(
        median_lmin=median,
        p95_lmin=p95,
        p995_lmin=p995,
        minutes_over_redline=minutes_over,
        large_leak_pct=pct_over,
        large_leak_events=large_leak_events,
    )


def _detect_large_leak_events(
    lmin: np.ndarray,
    *,
    sample_period_s: float,
    start_time: datetime,
    session_id: int | None,
) -> list[NightlyEvent]:
    """Walk the leak signal, emit a LargeLeak event for each sustained run.

    A run is `LARGE_LEAK_MIN_DURATION_S` or longer of consecutive samples
    exceeding `LEAK_REDLINE_LMIN`. Event timestamp is the start of the run;
    duration is the run length in seconds.
    """
    min_samples = max(1, int(np.ceil(LARGE_LEAK_MIN_DURATION_S / sample_period_s)))
    over = lmin > LEAK_REDLINE_LMIN
    if not over.any():
        return []

    # Find runs of True via diff-on-padded
    padded = np.concatenate(([False], over, [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]

    out: list[NightlyEvent] = []
    for s, e in zip(starts, ends):
        run_samples = e - s
        if run_samples < min_samples:
            continue
        run_seconds = float(run_samples) * sample_period_s
        ts = start_time + timedelta(seconds=float(s) * sample_period_s)
        out.append(
            NightlyEvent(
                date=ts.date(),
                timestamp=ts,
                session_id=session_id,
                event_type="LargeLeak",
                duration_seconds=run_seconds,
                leak_at_event=float(lmin[s]),
            )
        )
    return out
