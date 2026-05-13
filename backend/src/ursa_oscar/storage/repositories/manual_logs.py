"""Repository for manual_logs rows.

Phase 3 work; Phase 1 stubs the CRUD surface so the FastAPI / MCP layers can
reference it without import errors.
"""
from __future__ import annotations

from datetime import date as date_t

from ...models.domain import ManualLog
from ..db import DuckDBManager


_FIELDS = (
    "id", "date", "log_type", "timestamp", "value_text", "value_numeric",
    "unit", "category", "notes", "last_updated",
)


def insert(db: DuckDBManager, log: ManualLog) -> ManualLog:
    if db.read_only:
        raise RuntimeError("manual_logs.insert called on a read-only DB connection")
    # nextval() + INSERT must share one locked window so concurrent inserts
    # don't allocate the same id.
    with db.serialized() as conn:
        lid = log.id
        if lid is None:
            lid = conn.execute("SELECT nextval('manual_logs_id_seq')").fetchone()[0]
        payload = log.model_dump()
        payload["id"] = lid
        values = tuple(payload[f] for f in _FIELDS)
        columns = ", ".join(_FIELDS)
        placeholders = ", ".join(["?"] * len(_FIELDS))
        conn.execute(
            f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders})",
            values,
        )
        return log.model_copy(update={"id": lid})


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
