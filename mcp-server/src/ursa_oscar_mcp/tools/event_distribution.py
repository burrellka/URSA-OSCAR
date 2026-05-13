"""get_event_distribution_by_hour — hourly histogram for one night."""
from __future__ import annotations

from datetime import date as date_t, datetime

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_event_distribution_by_hour(
    date: str,
    event_types: list[str] | None = None,
) -> dict:
    """Return per-hour event counts for ONE night, optionally filtered by type.

    Reveals time-of-night patterns that bare totals hide. Useful for catching:
    - Central apnea clusters in the 2-4 AM REM window (TECSA fingerprint)
    - Position-dependent obstructive runs at session start
    - End-of-night events tied to mask drift or leak excursions

    Single-date only — hour-of-night patterns don't aggregate meaningfully
    across nights.

    Use when the user asks:
        "When in the night do my centrals cluster?"
        "Show me last night's events by hour."

    Args:
        date: YYYY-MM-DD.
        event_types: Optional list. Restrict to e.g. ["ClearAirway"] for
            central-only views. Omit for all event types.

    Returns:
        {"ok": True, "data": {
            "date": "YYYY-MM-DD",
            "hours": [{"hour": int (0-23), "counts": {event_type: int}}, ...]
        }}
    """
    try:
        target = date_t.fromisoformat(date)
    except ValueError:
        return _err(f"Invalid date '{date}'", code="INVALID_INPUT")

    try:
        params: dict = {"date": target.isoformat()}
        if event_types:
            params["event_type"] = event_types
        evs = api_get("/api/v1/events", params=params)
    except httpx.HTTPStatusError as e:
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")

    if not evs:
        return _err(f"No events for {target}", code="NOT_FOUND")

    by_hour: dict[int, dict[str, int]] = {}
    for ev in evs:
        ts = ev.get("timestamp")
        if not ts:
            continue
        try:
            hr = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
        except ValueError:
            continue
        etype = ev.get("event_type", "?")
        by_hour.setdefault(hr, {})[etype] = by_hour.setdefault(hr, {}).get(etype, 0) + 1

    return _ok({
        "date": target.isoformat(),
        "hours": [{"hour": hr, "counts": by_hour[hr]} for hr in sorted(by_hour)],
    })
