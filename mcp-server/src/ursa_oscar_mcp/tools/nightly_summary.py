"""get_nightly_summary — single-night or range nightly summary tool."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_nightly_summary(date: str, end_date: str | None = None) -> dict:
    """Return the full nightly summary record(s) for a CPAP night or date range.

    The nightly summary captures everything OSCAR's Daily View shows in
    aggregate form: AHI broken into obstructive / central / hypopnea / RERA
    components, pressure percentiles (median / 95% / 99.5%), EPAP equivalents,
    leak statistics, time-in-apnea, equipment settings, and session count.

    Use this tool when the user asks:
        "How was last night?"
        "Show me my CPAP data for May 10."
        "What's my AHI been this week?"
        "Compare last night to the night before."

    Args:
        date: Single date (YYYY-MM-DD). If `end_date` is omitted, this is the
            only night returned.
        end_date: Optional upper bound (inclusive). When set, returns all
            nights from `date` through `end_date`.

    Returns:
        {"ok": True, "data": {...summary...}} for single-date queries, or
        {"ok": True, "data": [{...}, {...}]} for range queries.
        {"ok": False, "error": "...", "code": "NOT_FOUND"} if the requested
        night isn't in the database.
    """
    try:
        date_t.fromisoformat(date)
    except ValueError:
        return _err(f"Invalid date '{date}', expected YYYY-MM-DD", code="INVALID_INPUT")
    if end_date is not None:
        try:
            if date_t.fromisoformat(end_date) < date_t.fromisoformat(date):
                return _err("end_date must be on or after date", code="INVALID_INPUT")
        except ValueError:
            return _err(f"Invalid end_date '{end_date}'", code="INVALID_INPUT")

    try:
        if end_date is None:
            return _ok(api_get(f"/api/v1/night/{date}"))
        return _ok(api_get("/api/v1/nights", params={"start": date, "end": end_date}))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return _err(f"No nightly data for {date}", code="NOT_FOUND")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
