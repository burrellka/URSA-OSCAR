"""compare_periods — Phase 3 Item 5A, Tier-2 MCP tool.

Thin proxy over GET /api/v1/analytics/compare-periods. Per ADR-003,
compute lives in the API container; this tool just shape-shifts the
parameters into a query string and wraps the response in the standard
envelope.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def compare_periods(
    period_a_start: str,
    period_a_end: str,
    period_b_start: str,
    period_b_end: str,
    metrics: list[str] | None = None,
) -> dict:
    """Compare CPAP metrics between two date ranges and report deltas.

    Use this tool when the user asks:
        "Compare my AHI this week vs last week."
        "Are things better or worse than last month?"
        "How did my numbers change after I started melatonin?"
        "Show me my CPAP performance in May vs April."

    The tool computes mean / median / min / max / std-dev for each metric
    in both periods, then percent-change between them, and applies a
    direction-aware interpretation: "substantial_improvement",
    "moderate_improvement", "stable", "moderate_worsening",
    "substantial_worsening". Lower-is-better metrics (AHI, leak,
    minutes-in-apnea) flip the sign automatically; pressure is treated
    as neutral and gets "_change" suffixes instead of "improvement".

    Args:
        period_a_start: YYYY-MM-DD, inclusive start of the first period.
        period_a_end:   YYYY-MM-DD, inclusive end of the first period.
        period_b_start: YYYY-MM-DD, inclusive start of the second period.
        period_b_end:   YYYY-MM-DD, inclusive end of the second period.
        metrics: Optional list of metric names to compare. Defaults to
            the standard AHI / pressure / leak / apnea / mask-on set.
            Metric names follow the same convention as the correlation
            tool — bare nightly_summary columns ("total_ahi",
            "p95_pressure") and manual-log metrics as
            "log_type:filter:field" ("medication:Melatonin:dose",
            "alertness::score").

    Returns:
        On success:
            {"ok": true, "data": {
                "period_a": {"start": "...", "end": "...", "n_nights": N},
                "period_b": {"start": "...", "end": "...", "n_nights": N},
                "metrics": {
                    "total_ahi": {
                        "period_a": {mean, median, n, ...},
                        "period_b": {mean, median, n, ...},
                        "absolute_delta": ...,
                        "relative_delta_pct": ...,
                        "interpretation": "substantial_improvement",
                    },
                    ...
                },
                "summary": "AHI down 37% (substantial improvement). ..."
            }}

    Caveats. Periods with < 3 nights produce noisy comparisons — the
    interpretation labels are best-effort and the agent should soften
    language ("appears to have improved" not "improved") when n is
    small. Insufficient-data metric responses are surfaced explicitly
    via the "insufficient_data" interpretation label.
    """
    for label, value in [
        ("period_a_start", period_a_start),
        ("period_a_end", period_a_end),
        ("period_b_start", period_b_start),
        ("period_b_end", period_b_end),
    ]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}; expected YYYY-MM-DD",
                        code="INVALID_INPUT")

    params: dict[str, str | list[str]] = {
        "period_a_start": period_a_start,
        "period_a_end": period_a_end,
        "period_b_start": period_b_start,
        "period_b_end": period_b_end,
    }
    if metrics:
        params["metrics"] = metrics

    try:
        return _ok(api_get("/api/v1/analytics/compare-periods", params=params))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
