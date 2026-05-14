"""GET endpoints for nightly_summary rows, plus Phase 3 hard-delete purge."""
from __future__ import annotations

import os
from datetime import date as date_t
from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel

from ..models.domain import NightlySummary
from ..storage.repositories import nights as nights_repo

router = APIRouter(prefix="/api/v1", tags=["nights"])


# Time-series tables touched by per-night delete. Matches the
# importer's _PLD_SERIES_MAP plus pressure/flow.
_TIMESERIES_TABLES = (
    "pressure_timeseries",
    "flow_timeseries",
    "leak_timeseries",
    "flow_limit_timeseries",
    "tidal_volume_timeseries",
    "minute_vent_timeseries",
    "resp_rate_timeseries",
    "snore_timeseries",
)


@router.get("/nights", response_model=list[NightlySummary])
def list_nights(
    request: Request,
    start: Optional[date_t] = Query(default=None, description="Inclusive lower bound"),
    end: Optional[date_t] = Query(default=None, description="Inclusive upper bound"),
) -> list[NightlySummary]:
    """List nightly_summary records, optionally bounded by a date range."""
    db = request.app.state.db
    if start is None and end is None:
        # Get every available date, then list rows
        all_dates = nights_repo.list_dates(db)
        if not all_dates:
            return []
        start, end = all_dates[0], all_dates[-1]
    elif start is None:
        all_dates = nights_repo.list_dates(db, end=end)
        if not all_dates:
            return []
        start = all_dates[0]
    elif end is None:
        all_dates = nights_repo.list_dates(db, start=start)
        if not all_dates:
            return []
        end = all_dates[-1]
    return nights_repo.list_in_range(db, start, end)


@router.get("/night/{target_date}", response_model=NightlySummary)
def get_night(target_date: date_t, request: Request) -> NightlySummary:
    """Single nightly_summary row by date. 404 if no data for that night."""
    db = request.app.state.db
    night = nights_repo.get_by_date(db, target_date)
    if night is None:
        raise HTTPException(status_code=404, detail=f"No data for night {target_date}")
    return night


# =====================================================================
# Phase 3 hard-delete purge — Item 5 sprint.
# Three endpoints: single, range, and dry-run preview. All wrapped in
# db.serialized() per ADR-004 so concurrent reads can't observe a
# half-deleted state.
# =====================================================================

class PreviewDeleteRequest(BaseModel):
    start_date: date_t
    end_date: date_t


def _count_for_range(db, start: date_t, end: date_t) -> dict:
    """Return counts of rows that WOULD be deleted by a range purge."""
    with db.serialized() as conn:
        nights = conn.execute(
            "SELECT date FROM nightly_summary WHERE date >= ? AND date <= ? ORDER BY date",
            (start, end),
        ).fetchall()
        dates = [r[0].isoformat() for r in nights]

        events = conn.execute(
            "SELECT COUNT(*) FROM nightly_events WHERE date >= ? AND date <= ?",
            (start, end),
        ).fetchone()[0]

        ts_total = 0
        for table in _TIMESERIES_TABLES:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE date >= ? AND date <= ?",
                (start, end),
            ).fetchone()[0]
            ts_total += int(n)

        manual_logs = conn.execute(
            "SELECT COUNT(*) FROM manual_logs WHERE date >= ? AND date <= ?",
            (start, end),
        ).fetchone()[0]

    return {
        "nights": len(dates),
        "events": int(events),
        "timeseries_rows": ts_total,
        "manual_logs": int(manual_logs),
        "dates": dates,
    }


def _delete_for_range(
    db, start: date_t, end: date_t, delete_manual_logs: bool,
) -> dict:
    """Perform the hard delete. Returns count of rows touched per table."""
    with db.serialized() as conn:
        conn.execute("BEGIN")
        try:
            evt_res = conn.execute(
                "DELETE FROM nightly_events WHERE date >= ? AND date <= ?",
                (start, end),
            ).fetchone()
            events_deleted = int(evt_res[0]) if evt_res else 0

            ts_total = 0
            for table in _TIMESERIES_TABLES:
                row = conn.execute(
                    f"DELETE FROM {table} WHERE date >= ? AND date <= ?",
                    (start, end),
                ).fetchone()
                ts_total += int(row[0]) if row else 0

            nights_res = conn.execute(
                "DELETE FROM nightly_summary WHERE date >= ? AND date <= ?",
                (start, end),
            ).fetchone()
            nights_deleted = int(nights_res[0]) if nights_res else 0

            manual_deleted = 0
            if delete_manual_logs:
                mr = conn.execute(
                    "DELETE FROM manual_logs WHERE date >= ? AND date <= ?",
                    (start, end),
                ).fetchone()
                manual_deleted = int(mr[0]) if mr else 0

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {
        "nights_deleted": nights_deleted,
        "events_deleted": events_deleted,
        "timeseries_rows_deleted": ts_total,
        "manual_logs_deleted": manual_deleted,
    }


def _db_size_mb(db_path) -> float | None:
    try:
        return round(os.path.getsize(str(db_path)) / (1024 * 1024), 2)
    except OSError:
        return None


@router.delete("/nights/{target_date}")
def delete_night(
    target_date: date_t,
    request: Request,
    delete_manual_logs: bool = Query(
        default=False,
        description="If true, also delete manual_logs rows for this date.",
    ),
) -> dict:
    """Hard-delete all data for a single night.

    Removes events, all 7 time-series rows, and the nightly_summary
    entry. Manual logs are kept by default (per architect decision: they
    represent observations independent of the CPAP recording) — pass
    ``delete_manual_logs=true`` to also remove those.
    """
    db = request.app.state.db
    night = nights_repo.get_by_date(db, target_date)
    if night is None:
        raise HTTPException(status_code=404, detail=f"No data for night {target_date}")

    result = _delete_for_range(db, target_date, target_date, delete_manual_logs)
    return {
        "date": target_date.isoformat(),
        "events_deleted": result["events_deleted"],
        "timeseries_rows_deleted": result["timeseries_rows_deleted"],
        "manual_logs_deleted": result["manual_logs_deleted"],
    }


@router.delete("/nights")
def delete_nights_range(
    request: Request,
    start_date: date_t = Query(..., description="Inclusive start"),
    end_date: date_t = Query(..., description="Inclusive end"),
    delete_manual_logs: bool = Query(
        default=False,
        description="If true, also delete manual_logs rows in range.",
    ),
) -> dict:
    """Bulk hard-delete across a date range. After delete, runs CHECKPOINT
    to reclaim disk space and reports before/after DB size.

    The architect-blessed UX requires a UI-side type-to-confirm flow
    before calling this endpoint. The endpoint itself doesn't enforce
    that — it just executes the delete and reports.
    """
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = request.app.state.db
    settings = request.app.state.db.path if hasattr(request.app.state.db, "path") else None
    size_before = _db_size_mb(settings) if settings else None

    counts = _count_for_range(db, start_date, end_date)
    result = _delete_for_range(db, start_date, end_date, delete_manual_logs)

    # CHECKPOINT reclaims space after a large delete. Held under the
    # serialization lock so no concurrent write can race the
    # checkpoint window. CHECKPOINT FORCE is a no-op on DuckDB versions
    # where the regular CHECKPOINT auto-truncates; both shapes work.
    with db.serialized() as conn:
        try:
            conn.execute("CHECKPOINT")
        except Exception:
            pass  # Non-fatal — delete already committed.

    size_after = _db_size_mb(settings) if settings else None

    return {
        "nights_deleted": result["nights_deleted"],
        "events_deleted": result["events_deleted"],
        "timeseries_rows_deleted": result["timeseries_rows_deleted"],
        "manual_logs_deleted": result["manual_logs_deleted"],
        "dates": counts["dates"],
        "db_size_before_mb": size_before,
        "db_size_after_mb": size_after,
    }


@router.post("/nights/preview-delete")
def preview_delete_nights(
    request: Request,
    body: Annotated[PreviewDeleteRequest, Body(...)],
) -> dict:
    """Dry-run: count what would be deleted by DELETE /api/v1/nights with
    the same range. Returns row counts per table plus the list of dates
    that have data, so the UI can show the user exactly what they're
    about to remove."""
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    db = request.app.state.db
    return _count_for_range(db, body.start_date, body.end_date)


@router.post("/admin/checkpoint")
def manual_checkpoint(request: Request) -> dict:
    """Run DuckDB CHECKPOINT to reclaim disk space. Surfaced as a UI
    button on Settings → Data Management. Held under db.serialized()."""
    db = request.app.state.db
    settings = db.path if hasattr(db, "path") else None
    size_before = _db_size_mb(settings) if settings else None
    with db.serialized() as conn:
        conn.execute("CHECKPOINT")
    size_after = _db_size_mb(settings) if settings else None
    return {
        "db_size_before_mb": size_before,
        "db_size_after_mb": size_after,
    }
