"""Repository for manual_logs rows.

Phase 3 Item 3A/3B: the repository now operates on the discriminated-union
typed log entries from ``models.manual_logs`` (MedicationLog, SymptomLog,
AlertnessLog, SleepEnvironmentLog, FreeformLog). Each typed entry knows
how to serialize itself into the generic ``manual_logs`` row shape via
``to_storage_dict()``; ``from_storage_row()`` reconstructs the typed
model on read.

Old call sites that referenced the legacy generic ``ManualLog`` shape
continue to work via the back-compat ``insert_legacy()`` shim. New code
should use ``insert()`` (typed entry in, typed entry out) and
``list_for_range()`` (returns a list of typed entries).
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime, timezone
from typing import Any

from ...models.domain import ManualLog
from ...models.manual_logs import _ManualLogBase, from_storage_row
from ..db import DuckDBManager


# Columns selected on read. id first so SELECT * order is stable across queries.
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


def insert(db: DuckDBManager, entry: _ManualLogBase) -> _ManualLogBase:
    """Insert a typed manual-log entry, returning the entry with id +
    last_updated populated from the DB-assigned values.

    `entry` is one of the five discriminated subclasses from
    ``models.manual_logs``. The dispatch on log_type happens via the
    entry's own ``to_storage_dict()`` — the repository stays type-agnostic
    at the row layer.
    """
    if db.read_only:
        raise RuntimeError("manual_logs.insert called on a read-only DB connection")

    row = entry.to_storage_dict()
    # Stamp last_updated server-side (same discipline as nights.upsert
    # in Phase 3 Item 1B). Eliminates the "in-memory model carries None,
    # gets written as NULL, suppresses column DEFAULT" footgun.
    now = datetime.now(timezone.utc)
    row["last_updated"] = now

    columns = ", ".join(_INSERT_FIELDS)
    placeholders = ", ".join(["?"] * len(_INSERT_FIELDS))
    values = tuple(row[f] for f in _INSERT_FIELDS)

    with db.serialized() as conn:
        result = conn.execute(
            f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders}) RETURNING id",
            values,
        ).fetchone()
        assigned_id = result[0] if result else None
        return entry.model_copy(update={"id": assigned_id, "last_updated": now})


def insert_legacy(db: DuckDBManager, log: ManualLog) -> ManualLog:
    """Back-compat insert for the generic ManualLog shape.

    Lets older callers (and any future SQL-style consumers) write directly
    against the row schema without going through one of the typed
    discriminated-union models. New code should prefer ``insert(entry)``.
    """
    if db.read_only:
        raise RuntimeError("manual_logs.insert_legacy called on a read-only DB connection")
    payload = log.model_dump()
    # Same server-side last_updated stamp.
    payload["last_updated"] = datetime.now(timezone.utc)

    with db.serialized() as conn:
        if log.id is not None:
            columns = "id, " + ", ".join(_INSERT_FIELDS)
            placeholders = "?, " + ", ".join(["?"] * len(_INSERT_FIELDS))
            values = (log.id, *(payload[f] for f in _INSERT_FIELDS))
            conn.execute(
                f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders})",
                values,
            )
            return log.model_copy(update={"id": log.id, "last_updated": payload["last_updated"]})
        columns = ", ".join(_INSERT_FIELDS)
        placeholders = ", ".join(["?"] * len(_INSERT_FIELDS))
        values = tuple(payload[f] for f in _INSERT_FIELDS)
        row = conn.execute(
            f"INSERT INTO manual_logs ({columns}) VALUES ({placeholders}) RETURNING id",
            values,
        ).fetchone()
        assigned_id = row[0] if row else None
        return log.model_copy(update={"id": assigned_id, "last_updated": payload["last_updated"]})


def get_by_id(db: DuckDBManager, log_id: int) -> _ManualLogBase | None:
    """Return the typed log entry for a given id, or None if not found."""
    columns = ", ".join(_SELECT_FIELDS)
    row = db.execute(
        f"SELECT {columns} FROM manual_logs WHERE id = ?",
        (log_id,),
    ).fetchone()
    if row is None:
        return None
    return from_storage_row(dict(zip(_SELECT_FIELDS, row)))


def list_for_range(
    db: DuckDBManager,
    start: date_t,
    end: date_t,
    log_type: str | None = None,
    category: str | None = None,
) -> list[_ManualLogBase]:
    """List typed log entries in [start, end] (inclusive), optionally
    filtered by log_type and/or category. Returns a list of typed entries
    (the discriminated-union shape from ``models.manual_logs``).
    """
    columns = ", ".join(_SELECT_FIELDS)
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
    return [from_storage_row(dict(zip(_SELECT_FIELDS, r))) for r in rows]


def update_partial(
    db: DuckDBManager,
    log_id: int,
    patch: dict[str, Any],
) -> _ManualLogBase | None:
    """Partial update of a manual_logs row by id.

    `patch` is a dict of column-name -> new value for the storage-shape
    columns (value_text, value_numeric, unit, category, notes, timestamp,
    date). Caller is responsible for translating typed-model field names
    into row-shape columns (e.g., a MedicationLog's `dose` patch maps to
    `value_numeric`). Returns the post-update typed entry, or None if the
    id didn't exist.
    """
    if db.read_only:
        raise RuntimeError("manual_logs.update_partial called on a read-only DB connection")
    if not patch:
        # No-op; just fetch and return current state.
        return get_by_id(db, log_id)

    # Always bump last_updated.
    patch_with_audit = {**patch, "last_updated": datetime.now(timezone.utc)}
    set_clause = ", ".join(f"{col} = ?" for col in patch_with_audit)
    sql = f"UPDATE manual_logs SET {set_clause} WHERE id = ?"
    values = (*patch_with_audit.values(), log_id)

    with db.serialized() as conn:
        conn.execute(sql, values)

    return get_by_id(db, log_id)


def delete_by_id(db: DuckDBManager, log_id: int) -> bool:
    """Remove a manual_logs row by id. Returns True if a row was deleted,
    False if the id didn't exist.
    """
    if db.read_only:
        raise RuntimeError("manual_logs.delete_by_id called on a read-only DB connection")
    with db.serialized() as conn:
        result = conn.execute(
            "DELETE FROM manual_logs WHERE id = ? RETURNING id",
            (log_id,),
        ).fetchone()
    return result is not None
