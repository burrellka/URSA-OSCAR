"""OSCAR-compatible CSV export endpoints — 0.9.7.

Mirrors the column layout OSCAR itself produces, so files emitted here
are drop-in compatible with any downstream tool that consumes OSCAR's
Daily / Summary / Sessions CSVs (SleepHQ, oscar-parity scripts, the
operator's own R/Python workbench).

Three shapes:

  OSCAR Summary  — one row per night
                   42 columns: Date, Session Count, Start, End, Total
                   Time, AHI, then 16 event-type counts, then 21
                   pressure-stat columns (Median/95/99.5 ×
                   Pressure/IPAP/EPAP/FlowLimit with paired "Set"
                   variants).
  OSCAR Sessions — one row per session, same columns but with
                   ``Session`` (the session id) instead of
                   ``Session Count``. Per-session event counts come
                   from the events table grouped by session_id;
                   per-session AHI derived from event counts +
                   mask-on time.
  OSCAR Daily    — one row per event, four columns: DateTime, Session,
                   Event, Data/Duration.

URSA-OSCAR doesn't currently track several OSCAR event types (UA, VS,
VS2, SA, NR, EP, UF1, UF2, PP) or the "Pressure Set" requested-setting
columns or IPAP/Flow-Limit-stat columns. By operator decision (0.9.7)
those are zero-filled so the column layout matches OSCAR exactly —
matches OSCAR's own behavior, which fills the same columns with 0 on
single-pressure devices like the AirSense 11.

Endpoints:

  GET  /api/v1/exports/oscar/summary.csv?start_date=&end_date=
  GET  /api/v1/exports/oscar/sessions.csv?start_date=&end_date=
  GET  /api/v1/exports/oscar/daily.csv?start_date=&end_date=
  POST /api/v1/exports/oscar/server  — write the same CSV to
       ``EXPORTS_PATH`` and return ``{filename, path, bytes}``.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date as date_t
from pathlib import Path
from typing import Iterable, Iterator, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models.domain import NightlyEvent, NightlySummary, Session
from ..storage.repositories import events as events_repo
from ..storage.repositories import nights as nights_repo
from ..storage.repositories import sessions as sessions_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exports/oscar", tags=["exports"])


# -----------------------------------------------------------------------
# Column definitions — frozen here so the layout doesn't drift.
# -----------------------------------------------------------------------

# Event-type tally columns. URSA-OSCAR populates the ones in the dict
# values; the rest zero-fill. Order matches OSCAR Summary / Sessions
# columns positions 6..21.
_OSCAR_EVENT_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("CA Count", "ClearAirway"),
    ("A Count", "Apnea"),
    ("OA Count", "Obstructive"),
    ("H Count", "Hypopnea"),
    ("UA Count", None),
    ("VS Count", None),
    ("VS2 Count", None),
    ("RE Count", "RERA"),
    ("FL Count", "FlowLimit"),
    ("SA Count", None),
    ("NR Count", None),
    ("EP Count", None),
    ("LF Count", "LargeLeak"),
    ("UF1 Count", None),
    ("UF2 Count", None),
    ("PP Count", "PeriodicBreathing"),
)

# Pressure-stat columns. Maps the OSCAR column name to a NightlySummary
# attribute name when URSA tracks it. ``None`` means zero-fill.
_OSCAR_PRESSURE_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("Median Pressure", "median_pressure"),
    ("Median Pressure Set", None),
    ("Median IPAP", None),
    ("Median IPAP Set", None),
    ("Median EPAP", "median_epap"),
    ("Median EPAP Set", None),
    ("Median Flow Limit.", None),
    ("95% Pressure", "p95_pressure"),
    ("95% Pressure Set", None),
    ("95% IPAP", None),
    ("95% IPAP Set", None),
    ("95% EPAP", "p95_epap"),
    ("95% EPAP Set", None),
    ("95% Flow Limit.", None),
    ("99.5% Pressure", "p995_pressure"),
    ("99.5% Pressure Set", None),
    ("99.5% IPAP", None),
    ("99.5% IPAP Set", None),
    ("99.5% EPAP", "p995_epap"),
    ("99.5% EPAP Set", None),
    ("99.5% Flow Limit.", None),
)

# v6 — per-session pressure-stat mapping (Phase 5.5). Same OSCAR column
# names + ordering as ``_OSCAR_PRESSURE_COLUMNS``, but values come from
# the ``Session`` model's cached fields instead of NightlySummary's
# night-level stats. ``None`` columns still zero-fill: the "Pressure
# Set" / "EPAP Set" / "IPAP Set" series are device-requested-setting
# values URSA doesn't track at all, and the IPAP percentiles stay None
# on the Session model for AirSense single-pressure data (NULL in the
# DB → "0" in CSV via _fmt_number — same behavior as OSCAR's own
# single-pressure exports). When/if bilevel-device support lands the
# ipap_* columns get populated by the importer and the same CSV path
# renders them automatically.
_OSCAR_SESSION_PRESSURE_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("Median Pressure", "pressure_median"),
    ("Median Pressure Set", None),
    ("Median IPAP", "ipap_median"),
    ("Median IPAP Set", None),
    ("Median EPAP", "epap_median"),
    ("Median EPAP Set", None),
    ("Median Flow Limit.", "flow_limit_median"),
    ("95% Pressure", "pressure_p95"),
    ("95% Pressure Set", None),
    ("95% IPAP", "ipap_p95"),
    ("95% IPAP Set", None),
    ("95% EPAP", "epap_p95"),
    ("95% EPAP Set", None),
    ("95% Flow Limit.", "flow_limit_p95"),
    ("99.5% Pressure", "pressure_p995"),
    ("99.5% Pressure Set", None),
    ("99.5% IPAP", "ipap_p995"),
    ("99.5% IPAP Set", None),
    ("99.5% EPAP", "epap_p995"),
    ("99.5% EPAP Set", None),
    ("99.5% Flow Limit.", "flow_limit_p995"),
)

_OSCAR_SUMMARY_HEADER: list[str] = (
    ["Date", "Session Count", "Start", "End", "Total Time", "AHI"]
    + [name for name, _ in _OSCAR_EVENT_COLUMNS]
    + [name for name, _ in _OSCAR_PRESSURE_COLUMNS]
)

_OSCAR_SESSIONS_HEADER: list[str] = (
    ["Date", "Session", "Start", "End", "Total Time", "AHI"]
    + [name for name, _ in _OSCAR_EVENT_COLUMNS]
    + [name for name, _ in _OSCAR_PRESSURE_COLUMNS]
)

_OSCAR_DAILY_HEADER: list[str] = ["DateTime", "Session", "Event", "Data/Duration"]


# -----------------------------------------------------------------------
# Request validation + helpers.
# -----------------------------------------------------------------------


def _resolve_range(
    db,
    start_date: date_t | None,
    end_date: date_t | None,
) -> tuple[date_t, date_t]:
    """Validate the requested range. When both are None, fall back to the
    'most recent day' present in the DB — that matches the frontend's
    'Most Recent Day' preset default."""
    if start_date is None and end_date is None:
        all_dates = nights_repo.list_dates(db)
        if not all_dates:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No nightly data has been imported yet. Run an "
                    "import first, then retry the export."
                ),
            )
        only = all_dates[-1]  # list_dates returns ascending; tail = newest
        return only, only
    if start_date is None or end_date is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide both start_date and end_date, or neither (to "
                "default to the most recent night)."
            ),
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must be on or after start_date.",
        )
    return start_date, end_date


def _format_total_time(minutes: int | float | None) -> str:
    """OSCAR's Total Time format is HH:MM:SS. URSA tracks integer
    minutes, so seconds always print as '00'."""
    if minutes is None:
        return "00:00:00"
    total = int(round(float(minutes)))
    hours, rem = divmod(total, 60)
    return f"{hours:02d}:{rem:02d}:00"


def _iso_or_blank(dt) -> str:
    if dt is None:
        return ""
    return dt.isoformat(timespec="seconds")


def _fmt_number(v) -> str:
    """OSCAR writes floats without scientific notation. We mirror its
    look: integers stay bare, floats keep meaningful decimals."""
    if v is None:
        return "0"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # Strip trailing zeros while keeping ".0" for whole-number floats
        # (OSCAR's own output mixes both — '7.1' and '0' next to each
        # other). Match by rounding to 3 dp and dropping needless zeros.
        s = f"{v:.3f}".rstrip("0").rstrip(".")
        return s if s and s != "-" else "0"
    return str(v)


# -----------------------------------------------------------------------
# Row builders.
# -----------------------------------------------------------------------


def _build_summary_row(
    night: NightlySummary,
    event_counts: dict[str, int],
) -> list[str]:
    row: list[str] = [
        night.date.isoformat(),
        str(night.session_count or 0),
        _iso_or_blank(night.start_time),
        _iso_or_blank(night.end_time),
        _format_total_time(night.total_time_minutes),
        _fmt_number(night.total_ahi),
    ]
    for _name, urs_type in _OSCAR_EVENT_COLUMNS:
        row.append(str(event_counts.get(urs_type, 0) if urs_type else 0))
    for _name, attr in _OSCAR_PRESSURE_COLUMNS:
        val = getattr(night, attr) if attr else None
        row.append(_fmt_number(val))
    return row


def _build_session_row(
    night_date: date_t,
    session: Session,
    session_events: list[NightlyEvent],
) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for ev in session_events:
        counts[ev.event_type] += 1

    # Per-session AHI from event counts + mask-on hours. The OSCAR AHI
    # column on the Sessions CSV is computed the same way (counts of
    # CA + A + OA + H over recorded hours).
    ahi_event_count = (
        counts.get("ClearAirway", 0)
        + counts.get("Apnea", 0)
        + counts.get("Obstructive", 0)
        + counts.get("Hypopnea", 0)
    )
    hours = (session.mask_on_minutes or 0) / 60.0
    ahi = (ahi_event_count / hours) if hours > 0 else 0.0

    row: list[str] = [
        night_date.isoformat(),
        str(session.session_id),
        _iso_or_blank(session.start_ts),
        _iso_or_blank(session.end_ts),
        _format_total_time(session.mask_on_minutes),
        _fmt_number(round(ahi, 3)),
    ]
    for _name, urs_type in _OSCAR_EVENT_COLUMNS:
        row.append(str(counts.get(urs_type, 0) if urs_type else 0))
    # v6 (Phase 5.5) — per-session pressure stats come from the
    # ``sessions`` table cache populated at import time + by the v6
    # backfill on first 0.9.8 boot. Columns URSA doesn't track
    # (Pressure Set / IPAP Set / EPAP Set on AirSense data, IPAP
    # percentiles on single-pressure devices, Flow-Limit-Set) still
    # zero-fill via _fmt_number(None) → "0".
    for _name, attr in _OSCAR_SESSION_PRESSURE_COLUMNS:
        val = getattr(session, attr) if attr else None
        row.append(_fmt_number(val))
    return row


def _build_daily_rows(events: Iterable[NightlyEvent]) -> Iterator[list[str]]:
    for ev in events:
        # OSCAR's Data/Duration column carries duration in seconds with
        # 2-decimal precision (e.g., '10.00').
        dur = ev.duration_seconds or 0.0
        yield [
            ev.timestamp.isoformat(timespec="seconds"),
            str(ev.session_id) if ev.session_id is not None else "",
            ev.event_type,
            f"{dur:.2f}",
        ]


# -----------------------------------------------------------------------
# CSV streaming generators.
# -----------------------------------------------------------------------


def _stream_csv(
    header: list[str],
    rows: Iterable[list[str]],
) -> Iterator[str]:
    """Generic chunked CSV streamer. Reuses one StringIO buffer per row
    to keep memory flat across large ranges."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    for row in rows:
        writer.writerow(row)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


def _summary_rows(db, start_date: date_t, end_date: date_t) -> Iterator[list[str]]:
    for night in nights_repo.list_in_range(db, start_date, end_date):
        counts = events_repo.count_for_date(db, night.date)
        yield _build_summary_row(night, counts)


def _session_rows(db, start_date: date_t, end_date: date_t) -> Iterator[list[str]]:
    for night in nights_repo.list_in_range(db, start_date, end_date):
        sessions = sessions_repo.list_for_date(db, night.date)
        events = events_repo.list_for_date(db, night.date)
        # Group events by session_id once per night.
        by_session: dict[int, list[NightlyEvent]] = defaultdict(list)
        for ev in events:
            if ev.session_id is not None:
                by_session[ev.session_id].append(ev)
        for session in sessions:
            yield _build_session_row(
                night.date, session, by_session.get(session.session_id, []),
            )


def _daily_rows(db, start_date: date_t, end_date: date_t) -> Iterator[list[str]]:
    for night in nights_repo.list_in_range(db, start_date, end_date):
        for row in _build_daily_rows(events_repo.list_for_date(db, night.date)):
            yield row


# Single source of truth for "what's the header + row generator for a
# given OSCAR-shape type". Used by both the GET endpoints and the
# POST /server endpoint.
_TYPE_REGISTRY: dict[str, tuple[list[str], str]] = {
    "summary": (_OSCAR_SUMMARY_HEADER, "Summary"),
    "sessions": (_OSCAR_SESSIONS_HEADER, "Sessions"),
    "daily": (_OSCAR_DAILY_HEADER, "Daily"),
}


def _rows_for_type(
    db, export_type: str, start_date: date_t, end_date: date_t,
) -> Iterator[list[str]]:
    if export_type == "summary":
        yield from _summary_rows(db, start_date, end_date)
    elif export_type == "sessions":
        yield from _session_rows(db, start_date, end_date)
    elif export_type == "daily":
        yield from _daily_rows(db, start_date, end_date)
    else:  # pragma: no cover - registry guards this
        raise HTTPException(status_code=400, detail=f"Unknown export type: {export_type}")


def _filename_for(
    export_type: str, start_date: date_t, end_date: date_t,
) -> str:
    """Single-date or range, matching OSCAR's naming convention with
    our project prefix in place of the user-id field."""
    _, label = _TYPE_REGISTRY[export_type]
    if start_date == end_date:
        return f"URSA-OSCAR_{label}_{start_date}.csv"
    return f"URSA-OSCAR_{label}_{start_date}_to_{end_date}.csv"


# -----------------------------------------------------------------------
# GET endpoints — stream to browser as text/csv attachment.
# -----------------------------------------------------------------------


@router.get("/summary.csv")
def export_oscar_summary(
    request: Request,
    start_date: date_t | None = None,
    end_date: date_t | None = None,
) -> StreamingResponse:
    """OSCAR Summary CSV (one row per night) for the given range. When
    neither date is provided, defaults to the most recent night."""
    db = request.app.state.db
    s, e = _resolve_range(db, start_date, end_date)
    filename = _filename_for("summary", s, e)
    return StreamingResponse(
        _stream_csv(_OSCAR_SUMMARY_HEADER, _summary_rows(db, s, e)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions.csv")
def export_oscar_sessions(
    request: Request,
    start_date: date_t | None = None,
    end_date: date_t | None = None,
) -> StreamingResponse:
    """OSCAR Sessions CSV (one row per session)."""
    db = request.app.state.db
    s, e = _resolve_range(db, start_date, end_date)
    filename = _filename_for("sessions", s, e)
    return StreamingResponse(
        _stream_csv(_OSCAR_SESSIONS_HEADER, _session_rows(db, s, e)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/daily.csv")
def export_oscar_daily(
    request: Request,
    start_date: date_t | None = None,
    end_date: date_t | None = None,
) -> StreamingResponse:
    """OSCAR Daily CSV (one row per event)."""
    db = request.app.state.db
    s, e = _resolve_range(db, start_date, end_date)
    filename = _filename_for("daily", s, e)
    return StreamingResponse(
        _stream_csv(_OSCAR_DAILY_HEADER, _daily_rows(db, s, e)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -----------------------------------------------------------------------
# POST /server — write the same CSV to disk under EXPORTS_PATH.
# -----------------------------------------------------------------------


ExportType = Literal["summary", "sessions", "daily"]


class ServerExportRequest(BaseModel):
    export_type: ExportType = Field(
        ..., description="One of: summary, sessions, daily.",
    )
    start_date: date_t | None = None
    end_date: date_t | None = None


class ServerExportResult(BaseModel):
    filename: str
    path: str
    bytes: int
    rows: int


@router.post("/server", response_model=ServerExportResult)
def export_oscar_to_server(
    body: ServerExportRequest,
    request: Request,
) -> ServerExportResult:
    """Write the requested OSCAR-shape CSV to ``EXPORTS_PATH`` inside
    the API container (operator-visible at the bound volume mount).
    Returns the filename, absolute path inside the container, byte
    count, and data-row count (excludes header)."""
    db = request.app.state.db
    s, e = _resolve_range(db, body.start_date, body.end_date)

    if body.export_type not in _TYPE_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown export_type: {body.export_type}",
        )
    header, _ = _TYPE_REGISTRY[body.export_type]
    filename = _filename_for(body.export_type, s, e)

    settings = get_settings()
    exports_dir: Path = settings.exports_path
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_path = exports_dir / filename

    row_count = 0
    bytes_written = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for row in _rows_for_type(db, body.export_type, s, e):
            writer.writerow(row)
            row_count += 1
        fh.flush()
        bytes_written = fh.tell()

    logger.info(
        "exports/oscar/server wrote %s (%d rows, %d bytes) to %s",
        filename, row_count, bytes_written, out_path,
    )
    return ServerExportResult(
        filename=filename,
        path=str(out_path),
        bytes=bytes_written,
        rows=row_count,
    )
