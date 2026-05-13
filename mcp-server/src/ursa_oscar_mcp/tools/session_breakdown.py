"""get_session_breakdown — per-session details within one night."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_session_breakdown(date: str) -> dict:
    """Return per-session details for nights with multiple mask-on periods.

    Useful when a night was interrupted (mask off / bathroom break /
    readjust) and the patient wants to know which session had the apnea
    cluster.

    Use when the user asks:
        "Was the bad AHI in the first or second session?"
        "Break down last night's sessions."

    Args:
        date: YYYY-MM-DD.

    Returns:
        {"ok": True, "data": {"date": str, "sessions": [{"session_id": int,
            "first_event": iso | null, "last_event": iso | null,
            "event_counts": {event_type: int}, "total_events": int}, ...]}}
    """
    try:
        target = date_t.fromisoformat(date)
    except ValueError:
        return _err(f"Invalid date '{date}'", code="INVALID_INPUT")

    try:
        evs = api_get("/api/v1/events", params={"date": target.isoformat()})
    except httpx.HTTPStatusError as e:
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
    if not evs:
        return _err(f"No events for {target}", code="NOT_FOUND")

    per_session: dict[int, dict] = {}
    for ev in evs:
        sid = int(ev.get("session_id") or 0)
        entry = per_session.setdefault(sid, {
            "session_id": sid, "first_event": None, "last_event": None,
            "event_counts": {}, "total_events": 0,
        })
        etype = ev.get("event_type", "?")
        entry["event_counts"][etype] = entry["event_counts"].get(etype, 0) + 1
        entry["total_events"] += 1
        ts = ev.get("timestamp")
        if ts:
            if entry["first_event"] is None or ts < entry["first_event"]:
                entry["first_event"] = ts
            if entry["last_event"] is None or ts > entry["last_event"]:
                entry["last_event"] = ts

    sessions = [per_session[sid] for sid in sorted(per_session)]
    return _ok({"date": target.isoformat(), "sessions": sessions})
