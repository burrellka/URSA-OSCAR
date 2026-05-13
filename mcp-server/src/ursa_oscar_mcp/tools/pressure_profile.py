"""get_pressure_profile — pressure percentiles + ceiling-hit flag."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_pressure_profile(date: str, end_date: str | None = None) -> dict:
    """Return median / 95% / 99.5% pressure for a night or date range.

    Includes a `ceiling_hit` flag (machine reached its max prescribed
    pressure) and a recommendation field. Use for assessing CPAP titration:
    if 95% pressure is bumping against max_pressure_setting and AHI is still
    elevated, the prescribed range needs review.

    Use when the user asks:
        "What pressure am I averaging?"
        "Is my CPAP pressure maxing out?"
        "Show me last week's pressure trend."

    Args:
        date: YYYY-MM-DD.
        end_date: Optional upper bound for range queries.

    Returns:
        {"ok": True, "data": {..., "ceiling_hit": bool, "recommendation": str | None}}
    """
    try:
        start = date_t.fromisoformat(date)
    except ValueError:
        return _err(f"Invalid date '{date}'", code="INVALID_INPUT")
    end = start
    if end_date is not None:
        try:
            end = date_t.fromisoformat(end_date)
        except ValueError:
            return _err(f"Invalid end_date '{end_date}'", code="INVALID_INPUT")
        if end < start:
            return _err("end_date must be on or after date", code="INVALID_INPUT")

    try:
        rows = api_get("/api/v1/nights", params={"start": start.isoformat(), "end": end.isoformat()})
    except httpx.HTTPStatusError as e:
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
    if not rows:
        return _err(f"No nightly data for {start}..{end}", code="NOT_FOUND")

    def avg(field: str) -> float | None:
        vals = [r[field] for r in rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    p95 = avg("p95_pressure")
    max_setting = next((r.get("max_pressure_setting") for r in rows if r.get("max_pressure_setting") is not None), None)
    ceiling_hit = bool(p95 is not None and max_setting is not None and p95 >= max_setting * 0.97)
    recommendation = None
    if ceiling_hit:
        recommendation = (
            "95% pressure is at or near the prescribed maximum. If AHI remains "
            "elevated, consider asking your sleep doctor about widening the range."
        )

    return _ok({
        "date_range": [start.isoformat(), end.isoformat()],
        "median_pressure": avg("median_pressure"),
        "p95_pressure": p95,
        "p995_pressure": avg("p995_pressure"),
        "median_epap": avg("median_epap"),
        "p95_epap": avg("p95_epap"),
        "p995_epap": avg("p995_epap"),
        "max_pressure_setting": max_setting,
        "ceiling_hit": ceiling_hit,
        "recommendation": recommendation,
    })
