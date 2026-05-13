"""Repository for nightly_summary rows."""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime, timezone
from typing import Optional

from ...models.domain import NightlySummary
from ..db import DuckDBManager


# Order matters — must align with the INSERT below and the SELECT projection.
_FIELDS = (
    "date", "session_count", "start_time", "end_time", "total_time_minutes",
    "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index", "rera_index",
    "median_pressure", "p95_pressure", "p995_pressure",
    "median_epap", "p95_epap", "p995_epap",
    "median_leak", "p95_leak", "p995_leak",
    "minutes_in_apnea", "minutes_over_leak_redline",
    "cheyne_stokes_pct", "large_leak_pct",
    "machine_model", "mode",
    "min_pressure_setting", "max_pressure_setting",
    "epr_level", "ramp_time_minutes",
    "humidity_level", "mask_type",
    # Schema v2 — Device-Settings expansion
    "antibacterial_filter", "climate_control", "epr_mode",
    "humidifier_status", "patient_view", "response_mode",
    "smart_start", "temperature_celsius", "temperature_enable",
    "last_updated",
)


def upsert(db: DuckDBManager, night: NightlySummary) -> None:
    """Insert or overwrite a nightly_summary row (PK = date).

    DuckDB doesn't support ON CONFLICT DO UPDATE on all column sets without
    explicit constraints, so we DELETE+INSERT in a transaction.
    """
    if db.read_only:
        raise RuntimeError("nights.upsert called on a read-only DB connection")

    placeholders = ", ".join(["?"] * len(_FIELDS))
    columns = ", ".join(_FIELDS)
    payload = night.model_dump()
    # Phase 3 Item 1B: last_updated must reflect "when this row was
    # written," not whatever the analytics pipeline happened to leave on
    # the in-memory model (which was always None and got inserted as
    # NULL — overriding the column's DEFAULT CURRENT_TIMESTAMP and
    # making the Daily View's "last imported" header render blank).
    # Always stamp it server-side at write time.
    payload["last_updated"] = datetime.now(timezone.utc)
    values = tuple(payload[f] for f in _FIELDS)

    # Held across BEGIN/COMMIT so concurrent readers can't observe the
    # window between DELETE and INSERT.
    with db.serialized() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM nightly_summary WHERE date = ?", (night.date,))
            conn.execute(
                f"INSERT INTO nightly_summary ({columns}) VALUES ({placeholders})",
                values,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def get_by_date(db: DuckDBManager, target: date_t) -> Optional[NightlySummary]:
    columns = ", ".join(_FIELDS)
    row = db.execute(
        f"SELECT {columns} FROM nightly_summary WHERE date = ?",
        (target,),
    ).fetchone()
    if row is None:
        return None
    return NightlySummary.model_validate(dict(zip(_FIELDS, row)))


def list_dates(
    db: DuckDBManager,
    start: date_t | None = None,
    end: date_t | None = None,
) -> list[date_t]:
    """Returns available nightly dates in ascending order, optionally bounded."""
    where_clauses = []
    params: list = []
    if start is not None:
        where_clauses.append("date >= ?")
        params.append(start)
    if end is not None:
        where_clauses.append("date <= ?")
        params.append(end)
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = db.execute(
        f"SELECT date FROM nightly_summary {where} ORDER BY date ASC",
        params if params else None,
    ).fetchall()
    return [r[0] for r in rows]


def list_in_range(
    db: DuckDBManager,
    start: date_t,
    end: date_t,
) -> list[NightlySummary]:
    columns = ", ".join(_FIELDS)
    rows = db.execute(
        f"SELECT {columns} FROM nightly_summary "
        "WHERE date >= ? AND date <= ? ORDER BY date ASC",
        (start, end),
    ).fetchall()
    return [NightlySummary.model_validate(dict(zip(_FIELDS, r))) for r in rows]


def delete_for_date(db: DuckDBManager, target: date_t) -> int:
    """Remove all summary rows for `target`. Returns count deleted (0 or 1).

    Used during re-import: dedup-on-date overwrites everything for that night.
    """
    if db.read_only:
        raise RuntimeError("nights.delete_for_date called on a read-only DB connection")
    return db.execute(
        "DELETE FROM nightly_summary WHERE date = ?", (target,)
    ).fetchone()[0] or 0
