"""Repository for nightly_events rows."""
from __future__ import annotations

from datetime import date as date_t

from ...models.domain import NightlyEvent
from ..db import DuckDBManager


_INSERT_FIELDS = (
    "id", "date", "timestamp", "session_id", "event_type", "duration_seconds",
    "pressure_at_event", "epap_at_event", "flow_at_event", "leak_at_event",
)

_SELECT_FIELDS = _INSERT_FIELDS


def bulk_insert(db: DuckDBManager, events: list[NightlyEvent]) -> int:
    """Insert many events at once. Returns count inserted.

    Auto-assigns id from the sequence if event.id is None.
    """
    if db.read_only:
        raise RuntimeError("events.bulk_insert called on a read-only DB connection")
    if not events:
        return 0

    # nextval() + executemany must share one locked window so we don't
    # collide with concurrent inserts allocating the same id range.
    with db.serialized() as conn:
        rows = []
        for e in events:
            eid = e.id
            if eid is None:
                eid = conn.execute("SELECT nextval('nightly_events_id_seq')").fetchone()[0]
            payload = e.model_dump()
            payload["id"] = eid
            rows.append(tuple(payload[f] for f in _INSERT_FIELDS))

        placeholders = ", ".join(["?"] * len(_INSERT_FIELDS))
        columns = ", ".join(_INSERT_FIELDS)
        conn.executemany(
            f"INSERT INTO nightly_events ({columns}) VALUES ({placeholders})",
            rows,
        )
        return len(rows)


def delete_for_date(db: DuckDBManager, target: date_t) -> int:
    """Remove all events for `target`. Used by the re-import dedup logic."""
    if db.read_only:
        raise RuntimeError("events.delete_for_date called on a read-only DB connection")
    cur = db.execute("DELETE FROM nightly_events WHERE date = ?", (target,))
    row = cur.fetchone()
    return (row[0] if row else 0) or 0


def list_for_date(
    db: DuckDBManager,
    target: date_t,
    event_types: list[str] | None = None,
) -> list[NightlyEvent]:
    columns = ", ".join(_SELECT_FIELDS)
    sql = f"SELECT {columns} FROM nightly_events WHERE date = ?"
    params: list = [target]
    if event_types:
        placeholders = ", ".join(["?"] * len(event_types))
        sql += f" AND event_type IN ({placeholders})"
        params.extend(event_types)
    sql += " ORDER BY timestamp ASC"
    rows = db.execute(sql, params).fetchall()
    return [NightlyEvent.model_validate(dict(zip(_SELECT_FIELDS, r))) for r in rows]


def count_for_date(db: DuckDBManager, target: date_t) -> dict[str, int]:
    """Return per-event-type counts for a single night.

    Used by MCP `get_ahi_breakdown` and the regression harness.
    """
    rows = db.execute(
        "SELECT event_type, COUNT(*) FROM nightly_events "
        "WHERE date = ? GROUP BY event_type",
        (target,),
    ).fetchall()
    return {event_type: count for event_type, count in rows}
