"""Safe-projection guardrails — Phase 0.13.5.

Background: ``analytics/trend.py`` fits a linear regression to a
metric's daily values and extrapolates a projection_days-out value
via ``intercept + slope * (last_x + projection_days)``. Without
constraints this produces clinically nonsensical projections like
"Projected in 30 days: -23.72 AHI" — a result the operator saw in
PDF review after Ticket 6.3 and which prompted this patch.

Two guards live in this module:

  1. **Sample-size rule**: don't project further than 4× your sample
     size. Formally: ``n_samples >= projection_days * 0.25``. With 30
     days of projection, you need at least 8 observed nights; with
     60 days, 15. Below the threshold, the projection is suppressed
     entirely (returned as None) and the operator sees a suppression
     reason instead of a value.

  2. **Physical bounds**: every nightly metric has a realistic
     physical range (AHI can't be negative; CPAP pressure can't
     exceed the device's hardware ceiling; mask-on minutes can't
     exceed a night). When the regression line projects outside the
     metric's bounds, the value is clamped AND flagged. The operator
     sees the clamped value alongside a ``raw_projected_value`` and
     ``clamped: true`` so they understand what happened.

The 25% rule + bounds clamp together handle the two failure modes
that produced the "-23.72 AHI" output:
  - Steep negative slope from a short window of improving nights
  - Linear extrapolation that doesn't know AHI ≥ 0

This module is pure compute — no I/O, no DB. Tested in isolation
with constructed slopes / intercepts; the trend endpoint integrates
the result into its existing ``projection`` block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# METRIC_BOUNDS registry
#
# (lower, upper) tuples in the metric's native unit. Values outside
# these bounds are clamped in safe_projection.
#
# Sources:
#   - AHI metrics: AASM diagnostic categories cap "severe OSA" at 30+
#     events/hour, but the underlying event count is unbounded. 100
#     is a defensive upper bound — clinically severe + practically
#     plausible. Below 0 is impossible (events can't be negative).
#   - Pressure metrics: ResMed AirSense 10/11 device range is 4-20
#     cmH2O. We use [4, 25] as a soft ceiling to allow other devices
#     with slightly different ranges.
#   - Leak metrics: median typically <30 L/min, p95 <40, p995 can
#     spike to 60+ during mask-off events. Above 100 is implausible
#     even on the worst-leak nights.
#   - Time-based metrics: minutes per night, capped at 720 (12 hours
#     of mask-on time is the practical maximum).
#   - large_leak_pct: 0-100 by definition.
#   - session_count: typically 1-3, occasionally up to 10 on heavily-
#     interrupted nights.
# ---------------------------------------------------------------------------

METRIC_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    # AHI family
    "total_ahi": (0.0, 100.0),
    "obstructive_ahi": (0.0, 100.0),
    "central_ahi": (0.0, 100.0),
    "hypopnea_index": (0.0, 100.0),
    "rera_index": (0.0, 100.0),
    # Pressure family (cmH2O)
    "median_pressure": (4.0, 25.0),
    "p95_pressure": (4.0, 25.0),
    "p995_pressure": (4.0, 25.0),
    "median_epap": (4.0, 25.0),
    "p95_epap": (4.0, 25.0),
    "p995_epap": (4.0, 25.0),
    # Leak family (L/min)
    "median_leak": (0.0, 60.0),
    "p95_leak": (0.0, 80.0),
    "p995_leak": (0.0, 100.0),
    # Time/duration metrics
    "minutes_in_apnea": (0.0, 600.0),
    "minutes_over_leak_redline": (0.0, 600.0),
    "total_time_minutes": (0.0, 720.0),
    # Percentage metrics
    "large_leak_pct": (0.0, 100.0),
    # Counts
    "session_count": (0.0, 10.0),
}


# Sample-size rule: the projection_days you're extrapolating must be
# at most 4× your observed window. Equivalent to n_samples >=
# projection_days * 0.25. Below this ratio, projections are
# statistically meaningless even when the slope is significant.
SAMPLE_TO_PROJECTION_RATIO: Final[float] = 0.25

# Always require at least 5 samples regardless of projection_days —
# matches the existing trend.py early-return threshold so the two
# guards compose cleanly.
ABSOLUTE_MIN_SAMPLES: Final[int] = 5


@dataclass(frozen=True)
class SafeProjection:
    """Result of a guarded projection.

    Fields:
      projected_value      — final value the operator sees. None when
                             suppressed by the sample-size rule;
                             otherwise the raw value (possibly clamped).
      raw_projected_value  — what the bare regression line would have
                             produced before guards. Useful for
                             debugging and for explaining "why is the
                             projection capped at X."
      clamped              — True when the raw value was outside the
                             metric's physical bounds.
      bounds_used          — the (lo, hi) tuple from METRIC_BOUNDS, or
                             None when the metric isn't registered (in
                             which case no clamping happens).
      suppressed_reason    — a short machine-readable code when the
                             projection was suppressed (None when the
                             value is usable). One of:
                               'insufficient_samples'
                               'clamped_to_lower_bound'
                               'clamped_to_upper_bound'
      explanation          — operator-facing prose, suitable to render
                             alongside the value in the UI / PDF.
    """
    projected_value: float | None
    raw_projected_value: float
    clamped: bool
    bounds_used: tuple[float, float] | None
    suppressed_reason: str | None
    explanation: str | None


def safe_projection(
    metric: str,
    slope: float,
    intercept: float,
    last_x: float,
    projection_days: int,
    n_samples: int,
) -> SafeProjection:
    """Compute the regression projection for ``last_x + projection_days``
    days out, applying the sample-size + bounds guards.

    Arguments:
      metric           — name as it appears in METRIC_BOUNDS (or any
                         string; unknown metrics simply skip the
                         bounds clamp).
      slope            — regression slope (units per day).
      intercept        — regression intercept (units).
      last_x           — the most recent sample's x-coordinate, where
                         x is "days since the regression's start
                         date." For a window of N consecutive
                         observed days, last_x == N-1.
      projection_days  — how far forward to extrapolate (positive int).
      n_samples        — count of observed nights used to fit the
                         regression.

    Returns a SafeProjection dataclass — see its docstring for fields.
    """
    raw = float(intercept + slope * (last_x + projection_days))
    bounds = METRIC_BOUNDS.get(metric)

    # ---- Guard 1: sample-size rule ----
    min_required = max(
        ABSOLUTE_MIN_SAMPLES,
        int(projection_days * SAMPLE_TO_PROJECTION_RATIO),
    )
    if n_samples < min_required:
        return SafeProjection(
            projected_value=None,
            raw_projected_value=raw,
            clamped=False,
            bounds_used=bounds,
            suppressed_reason="insufficient_samples",
            explanation=(
                f"Projection suppressed: need at least {min_required} "
                f"observed nights to project {projection_days} days "
                f"forward, got {n_samples}. Either wait for more data "
                f"or shorten the projection horizon."
            ),
        )

    # ---- Guard 2: physical bounds ----
    if bounds is None:
        # Unregistered metric — return the raw projection unguarded.
        # Caller is responsible for handling this case (or extending
        # METRIC_BOUNDS).
        return SafeProjection(
            projected_value=raw,
            raw_projected_value=raw,
            clamped=False,
            bounds_used=None,
            suppressed_reason=None,
            explanation=None,
        )

    lo, hi = bounds
    if raw < lo:
        return SafeProjection(
            projected_value=lo,
            raw_projected_value=raw,
            clamped=True,
            bounds_used=bounds,
            suppressed_reason="clamped_to_lower_bound",
            explanation=(
                f"Raw projection {raw:.2f} is below the physical floor "
                f"for {metric} ({lo}). Linear extrapolation can produce "
                f"clinically impossible values for short, steeply-"
                f"trending windows. Clamped to {lo}; treat as a 'floor "
                f"reached, no further improvement projected' signal."
            ),
        )
    if raw > hi:
        return SafeProjection(
            projected_value=hi,
            raw_projected_value=raw,
            clamped=True,
            bounds_used=bounds,
            suppressed_reason="clamped_to_upper_bound",
            explanation=(
                f"Raw projection {raw:.2f} exceeds the physical ceiling "
                f"for {metric} ({hi}). Likely an artifact of fitting a "
                f"line to a few high-variance nights. Clamped to {hi}; "
                f"either widen the window or treat this projection as "
                f"speculative."
            ),
        )

    # In-bounds: return the unguarded value.
    return SafeProjection(
        projected_value=raw,
        raw_projected_value=raw,
        clamped=False,
        bounds_used=bounds,
        suppressed_reason=None,
        explanation=None,
    )
