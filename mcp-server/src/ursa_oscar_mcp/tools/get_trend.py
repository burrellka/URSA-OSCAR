"""get_trend — Phase 3 Item 5C, Tier-2 MCP tool.

Thin proxy over GET /api/v1/analytics/trend. Fits a linear regression
on the daily values of a metric and reports slope, R², projection,
and a direction-aware interpretation label.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_trend(
    metric: str,
    start_date: str,
    end_date: str,
    projection_days: int = 30,
) -> dict:
    """Compute a linear trend for one metric over a date range.

    Use this tool when the user asks:
        "Is my AHI getting better over time?"
        "What's my trajectory on central apneas this year?"
        "Project my pressure 30 days out at the current rate."
        "Has my leak rate been trending up since I switched masks?"

    The tool fits a simple linear regression (slope * day_index +
    intercept) on the metric's daily values, returns slope-per-day, R²,
    the current value estimate at the regression line, a projection N
    days into the future, and an interpretation label.

    Interpretation label semantics:
        improving      — trend direction matches "better" for this metric
                         (AHI dropping, mask-on minutes rising)
        worsening      — opposite of improving
        changing       — slope is real but the metric is neutral (e.g.,
                         pressure has no "better" direction)
        no_clear_trend — R² < 0.10; day-to-day noise dominates
        insufficient_data — < 5 days of values in range

    Caveats:
        - Projection is a linear extrapolation. Long-horizon projections
          are usually wrong. Treat the 30-day projection as a "what if
          the current trend continued" thought experiment, NOT a forecast.
        - The R² value indicates fit quality. R² < 0.3 means the trend
          line explains less than 30% of the day-to-day variance — soften
          claims accordingly.
        - For metrics with a clinically-meaningful floor (e.g., AHI can't
          go below 0), the linear extrapolation may project negative
          values. Relay the projection as bounded ("approaching 0") not
          literal ("-29.5 next month").

    Args:
        metric: Metric name. Same convention as analyze_correlation —
            bare nightly_summary column ("total_ahi", "p95_pressure") or
            "log_type:filter:field" string for manual logs.
        start_date: YYYY-MM-DD, inclusive start.
        end_date: YYYY-MM-DD, inclusive end.
        projection_days: How far forward to project. Default 30,
            max 365.

    Returns:
        On success:
            {"ok": true, "data": {
                "metric": "...",
                "date_range": {"start": "...", "end": "..."},
                "n_nights": N,
                "slope_per_day": value,
                "intercept": value,
                "r_squared": value,
                "p_value": value,
                "current_value_estimate": value,
                "projection": {
                    "projection_days": N,
                    "projection_date": "YYYY-MM-DD",
                    "projected_value": value
                },
                "interpretation": "improving"|...,
                "interpretation_text": "..."
            }}
    """
    for label, value in [("start_date", start_date), ("end_date", end_date)]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")
    if not (1 <= projection_days <= 365):
        return _err("projection_days must be between 1 and 365", code="INVALID_INPUT")

    try:
        return _ok(api_get("/api/v1/analytics/trend", params={
            "metric": metric,
            "start_date": start_date,
            "end_date": end_date,
            "projection_days": projection_days,
        }))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
