"""Export endpoints — Phase 2 CSV; Phase 3 OSCAR-compat / PDF."""
from __future__ import annotations

import csv
import io
from datetime import date as date_t

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..storage.repositories import events as events_repo
from ..storage.repositories import nights as nights_repo

router = APIRouter(prefix="/api/v1", tags=["exports"])


@router.get("/exports/{target_date}.csv")
def export_night_csv(target_date: date_t, request: Request) -> StreamingResponse:
    """Stream a single-night CSV.

    Columns mirror the `nightly_summary` schema fields plus joined per-event-type
    counts (ca_count / oa_count / a_count / h_count / rera_count /
    large_leak_count). Schema and column order are URSA-OSCAR-canonical — not
    bit-for-bit OSCAR's Summary CSV, which contains 18 event-type tallies the
    AirSense 11 doesn't surface in EVE.edf. Phase 3 will add an
    OSCAR-compat format flag for clinicians who want the historical layout.
    """
    db = request.app.state.db
    night = nights_repo.get_by_date(db, target_date)
    if night is None:
        raise HTTPException(status_code=404, detail=f"No nightly data for {target_date}")

    event_counts = events_repo.count_for_date(db, target_date)

    buf = io.StringIO()
    writer = csv.writer(buf)

    summary_fields = [
        "date", "session_count", "start_time", "end_time", "total_time_minutes",
        "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index", "rera_index",
        "median_pressure", "p95_pressure", "p995_pressure",
        "median_epap", "p95_epap", "p995_epap",
        "median_leak", "p95_leak", "p995_leak",
        "minutes_in_apnea", "minutes_over_leak_redline", "large_leak_pct",
        "machine_model", "mode",
        "min_pressure_setting", "max_pressure_setting",
        "epr_level", "ramp_time_minutes", "humidity_level", "mask_type",
    ]
    event_count_fields = [
        ("ca_count", "ClearAirway"),
        ("oa_count", "Obstructive"),
        ("a_count", "Apnea"),
        ("h_count", "Hypopnea"),
        ("rera_count", "RERA"),
        ("large_leak_count", "LargeLeak"),
    ]

    writer.writerow(summary_fields + [name for name, _ in event_count_fields])

    payload = night.model_dump()
    row: list = []
    for f in summary_fields:
        v = payload.get(f)
        if hasattr(v, "isoformat"):
            row.append(v.isoformat())
        elif v is None:
            row.append("")
        else:
            row.append(v)
    for _, t in event_count_fields:
        row.append(event_counts.get(t, 0))
    writer.writerow(row)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="ursa-oscar-{target_date}.csv"',
        },
    )


@router.get("/exports")
def export_range_csv(
    request: Request,
    start_date: date_t,
    end_date: date_t,
    format: str = "csv",
) -> StreamingResponse:
    """Phase 3 Item 7 — bulk / range CSV export.

    Returns a multi-night CSV streamed as text/csv with one row per
    night in the range. Same column shape as the per-night endpoint
    above, so downstream cross-validation against OSCAR Summary CSVs
    (and the user's own scripts) keeps working — the only difference
    is more rows.

    Query parameters:
        start_date: YYYY-MM-DD, inclusive
        end_date:   YYYY-MM-DD, inclusive
        format:     reserved for a future OSCAR-compat layout; currently
                    only 'csv' is supported. Pass-through is silent so
                    a client that hard-codes ?format=csv works today.

    The response uses chunked transfer encoding (StreamingResponse) so
    large ranges don't buffer the whole CSV in memory before the first
    byte ships. For Kevin's 28-night use case the payload is < 50 KB
    anyway, but the streaming path future-proofs for a multi-year range.
    """
    if format != "csv":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{format}'. Only 'csv' is supported in this release.",
        )
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = request.app.state.db
    nights = nights_repo.list_in_range(db, start_date, end_date)

    summary_fields = [
        "date", "session_count", "start_time", "end_time", "total_time_minutes",
        "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index", "rera_index",
        "median_pressure", "p95_pressure", "p995_pressure",
        "median_epap", "p95_epap", "p995_epap",
        "median_leak", "p95_leak", "p995_leak",
        "minutes_in_apnea", "minutes_over_leak_redline", "large_leak_pct",
        "machine_model", "mode",
        "min_pressure_setting", "max_pressure_setting",
        "epr_level", "ramp_time_minutes", "humidity_level", "mask_type",
    ]
    event_count_fields = [
        ("ca_count", "ClearAirway"),
        ("oa_count", "Obstructive"),
        ("a_count", "Apnea"),
        ("h_count", "Hypopnea"),
        ("rera_count", "RERA"),
        ("large_leak_count", "LargeLeak"),
    ]

    def gen() -> "io.Iterator[str]":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(summary_fields + [name for name, _ in event_count_fields])
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)

        # One DB hit per night for event counts is fine at this scale —
        # 28 nights × ~ms each is sub-second total.
        for night in nights:
            event_counts = events_repo.count_for_date(db, night.date)
            payload = night.model_dump()
            row: list = []
            for f in summary_fields:
                v = payload.get(f)
                if hasattr(v, "isoformat"):
                    row.append(v.isoformat())
                elif v is None:
                    row.append("")
                else:
                    row.append(v)
            for _, t in event_count_fields:
                row.append(event_counts.get(t, 0))
            writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    filename = f"ursa-oscar-{start_date}_to_{end_date}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
