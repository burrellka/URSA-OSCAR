"""Repository for `sessions` and `excluded_sessions` tables.

Phase 4 Ticket 1 — session-level data + the operator-facing exclusion
list. The two tables are intentionally co-located here because the
session-exclusion feature consumes both as a single conceptual unit
(list sessions, mark which are excluded). recompute_summary() in the
analytics package is the only caller that joins them.

Idempotency notes:
- ``upsert_session`` is delete-then-insert keyed on (date, session_id)
  because DuckDB's INSERT OR REPLACE doesn't honor PK constraints the
  same way SQLite's does. The importer runs this per non-empty session.
- ``set_excluded`` is "toggle to a known state" rather than the toggle
  endpoint's flip — the endpoint computes the new state from the
  current state and then calls this.
"""
from __future__ import annotations

from datetime import date as date_t
from typing import Optional

from ...models.domain import Session
from ..db import DuckDBManager


# Columns selected when reading a session row alongside its excluded
# state — produced by the LEFT JOIN in list_for_date.
_SELECT_FIELDS = ("date", "session_id", "start_ts", "end_ts", "mask_on_minutes")

# v6 — per-session pressure-stat columns. Ordering is the canonical
# tuple shape; helper functions below depend on it.
_PRESSURE_STAT_COLUMNS: tuple[str, ...] = (
    "pressure_median", "pressure_p95", "pressure_p995",
    "ipap_median", "ipap_p95", "ipap_p995",
    "epap_median", "epap_p95", "epap_p995",
    "flow_limit_median", "flow_limit_p95", "flow_limit_p995",
    "leak_median", "leak_p95", "leak_p995",
)


def _row_to_session(row) -> Session:
    """Build a Session from the canonical SELECT row shape used by
    list_for_date / get. Accepts a row with 5 base columns + 15 pressure
    stats + 1 excluded flag (21 fields total)."""
    (
        d, sid, start, end, mins,
        p_med, p_95, p_995,
        i_med, i_95, i_995,
        e_med, e_95, e_995,
        f_med, f_95, f_995,
        l_med, l_95, l_995,
        excluded,
    ) = row
    return Session(
        date=d, session_id=sid, start_ts=start, end_ts=end,
        mask_on_minutes=mins, excluded=bool(excluded),
        pressure_median=p_med, pressure_p95=p_95, pressure_p995=p_995,
        ipap_median=i_med, ipap_p95=i_95, ipap_p995=i_995,
        epap_median=e_med, epap_p95=e_95, epap_p995=e_995,
        flow_limit_median=f_med, flow_limit_p95=f_95, flow_limit_p995=f_995,
        leak_median=l_med, leak_p95=l_95, leak_p995=l_995,
    )


# Reusable column projection. Keeps list_for_date / get in sync without
# repeating the long column list twice.
_SELECT_PROJECTION = (
    "s.date, s.session_id, s.start_ts, s.end_ts, s.mask_on_minutes, "
    + ", ".join(f"s.{c}" for c in _PRESSURE_STAT_COLUMNS)
    + ", x.excluded_at IS NOT NULL AS excluded"
)


def upsert_session(
    db: DuckDBManager,
    date: date_t,
    session_id: int,
    start_ts,
    end_ts,
    mask_on_minutes: float,
) -> None:
    """Write or update one session row.

    Called per non-empty session by the importer. Uses DELETE + INSERT
    so the row's identity is purely (date, session_id) — keeps the
    importer's "delete-for-date then rewrite" idempotency story
    consistent with the events repository.
    """
    if db.read_only:
        raise RuntimeError("sessions.upsert_session called on a read-only DB connection")
    with db.serialized() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE date = ? AND session_id = ?",
            (date, session_id),
        )
        conn.execute(
            """
            INSERT INTO sessions (date, session_id, start_ts, end_ts, mask_on_minutes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (date, session_id, start_ts, end_ts, mask_on_minutes),
        )


def delete_for_date(db: DuckDBManager, date: date_t) -> int:
    """Drop every session row for a given date. Used by the importer's
    night-level dedup path before writing fresh session records.
    Returns the count of rows removed.
    """
    if db.read_only:
        raise RuntimeError("sessions.delete_for_date called on a read-only DB connection")
    with db.serialized() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE date = ?", (date,)
        ).fetchone()[0]
        conn.execute("DELETE FROM sessions WHERE date = ?", (date,))
    return int(before or 0)


def list_for_date(db: DuckDBManager, date: date_t) -> list[Session]:
    """Return every session for a date, in session_id order, with the
    `excluded` flag populated from a LEFT JOIN against excluded_sessions.

    This is the read path that the Daily View's Session Information
    table consumes — one query, no N+1 problems.

    v6 — also pulls the 15 per-session pressure-stat columns for the
    OSCAR Sessions CSV exporter.
    """
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT_PROJECTION}
              FROM sessions s
              LEFT JOIN excluded_sessions x
                ON x.date = s.date AND x.session_id = s.session_id
             WHERE s.date = ?
             ORDER BY s.session_id ASC
            """,
            (date,),
        ).fetchall()
    return [_row_to_session(row) for row in rows]


def get(
    db: DuckDBManager, date: date_t, session_id: int,
) -> Optional[Session]:
    """Look up one specific session. Returns None if no row matches —
    e.g., an operator tried to toggle a session that doesn't exist
    in the importer's record."""
    with db.serialized() as conn:
        row = conn.execute(
            f"""
            SELECT {_SELECT_PROJECTION}
              FROM sessions s
              LEFT JOIN excluded_sessions x
                ON x.date = s.date AND x.session_id = s.session_id
             WHERE s.date = ? AND s.session_id = ?
            """,
            (date, session_id),
        ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def set_pressure_stats(
    db: DuckDBManager,
    date: date_t,
    session_id: int,
    stats: dict[str, float | None],
) -> None:
    """v6 — write the 15 pressure-stat columns for one session.

    ``stats`` must contain entries keyed exactly by the column names
    in ``_PRESSURE_STAT_COLUMNS``. Missing keys default to NULL.
    Idempotent — repeated calls overwrite. Used by the importer (per
    session, post-timeseries-write) and by the standalone backfill
    script."""
    if db.read_only:
        raise RuntimeError("sessions.set_pressure_stats called on a read-only DB connection")
    values = [stats.get(col) for col in _PRESSURE_STAT_COLUMNS]
    set_clause = ", ".join(f"{col} = ?" for col in _PRESSURE_STAT_COLUMNS)
    with db.serialized() as conn:
        conn.execute(
            f"UPDATE sessions SET {set_clause} WHERE date = ? AND session_id = ?",
            (*values, date, session_id),
        )


def compute_pressure_stats(
    db: DuckDBManager,
    date: date_t,
    start_ts,
    end_ts,
) -> dict[str, float | None]:
    """v6 — compute the 15 per-session percentiles from the timeseries
    tables for a given (date, [start_ts, end_ts]) window.

    Channels:
      pressure_timeseries.pressure   -> pressure_*
      pressure_timeseries.epap       -> epap_*
      leak_timeseries.leak_rate      -> leak_*
      flow_limit_timeseries.flow_limit -> flow_limit_*

    IPAP columns stay None — URSA doesn't track a separate IPAP channel
    on single-pressure devices. Reserved for future bilevel-device
    support; the column slots exist in the schema so a later importer
    can fill them without another migration.

    quantile_cont over an empty window returns NULL for each percentile,
    which is the right behavior — a session with no timeseries data
    in a channel just gets NULL in that channel's three columns. The
    OSCAR Sessions CSV exporter renders NULL as ``0`` matching OSCAR's
    own zero-fill convention.
    """
    with db.serialized() as conn:
        pressure_row = conn.execute(
            """
            SELECT quantile_cont(pressure, 0.5),
                   quantile_cont(pressure, 0.95),
                   quantile_cont(pressure, 0.995),
                   quantile_cont(epap, 0.5),
                   quantile_cont(epap, 0.95),
                   quantile_cont(epap, 0.995)
              FROM pressure_timeseries
             WHERE date = ? AND timestamp BETWEEN ? AND ?
            """,
            (date, start_ts, end_ts),
        ).fetchone()
        leak_row = conn.execute(
            """
            SELECT quantile_cont(leak_rate, 0.5),
                   quantile_cont(leak_rate, 0.95),
                   quantile_cont(leak_rate, 0.995)
              FROM leak_timeseries
             WHERE date = ? AND timestamp BETWEEN ? AND ?
            """,
            (date, start_ts, end_ts),
        ).fetchone()
        fl_row = conn.execute(
            """
            SELECT quantile_cont(flow_limit, 0.5),
                   quantile_cont(flow_limit, 0.95),
                   quantile_cont(flow_limit, 0.995)
              FROM flow_limit_timeseries
             WHERE date = ? AND timestamp BETWEEN ? AND ?
            """,
            (date, start_ts, end_ts),
        ).fetchone()

    p_med, p_95, p_995, e_med, e_95, e_995 = pressure_row or (None,) * 6
    l_med, l_95, l_995 = leak_row or (None,) * 3
    f_med, f_95, f_995 = fl_row or (None,) * 3

    return {
        "pressure_median": p_med, "pressure_p95": p_95, "pressure_p995": p_995,
        # IPAP intentionally left NULL on single-pressure devices.
        "ipap_median": None, "ipap_p95": None, "ipap_p995": None,
        "epap_median": e_med, "epap_p95": e_95, "epap_p995": e_995,
        "flow_limit_median": f_med, "flow_limit_p95": f_95, "flow_limit_p995": f_995,
        "leak_median": l_med, "leak_p95": l_95, "leak_p995": l_995,
    }


def list_non_excluded_ids(db: DuckDBManager, date: date_t) -> list[int]:
    """Return session_ids for a date that are NOT in excluded_sessions.
    The hot path for recompute_summary — we filter events + time-series
    to these ids when re-aggregating after a toggle."""
    with db.serialized() as conn:
        rows = conn.execute(
            """
            SELECT s.session_id
              FROM sessions s
              LEFT JOIN excluded_sessions x
                ON x.date = s.date AND x.session_id = s.session_id
             WHERE s.date = ? AND x.excluded_at IS NULL
             ORDER BY s.session_id ASC
            """,
            (date,),
        ).fetchall()
    return [r[0] for r in rows]


def is_excluded(db: DuckDBManager, date: date_t, session_id: int) -> bool:
    """Probe whether a (date, session_id) is currently marked excluded.
    Used by the toggle endpoint to decide insert vs. delete."""
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT 1 FROM excluded_sessions WHERE date = ? AND session_id = ? LIMIT 1",
            (date, session_id),
        ).fetchone()
    return row is not None


def set_excluded(
    db: DuckDBManager, date: date_t, session_id: int, excluded: bool,
) -> None:
    """Force the exclusion state to a known value. Idempotent — calling
    with excluded=True twice produces one row; excluded=False on an
    already-included session is a no-op."""
    if db.read_only:
        raise RuntimeError("sessions.set_excluded called on a read-only DB connection")
    with db.serialized() as conn:
        if excluded:
            # Insert if absent. We deliberately keep the original
            # excluded_at on a repeated exclude so the timestamp tracks
            # the FIRST time the operator excluded this session.
            existing = conn.execute(
                "SELECT 1 FROM excluded_sessions WHERE date = ? AND session_id = ?",
                (date, session_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO excluded_sessions (date, session_id) VALUES (?, ?)",
                    (date, session_id),
                )
        else:
            conn.execute(
                "DELETE FROM excluded_sessions WHERE date = ? AND session_id = ?",
                (date, session_id),
            )


def toggle(db: DuckDBManager, date: date_t, session_id: int) -> bool:
    """Flip a session's exclusion state. Returns the NEW state
    (True = now excluded, False = now included)."""
    new_state = not is_excluded(db, date, session_id)
    set_excluded(db, date, session_id, new_state)
    return new_state
