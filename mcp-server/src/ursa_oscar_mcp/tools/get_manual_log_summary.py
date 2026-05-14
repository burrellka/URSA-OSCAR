"""get_manual_log_summary — Phase 3 Item 5D, Tier-2 MCP tool.

Thin proxy over GET /api/v1/analytics/manual-log-summary. Aggregates
manual_logs by type within a date window and returns a structured
per-type rollup.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_manual_log_summary(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    log_type: str | None = None,
) -> dict:
    """Aggregate the user's manual logs over a date or range.

    Use this tool when the user asks:
        "How many times did I take melatonin this month?"
        "What's my average alertness score lately?"
        "Have I had any headaches in May?"
        "What was my bedroom temperature like this week?"

    The tool returns a structured rollup per log_type. For
    medication / symptom, the rollup is name → count + mean
    dose/severity. For alertness, scalar mean / median + morning /
    midday / evening time-of-day bucket means. For sleep_environment,
    mean / min / max temperature plus per-noise / per-light counts.
    For freeform, count + sample titles.

    Args:
        date: Single date YYYY-MM-DD. When set, the date range is just
            that one day and start_date / end_date are ignored.
        start_date: YYYY-MM-DD, inclusive start. Required with
            end_date if `date` is omitted.
        end_date: YYYY-MM-DD, inclusive end. Required with start_date
            if `date` is omitted.
        log_type: Optional filter to one type (medication / symptom /
            alertness / sleep_environment / freeform). When omitted,
            all five types are rolled up.

    Returns:
        On success:
            {"ok": true, "data": {
                "date_range": {"start": "...", "end": "..."},
                "total_entries": N,
                "by_type": {
                    "medication": {"count": N, "by_name": {...},
                                   "avg_dose_per_med": {...}},
                    "symptom":    {"count": N, "by_name": {...},
                                   "avg_severity_per_symptom": {...}},
                    "alertness":  {"count": N, "mean_score": ...,
                                   "median_score": ...,
                                   "mean_score_morning": ..., ...},
                    "sleep_environment": {"count": N,
                                          "avg_temperature_c": ...,
                                          "noise_level_counts": {...}, ...},
                    "freeform":   {"count": N, "sample_titles": [...]},
                }
            }}
    """
    # Validate exactly one shape: either `date` alone or
    # `start_date` + `end_date`.
    if date is not None:
        if start_date is not None or end_date is not None:
            return _err("Pass either `date` OR (`start_date` + `end_date`), not both",
                        code="INVALID_INPUT")
        try:
            date_t.fromisoformat(date)
        except ValueError:
            return _err(f"Invalid date '{date}'", code="INVALID_INPUT")
        params: dict[str, str] = {"date": date}
    else:
        if start_date is None or end_date is None:
            return _err("Provide either `date` or both `start_date` + `end_date`",
                        code="INVALID_INPUT")
        for label, value in [("start_date", start_date), ("end_date", end_date)]:
            try:
                date_t.fromisoformat(value)
            except ValueError:
                return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")
        params = {"start_date": start_date, "end_date": end_date}

    if log_type is not None:
        if log_type not in {"medication", "symptom", "alertness",
                            "sleep_environment", "freeform"}:
            return _err(f"Invalid log_type '{log_type}'", code="INVALID_INPUT")
        params["log_type"] = log_type

    try:
        return _ok(api_get("/api/v1/analytics/manual-log-summary", params=params))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
