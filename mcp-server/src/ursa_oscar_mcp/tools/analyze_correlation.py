"""analyze_correlation — Phase 3 Item 5B, Tier-2 MCP tool.

Thin proxy over GET /api/v1/analytics/correlation. Pearson r + p-value
between two metrics with optional lag-days shift on metric_b.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def analyze_correlation(
    metric_a: str,
    metric_b: str,
    start_date: str,
    end_date: str,
    lag_days: int = 0,
) -> dict:
    """Compute Pearson correlation between two metrics over a date range.

    Use this tool when the user asks:
        "Is melatonin correlated with my AHI?"
        "Do colder bedrooms give me better sleep?"
        "Does my AHI go down 2 days after I take more medication?"
        "Is there a relationship between morning alertness and last night's AHI?"

    Critically: correlation IS NOT CAUSATION. The tool reports the
    statistical relationship and a sample-size warning when n < 30. Soften
    language accordingly when relaying to the user — "associated with"
    not "caused by", and surface the warning when sample size is small.

    Metric naming convention:

      Bare nightly_summary column — e.g., "total_ahi", "obstructive_ahi",
      "central_ahi", "hypopnea_index", "rera_index", "p95_pressure",
      "median_pressure", "p995_pressure", "p95_leak", "median_leak",
      "minutes_in_apnea", "large_leak_pct", "total_time_minutes".

      Manual-log metric — "log_type:filter:field":
          "medication:Melatonin:dose"       Melatonin dose per day
          "medication:Doxepin"              Doxepin (default field=dose)
          "symptom:headache:severity"       Headache severity per day
          "symptom:fatigue"                 Fatigue (default field=severity)
          "alertness::score"                Alertness mean per day
          "alertness:morning:score"         Morning alertness only
          "alertness:evening:score"         Evening alertness only
          "sleep_environment::temperature_c" Daily mean bedroom temp

    Args:
        metric_a: First metric. Bare nightly column or
            "log_type:filter:field" string. See examples above.
        metric_b: Second metric. Same naming. The lag offset (below)
            applies to metric_b.
        start_date: YYYY-MM-DD, inclusive start of the analysis window.
        end_date: YYYY-MM-DD, inclusive end.
        lag_days: Integer shift applied to metric_b. Default 0 (same-day
            correlation). Positive values pair metric_a[day t] with
            metric_b[day t + lag_days] — useful for "does this thing
            today predict that thing N days later?"-shaped questions.
            Range: -30 to +30.

    Returns:
        On success:
            {"ok": true, "data": {
                "metric_a": "...",
                "metric_b": "...",
                "date_range": {"start": "...", "end": "..."},
                "lag_days": N,
                "n_pairs": N,
                "pearson_r": r,
                "p_value": p,
                "interpretation": "weak_negative"|"moderate_positive"|...,
                "interpretation_text": "Moderate negative correlation ... r=-0.42, p=0.034, n=26.",
                "sample_size_warning": null | "n < 30 — interpret with caution"
            }}

        On insufficient data:
            {"ok": true, "data": {..., "interpretation": "insufficient_data", ...}}

    The interpretation field is a machine label following the schema:
        negligible | weak_{positive,negative} | moderate_{...} |
        strong_{...} | very_strong_{...} | no_variance | insufficient_data
    """
    for label, value in [("start_date", start_date), ("end_date", end_date)]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")
    if not (-30 <= lag_days <= 30):
        return _err("lag_days must be between -30 and 30", code="INVALID_INPUT")

    try:
        return _ok(api_get("/api/v1/analytics/correlation", params={
            "metric_a": metric_a,
            "metric_b": metric_b,
            "start_date": start_date,
            "end_date": end_date,
            "lag_days": lag_days,
        }))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
