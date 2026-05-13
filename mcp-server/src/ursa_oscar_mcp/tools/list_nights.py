"""list_available_nights — calendar / summary listing."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def list_available_nights(
    start_date: str | None = None,
    end_date: str | None = None,
    filter_expression: str | None = None,
) -> dict:
    """List nights with CPAP data, optionally filtered.

    Returns one entry per available night with the most-used summary stats
    (AHI, session count, total time). Useful for "what nights do I have data
    for?" or for building a calendar / heatmap view.

    The `filter_expression` argument supports a small subset of SQL-like
    predicates: `AHI < 5`, `AHI > 10`, `session_count >= 2`. Anything more
    complex should use the `run_sql_query` Tier-3 escape hatch.

    Use when the user asks:
        "What nights do I have CPAP data for?"
        "Show me all nights with AHI under 5."
        "When did my CPAP last record a night?"

    Args:
        start_date: Optional lower bound (YYYY-MM-DD).
        end_date: Optional upper bound (YYYY-MM-DD).
        filter_expression: Optional simple predicate. Supported keys:
            AHI, session_count. Operators: <, <=, >, >=, =, !=.

    Returns:
        {"ok": True, "data": {"nights": [{"date": "YYYY-MM-DD", "ahi": float,
            "session_count": int, "total_time_minutes": int}, ...]}}
    """
    for label, val in [("start_date", start_date), ("end_date", end_date)]:
        if val is not None:
            try:
                date_t.fromisoformat(val)
            except ValueError:
                return _err(f"Invalid {label} '{val}'", code="INVALID_INPUT")

    parsed = _parse_filter(filter_expression) if filter_expression else None
    if filter_expression is not None and parsed is None:
        return _err(
            f"Could not parse filter '{filter_expression}'. "
            "Supported: 'AHI < 5', 'session_count >= 2', etc.",
            code="INVALID_INPUT",
        )

    params: dict[str, str] = {}
    if start_date:
        params["start"] = start_date
    if end_date:
        params["end"] = end_date

    try:
        rows = api_get("/api/v1/nights", params=params or None)
    except httpx.HTTPStatusError as e:
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")

    if parsed is not None:
        col, op, val = parsed
        rows = [r for r in rows if _matches(r, col, op, val)]

    nights = [
        {
            "date": r.get("date"),
            "ahi": round(r["total_ahi"], 3) if r.get("total_ahi") is not None else None,
            "session_count": r.get("session_count"),
            "total_time_minutes": r.get("total_time_minutes"),
        }
        for r in rows
    ]
    return _ok({"nights": nights})


_ALLOWED_COLUMNS = {"AHI": "total_ahi", "session_count": "session_count"}
_ALLOWED_OPS = {"<", "<=", ">", ">=", "=", "!="}


def _parse_filter(expr: str) -> tuple[str, str, float] | None:
    tokens = expr.replace("==", "=").split()
    if len(tokens) != 3:
        return None
    col_raw, op, val_raw = tokens
    col = _ALLOWED_COLUMNS.get(col_raw)
    if col is None or op not in _ALLOWED_OPS:
        return None
    try:
        val = float(val_raw)
    except ValueError:
        return None
    return (col, op, val)


def _matches(row: dict, col: str, op: str, val: float) -> bool:
    cell = row.get(col)
    if cell is None:
        return False
    cmp = {
        "<":  lambda a, b: a <  b,
        "<=": lambda a, b: a <= b,
        ">":  lambda a, b: a >  b,
        ">=": lambda a, b: a >= b,
        "=":  lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }
    return cmp[op](cell, val)
