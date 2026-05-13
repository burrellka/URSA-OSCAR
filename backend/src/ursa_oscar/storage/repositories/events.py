"""Repository for nightly_events rows."""
from __future__ import annotations

from datetime import date as date_t

from ...models.domain import NightlyEvent
from ..db import DuckDBManager


# Columns selected on read (id included so the response carries the
# DB-assigned surrogate key).
_SELECT_FIELDS = (
    "id", "date", "timestamp", "session_id", "event_type", "duration_seconds",
    "pressure_at_event", "epap_at_event", "flow_at_event", "leak_at_event",
)

# Columns specified on INSERT. Note `id` is intentionally omitted — Phase 3
# Item 1A moved id allocation into the table's DEFAULT nextval() so the
# sequence advance is coupled to a successful row write (rather than the
# previous pattern of a Python loop pulling nextval() up front, which could
# desync the sequence if the INSERT failed midway). The DB assigns the id;
# RETURNING reads it back if the caller cares.
_INSERT_FIELDS = (
    "date", "timestamp", "session_id", "event_type", "duration_seconds",
    "pressure_at_event", "epap_at_event", "flow_at_event", "leak_at_event",
)


def bulk_insert(db: DuckDBManager, events: list[NightlyEvent]) -> int:
    """Insert many events at once. Returns count inserted.

    Auto-assigns id from the sequence if event.id is None.
    """
    if db.read_only:
        raise RuntimeError("events.bulk_insert called on a read-only DB connection")
    if not events:
        return 0

    # Phase 3 Item 1A: id is assigned by the DB via DEFAULT nextval(), so the
    # INSERT statement omits the id column entirely. If a caller pre-set
    # event.id (e.g., for a deterministic test seed), we honor that path
    # with a separate per-row INSERT that includes id; otherwise the bulk
    # executemany is used. The DEFAULT/RETURNING split keeps sequence
    # advancement coupled to successful writes — partial-rollback no
    # longer leaves the sequence ahead of the table.
    explicit_id_rows: list[tuple] = []
    default_id_rows: list[tuple] = []
    for e in events:
        payload = e.model_dump()
        if e.id is not None:
            # Include id at the front of the tuple.
            explicit_id_rows.append(
                (e.id, *(payload[f] for f in _INSERT_FIELDS))
            )
        else:
            default_id_rows.append(tuple(payload[f] for f in _INSERT_FIELDS))

    columns_no_id = ", ".join(_INSERT_FIELDS)
    placeholders_no_id = ", ".join(["?"] * len(_INSERT_FIELDS))

    columns_with_id = "id, " + columns_no_id
    placeholders_with_id = "?, " + placeholders_no_id

    with db.serialized() as conn:
        if default_id_rows:
            conn.executemany(
                f"INSERT INTO nightly_events ({columns_no_id}) VALUES ({placeholders_no_id})",
                default_id_rows,
            )
        if explicit_id_rows:
            conn.executemany(
                f"INSERT INTO nightly_events ({columns_with_id}) VALUES ({placeholders_with_id})",
                explicit_id_rows,
            )
        return len(events)


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
