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
    """
    with db.serialized() as conn:
        rows = conn.execute(
            """
            SELECT s.date, s.session_id, s.start_ts, s.end_ts, s.mask_on_minutes,
                   x.excluded_at IS NOT NULL AS excluded
              FROM sessions s
              LEFT JOIN excluded_sessions x
                ON x.date = s.date AND x.session_id = s.session_id
             WHERE s.date = ?
             ORDER BY s.session_id ASC
            """,
            (date,),
        ).fetchall()
    out: list[Session] = []
    for row in rows:
        d, sid, start, end, mins, excluded = row
        out.append(Session(
            date=d, session_id=sid, start_ts=start, end_ts=end,
            mask_on_minutes=mins, excluded=bool(excluded),
        ))
    return out


def get(
    db: DuckDBManager, date: date_t, session_id: int,
) -> Optional[Session]:
    """Look up one specific session. Returns None if no row matches —
    e.g., an operator tried to toggle a session that doesn't exist
    in the importer's record."""
    with db.serialized() as conn:
        row = conn.execute(
            """
            SELECT s.date, s.session_id, s.start_ts, s.end_ts, s.mask_on_minutes,
                   x.excluded_at IS NOT NULL AS excluded
              FROM sessions s
              LEFT JOIN excluded_sessions x
                ON x.date = s.date AND x.session_id = s.session_id
             WHERE s.date = ? AND s.session_id = ?
            """,
            (date, session_id),
        ).fetchone()
    if row is None:
        return None
    d, sid, start, end, mins, excluded = row
    return Session(
        date=d, session_id=sid, start_ts=start, end_ts=end,
        mask_on_minutes=mins, excluded=bool(excluded),
    )


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
