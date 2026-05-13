"""get_ahi_breakdown — AHI decomposition with clinical interpretation."""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_ahi_breakdown(date: str, end_date: str | None = None) -> dict:
    """AHI broken down by event type with interpretation hints.

    Critical for the URSA use case: distinguishing CPAP efficacy (obstructive
    well-controlled?) from TECSA / treatment-emergent central apnea
    (centrals dominating? clustering?). The bare AHI number hides this; the
    breakdown surfaces it.

    Returns counts, per-event-type indices (events/hour of the same type),
    percent-of-total split, and an interpretation block flagging:
    - obstructive_treatment_status: well_controlled / partial / inadequate
    - central_apnea_concern: none / mild / elevated / significant
    - tecsa_likely: true when centrals >50% of total AHI on a >=5 AHI night

    Use when the user asks:
        "Were my apneas mostly central or obstructive last night?"
        "Is my CPAP working — is the obstructive AHI controlled?"
        "How much of my AHI is TECSA?"
        "Break down last night's AHI."

    Args:
        date: YYYY-MM-DD.
        end_date: Optional upper bound for range queries.

    Returns:
        {"ok": True, "data": {
            "total_ahi": float,
            "obstructive": {"count": int, "ahi": float, "pct_of_total": float},
            "central":     {"count": int, "ahi": float, "pct_of_total": float},
            "hypopnea":    {"count": int, "ahi": float, "pct_of_total": float},
            "apnea":       {"count": int, "ahi": float, "pct_of_total": float},
            "rera":        {"count": int, "rdi_contribution": float},
            "interpretation": {...}
        }}
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
        nights = api_get(
            "/api/v1/nights",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        if not nights:
            return _err(f"No nightly data for {start}..{end}", code="NOT_FOUND")

        # Accumulate event counts across the date range
        counts: dict[str, int] = {}
        total_minutes = 0
        for night in nights:
            total_minutes += int(night.get("total_time_minutes") or 0)
            evs = api_get("/api/v1/events", params={"date": night["date"]})
            for ev in evs:
                t = ev["event_type"]
                counts[t] = counts.get(t, 0) + 1
    except httpx.HTTPStatusError as e:
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")

    total_hours = total_minutes / 60.0 if total_minutes else 0.0

    def block(event_type: str) -> dict:
        c = counts.get(event_type, 0)
        return {"count": c, "ahi": round((c / total_hours) if total_hours else 0.0, 3)}

    obstructive = block("Obstructive")
    central = block("ClearAirway")
    hypopnea = block("Hypopnea")
    apnea_unclassified = block("Apnea")
    rera_count = counts.get("RERA", 0)
    rera_rdi = (rera_count / total_hours) if total_hours else 0.0

    ahi_count = (
        counts.get("Obstructive", 0)
        + counts.get("ClearAirway", 0)
        + counts.get("Hypopnea", 0)
        + counts.get("Apnea", 0)
    )
    total_ahi = (ahi_count / total_hours) if total_hours else 0.0

    def pct(c: int) -> float:
        return round(c / ahi_count * 100.0, 1) if ahi_count > 0 else 0.0

    obstructive["pct_of_total"] = pct(obstructive["count"])
    central["pct_of_total"] = pct(central["count"])
    hypopnea["pct_of_total"] = pct(hypopnea["count"])
    apnea_unclassified["pct_of_total"] = pct(apnea_unclassified["count"])

    interpretation = _interpret(
        obstructive_ahi=obstructive["ahi"],
        central_ahi=central["ahi"],
        total_ahi=total_ahi,
    )

    return _ok({
        "date_range": [start.isoformat(), end.isoformat()],
        "total_ahi": round(total_ahi, 3),
        "obstructive": obstructive,
        "central": central,
        "hypopnea": hypopnea,
        "apnea": apnea_unclassified,
        "rera": {"count": rera_count, "rdi_contribution": round(rera_rdi, 3)},
        "interpretation": interpretation,
    })


def _interpret(*, obstructive_ahi: float, central_ahi: float, total_ahi: float) -> dict:
    notes: list[str] = []
    if obstructive_ahi < 2:
        obstructive_status = "well_controlled"
    elif obstructive_ahi < 5:
        obstructive_status = "partial_control"
    else:
        obstructive_status = "inadequate_control"
    if central_ahi < 1:
        central_concern = "none"
    elif central_ahi < 5:
        central_concern = "mild"
    elif central_ahi < 10:
        central_concern = "elevated"
    else:
        central_concern = "significant"
    tecsa_likely = (
        total_ahi >= 5
        and central_ahi >= obstructive_ahi
        and central_ahi / max(total_ahi, 0.001) >= 0.5
    )
    if tecsa_likely:
        notes.append(
            "Centrals dominate the AHI. Consistent with TECSA / treatment-"
            "emergent central apnea pattern."
        )
    if obstructive_status == "inadequate_control":
        notes.append("Obstructive AHI above 5/hr — CPAP pressure may be insufficient.")
    return {
        "obstructive_treatment_status": obstructive_status,
        "central_apnea_concern": central_concern,
        "tecsa_likely": tecsa_likely,
        "notes": notes,
    }
