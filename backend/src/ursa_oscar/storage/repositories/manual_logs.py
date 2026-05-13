"""Repository for manual_logs rows.

Phase 3 work; Phase 1 stubs the CRUD surface so the FastAPI / MCP layers can
reference it without import errors.
"""
from __future__ import annotations

from datetime import date as date_t

from ...models.domain import ManualLog
from ..db import DuckDBManager


# Columns selected on read.
_SELECT_FIELDS = (
    "id", "date", "log_type", "timestamp", "value_text", "value_numeric",
    "unit", "category", "notes", "last_updated",
)

# Columns specified on INSERT. Phase 3 Item 1A: `id` omitted so the
# DB-side DEFAULT nextval() allocates it inside the INSERT transaction
# instead of a pre-fetched value that could desync on failed writes.
_INSERT_FIELDS = (
    "date", "log_type", "timestamp", "value_text", "value_numeric",
    "unit", "category", "notes", "last_updated",
)

# Back-compat alias for any older imports that still reference _FIELDS as
# the read column list. New code should use _SELECT_FIELDS.
_FIELDS = _SELECT_FIELDS


def insert(db: DuckDBManager, log: ManualLog) -> ManualLog:
    if db.read_only:
        raise RuntimeError("manual_logs.insert called on a read-only DB connection")
    # Phase 3 Item 1A: id allocated by DEFAULT nextval() inside the INSERT.
    # If caller pre-set log.id, honor that with an explicit-id INSERT;
    # otherwise let the DB assign and read it back via RETURNING.
    payload = log.model_dump()
    with db.serialized() as conn:
        if log.id is not None:
            columns = "id, " + ", ".join(_INSERT_FIELDS)
            placeholders = "?, " + ", ".join(["?"] * len(_INSERT_FIELDS))
            values = (log.id, *(payload[f] for f in _INSERT_FIELDS))
            conn.execute(
                f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders})",
                values,
            )
            return log.model_copy(update={"id": log.id})
        columns = ", ".join(_INSERT_FIELDS)
        placeholders = ", ".join(["?"] * len(_INSERT_FIELDS))
        values = tuple(payload[f] for f in _INSERT_FIELDS)
        row = conn.execute(
            f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders}) RETURNING id",
            values,
        ).fetchone()
        assigned_id = row[0] if row else None
        return log.model_copy(update={"id": assigned_id})


def list_for_range(
    db: DuckDBManager,
    start: date_t,
    end: date_t,
    log_type: str | None = None,
    category: str | None = None,
) -> list[ManualLog]:
    columns = ", ".join(_FIELDS)
    sql = f"SELECT {columns} FROM manual_logs WHERE date >= ? AND date <= ?"
    params: list = [start, end]
    if log_type is not None:
        sql += " AND log_type = ?"
        params.append(log_type)
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY date ASC, timestamp ASC"
    rows = db.execute(sql, params).fetchall()
    return [ManualLog.model_validate(dict(zip(_FIELDS, r))) for r in rows]
