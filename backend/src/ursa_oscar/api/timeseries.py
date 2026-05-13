"""GET /api/v1/timeseries/{date} — per-channel waveform data for the Daily View."""
from __future__ import annotations

from datetime import date as date_t
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..storage.repositories import timeseries as ts_repo

router = APIRouter(prefix="/api/v1", tags=["timeseries"])


# Public series names — match the repository's SERIES_SCHEMA. Validated at
# request time so a typo in the URL surfaces a 400 instead of a 500.
ALLOWED_SERIES = set(ts_repo.SERIES_SCHEMA.keys())


@router.get("/timeseries/{target_date}")
def get_timeseries(
    target_date: date_t,
    request: Request,
    series: list[str] = Query(
        default=["pressure"],
        description="One or more channel names. Defaults to ['pressure'].",
    ),
) -> dict:
    """Returns one or more waveform channels for a single night.

    Response shape:
        {
          "date": "YYYY-MM-DD",
          "series": {
             "pressure":   {"timestamps": [epoch_ms, ...], "values": [...], "secondary": [...] | null},
             "leak":       {"timestamps": [...], "values": [...], "secondary": null},
             ...
          }
        }

    Timestamps are epoch milliseconds (uPlot-friendly). The pressure series
    returns a `secondary` array carrying EPAP values aligned 1:1 with the
    primary pressure samples — the Daily View renders Pressure + EPAP as two
    lines on a single track.
    """
    db = request.app.state.db

    requested = [s.strip() for s in series if s and s.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="At least one series name required")
    bad = [s for s in requested if s not in ALLOWED_SERIES]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown series: {bad}. Allowed: {sorted(ALLOWED_SERIES)}",
        )

    # Fetch the night's range bounds from nightly_summary so we know what window
    # to query. Avoids returning a fully-empty payload if the schema row is
    # missing or the night hasn't been imported.
    bounds_row = db.execute(
        "SELECT start_time, end_time FROM nightly_summary WHERE date = ?",
        (target_date,),
    ).fetchone()
    if not bounds_row:
        raise HTTPException(status_code=404, detail=f"No night data for {target_date}")
    start_ts, end_ts = bounds_row
    if start_ts is None or end_ts is None:
        raise HTTPException(status_code=404, detail=f"Night {target_date} has no time window")

    out: dict[str, dict] = {}
    for s in requested:
        rows = ts_repo.range_query(db, s, start_ts, end_ts)
        if not rows:
            out[s] = {"timestamps": [], "values": [], "secondary": None}
            continue
        # rows: (timestamp, value [, secondary])
        timestamps = [int(r[0].timestamp() * 1000) for r in rows]
        values = [r[1] for r in rows]
        secondary = None
        if len(rows[0]) == 3:
            secondary = [r[2] for r in rows]
        out[s] = {"timestamps": timestamps, "values": values, "secondary": secondary}

    return {"date": target_date.isoformat(), "series": out}
