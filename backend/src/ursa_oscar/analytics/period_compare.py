"""Period-comparison math + interpretation strings.

Phase 3 Item 5A — ``/api/v1/analytics/compare-periods``.
"""
from __future__ import annotations

from datetime import date as date_t
from typing import Any

import numpy as np
import pandas as pd

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric
from .usage_rate import compute_usage_breakdown


# Metrics where "lower is better" — informs the improvement framing.
_LOWER_IS_BETTER = frozenset({
    "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index",
    "rera_index", "minutes_in_apnea", "minutes_over_leak_redline",
    "large_leak_pct", "median_leak", "p95_leak", "p995_leak",
    "cheyne_stokes_pct",
})

# Metrics where "higher is better."
_HIGHER_IS_BETTER = frozenset({
    "total_time_minutes",  # more mask-on time
    "session_count",
})


# Default metric set when caller doesn't specify.
_DEFAULT_COMPARE_METRICS = (
    "total_ahi",
    "obstructive_ahi",
    "central_ahi",
    "hypopnea_index",
    "p95_pressure",
    "p95_leak",
    "minutes_in_apnea",
    "total_time_minutes",
)


def compare_periods(
    db: DuckDBManager,
    period_a_start: date_t,
    period_a_end: date_t,
    period_b_start: date_t,
    period_b_end: date_t,
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Compute mean/median + percent-change for each metric between two
    date ranges. Returns the wire-shape envelope dict.
    """
    metrics = list(metrics) if metrics else list(_DEFAULT_COMPARE_METRICS)

    result_metrics: dict[str, Any] = {}
    improvements = 0
    worsenings = 0

    for m in metrics:
        a = resolve_metric(db, m, period_a_start, period_a_end).dropna()
        b = resolve_metric(db, m, period_b_start, period_b_end).dropna()

        a_summary = _stats(a)
        b_summary = _stats(b)
        a_mean = a_summary["mean"]
        b_mean = b_summary["mean"]

        if a_mean is None or b_mean is None:
            interpretation = "insufficient_data"
            absolute = None
            relative_pct = None
        else:
            absolute = float(b_mean - a_mean)
            # Guard against zero-base; report None rather than infinity.
            if a_mean == 0:
                relative_pct = None
            else:
                relative_pct = float((b_mean - a_mean) / abs(a_mean) * 100.0)
            interpretation = _classify_change(m, absolute, relative_pct)
            if "improvement" in interpretation:
                improvements += 1
            elif "worsening" in interpretation:
                worsenings += 1

        result_metrics[m] = {
            "period_a": a_summary,
            "period_b": b_summary,
            "absolute_delta": absolute,
            "relative_delta_pct": relative_pct,
            "interpretation": interpretation,
        }

    # 0.13.4 — per-period usage breakdown so the UI can surface
    # "X used / Y skipped" alongside the clinical metrics.
    usage_a = compute_usage_breakdown(db, period_a_start, period_a_end)
    usage_b = compute_usage_breakdown(db, period_b_start, period_b_end)

    return {
        "period_a": {
            "start": period_a_start.isoformat(),
            "end": period_a_end.isoformat(),
            "n_nights": int(_first_present(result_metrics, "period_a", "n") or 0),
            **usage_a,
        },
        "period_b": {
            "start": period_b_start.isoformat(),
            "end": period_b_end.isoformat(),
            "n_nights": int(_first_present(result_metrics, "period_b", "n") or 0),
            **usage_b,
        },
        "metrics": result_metrics,
        "summary": _build_summary(result_metrics, improvements, worsenings),
    }


def _stats(series: pd.Series) -> dict[str, Any]:
    if len(series) == 0:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "min": None, "max": None}
    return {
        "n": int(len(series)),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "std": float(series.std()) if len(series) > 1 else 0.0,
        "min": float(series.min()),
        "max": float(series.max()),
    }


def _classify_change(metric: str, absolute: float, relative_pct: float | None) -> str:
    """Return one of:
    substantial_improvement / moderate_improvement / stable /
    moderate_worsening / substantial_worsening / changed (neutral).

    Uses relative_pct thresholds; falls back to absolute when relative
    is undefined (a_mean == 0).
    """
    if relative_pct is None:
        # Use absolute change with looser thresholds for "low base" case.
        if abs(absolute) < 0.5:
            return "stable"
        direction = "improvement" if _is_good_change(metric, absolute) else "worsening"
        return f"moderate_{direction}"

    abs_r = abs(relative_pct)
    if abs_r < 10:
        return "stable"
    if abs_r < 25:
        prefix = "moderate"
    else:
        prefix = "substantial"

    if metric in _LOWER_IS_BETTER or metric in _HIGHER_IS_BETTER:
        good = _is_good_change(metric, absolute)
        suffix = "improvement" if good else "worsening"
        return f"{prefix}_{suffix}"

    # Neutral metric (e.g., pressure). Return "moderate_change" / "substantial_change".
    return f"{prefix}_change"


def _is_good_change(metric: str, absolute_delta: float) -> bool:
    if metric in _LOWER_IS_BETTER:
        return absolute_delta < 0
    if metric in _HIGHER_IS_BETTER:
        return absolute_delta > 0
    return False  # Neutral metrics never count as "good"; caller uses _change suffix


def _first_present(metrics: dict[str, Any], side: str, key: str):
    for v in metrics.values():
        s = v.get(side, {})
        if s.get(key) is not None:
            return s[key]
    return None


def _build_summary(metrics: dict[str, Any], improvements: int, worsenings: int) -> str:
    headline = next(iter(metrics.items()), None)
    if not headline:
        return "No metrics computed."
    metric, payload = headline
    interp = payload["interpretation"]
    relative = payload["relative_delta_pct"]
    if relative is None:
        body = f"{metric}: insufficient data for percent change."
    else:
        direction = "down" if relative < 0 else "up"
        body = f"{metric} {direction} {abs(relative):.1f}% ({interp.replace('_', ' ')})."
    if improvements and worsenings:
        tail = f" {improvements} metric(s) improved, {worsenings} worsened."
    elif improvements:
        tail = f" {improvements} metric(s) improved overall."
    elif worsenings:
        tail = f" {worsenings} metric(s) worsened overall."
    else:
        tail = ""
    return body + tail
