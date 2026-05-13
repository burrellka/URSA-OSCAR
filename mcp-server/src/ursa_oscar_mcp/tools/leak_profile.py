"""get_leak_profile — leak percentiles + redline interpretation."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_leak_profile(date: str, end_date: str | None = None) -> dict:
    """Return leak statistics and time-over-redline for a night or range.

    Distinguishes mask-seal issues from mouth-opening. AirSense 11 redline is
    24 L/min. Sustained periods above redline (>10 seconds) become LargeLeak
    events. Persistent high large_leak_pct → fitting / strap issue; bursts →
    mouth opening during REM.

    Use when the user asks:
        "Are my masks leaking?"
        "How was my leak last night?"
        "Show me leak time over redline this week."

    Args:
        date: YYYY-MM-DD.
        end_date: Optional upper bound for range queries.

    Returns:
        {"ok": True, "data": {..., "interpretation": {"seal_quality": str, "notes": [str]}}}
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

    minutes_over = sum((r.get("minutes_over_leak_redline") or 0.0) for r in rows)
    pct = avg("large_leak_pct") or 0.0

    if pct < 2.0:
        seal_quality = "good"
        notes: list[str] = []
    elif pct < 10.0:
        seal_quality = "marginal"
        notes = ["Leak crossed redline for a notable portion of the night. Check mask seating; consider re-adjusting straps."]
    else:
        seal_quality = "poor"
        notes = ["Large-leak time exceeded 10% of mask-on time. Mask seal or mouth opening during sleep are the typical causes."]

    return _ok({
        "date_range": [start.isoformat(), end.isoformat()],
        "median_leak": avg("median_leak"),
        "p95_leak": avg("p95_leak"),
        "p995_leak": avg("p995_leak"),
        "minutes_over_redline": round(minutes_over, 1),
        "large_leak_pct": pct,
        "interpretation": {"seal_quality": seal_quality, "notes": notes},
    })
