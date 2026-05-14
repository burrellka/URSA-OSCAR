"""Linear-trend analysis — Phase 3 Item 5C.

Fits a simple linear regression y = slope * day_index + intercept on
the daily values of a single metric, returns slope/day, R², trend
direction interpretation, and a 30-day-out projection.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta
from typing import Any

import numpy as np
from scipy import stats

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric

# Metrics where "lower is better" — same set as compare_periods.
_LOWER_IS_BETTER = frozenset({
    "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index",
    "rera_index", "minutes_in_apnea", "minutes_over_leak_redline",
    "large_leak_pct", "median_leak", "p95_leak", "p995_leak",
    "cheyne_stokes_pct",
})


def compute_trend(
    db: DuckDBManager,
    metric: str,
    start: date_t,
    end: date_t,
    projection_days: int = 30,
) -> dict[str, Any]:
    series = resolve_metric(db, metric, start, end).dropna()
    n = int(len(series))

    if n < 5:
        return {
            "metric": metric,
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "n_nights": n,
            "slope_per_day": None,
            "intercept": None,
            "r_squared": None,
            "p_value": None,
            "current_value_estimate": None,
            "projection": None,
            "interpretation": "insufficient_data",
            "interpretation_text": (
                f"Need at least 5 days of data to fit a trend; got {n}. "
                f"Either widen the date range or wait for more nights to "
                f"accumulate."
            ),
        }

    # x = days since start.
    days_since_start = np.array(
        [(d - start).days for d in series.index], dtype=float
    )
    values = series.to_numpy(dtype=float)

    # scipy.stats.linregress returns slope, intercept, rvalue, pvalue, stderr.
    res = stats.linregress(days_since_start, values)
    slope = float(res.slope)
    intercept = float(res.intercept)
    r_squared = float(res.rvalue) ** 2
    p_value = float(res.pvalue)

    last_day_index = days_since_start[-1]
    current_estimate = float(intercept + slope * last_day_index)
    proj_day_index = last_day_index + projection_days
    projection_value = float(intercept + slope * proj_day_index)
    projection_date = (series.index[-1] + timedelta(days=projection_days)).isoformat()

    interpretation, text = _interpret_trend(
        metric, slope, r_squared, p_value, n,
        current_estimate, projection_value, projection_days,
    )

    return {
        "metric": metric,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "n_nights": n,
        "slope_per_day": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "p_value": p_value,
        "current_value_estimate": current_estimate,
        "projection": {
            "projection_days": projection_days,
            "projection_date": projection_date,
            "projected_value": projection_value,
        },
        "interpretation": interpretation,
        "interpretation_text": text,
    }


def _interpret_trend(
    metric: str,
    slope: float,
    r_squared: float,
    p_value: float,
    n: int,
    current_estimate: float,
    projection_value: float,
    projection_days: int,
) -> tuple[str, str]:
    if r_squared < 0.1:
        return (
            "no_clear_trend",
            f"{metric} shows no clear linear trend (R²={r_squared:.2f}). "
            f"Day-to-day noise dominates over any directional change.",
        )

    direction = "down" if slope < 0 else "up"
    good = (
        (metric in _LOWER_IS_BETTER and slope < 0)
        or (metric not in _LOWER_IS_BETTER and slope > 0
            and metric in {"total_time_minutes", "session_count"})
    )
    descriptor = "improving" if good else "worsening" if metric in _LOWER_IS_BETTER or metric in {"total_time_minutes", "session_count"} else "changing"

    text = (
        f"{metric} is trending {direction} at {slope:+.4f}/day "
        f"(R²={r_squared:.2f}, p={p_value:.3f}, n={n}). "
        f"Current estimated value: {current_estimate:.2f}. "
        f"Projected in {projection_days} days: {projection_value:.2f}."
    )
    if descriptor != "changing":
        text += f" Direction: {descriptor}."

    return descriptor, text
