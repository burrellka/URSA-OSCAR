"""Repository for *_timeseries tables.

Generic API across the eight time-series tables — caller picks the table name.
The high-resolution flow timeseries can be large (25 Hz × 8 hours ≈ 720k rows
per night); use chunked insertion for that table.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime

import pandas as pd

from ..db import DuckDBManager


# Map of public series name → (table, value column [, secondary value column])
SERIES_SCHEMA: dict[str, tuple[str, str, str | None]] = {
    "pressure": ("pressure_timeseries", "pressure", "epap"),
    "flow": ("flow_timeseries", "flow_rate", None),
    "leak": ("leak_timeseries", "leak_rate", None),
    "flow_limit": ("flow_limit_timeseries", "flow_limit", None),
    "tidal_volume": ("tidal_volume_timeseries", "tidal_volume", None),
    "minute_vent": ("minute_vent_timeseries", "minute_vent", None),
    "resp_rate": ("resp_rate_timeseries", "resp_rate", None),
    "snore": ("snore_timeseries", "snore", None),
}


def bulk_insert(
    db: DuckDBManager,
    series: str,
    rows: list[tuple[date_t, datetime, float] | tuple[date_t, datetime, float, float]],
) -> int:
    """Insert rows into a time-series table.

    `rows` must contain tuples of length 3 (date, timestamp, value) for
    single-value tables, or length 4 (date, timestamp, value, secondary) for
    the pressure table.

    Uses DuckDB's appender API rather than SQL executemany — the appender
    skips per-row SQL parsing and writes directly via the binary protocol.
    On 14k-row PLD batches this is ~50ms vs ~3s for executemany, which is
    the difference between a 4-night import completing in <60s vs hitting a
    5-minute API timeout.
    """
    if db.read_only:
        raise RuntimeError("timeseries.bulk_insert called on a read-only DB connection")
    if series not in SERIES_SCHEMA:
        raise ValueError(f"Unknown series '{series}'. Valid: {list(SERIES_SCHEMA)}")
    if not rows:
        return 0

    table, value_col, secondary = SERIES_SCHEMA[series]

    # Build a pandas DataFrame in column order matching the table; DuckDB
    # ingests this directly via INSERT-SELECT against the local variable
    # name (it auto-registers Python objects referenced in SQL by name).
    # ~30x faster than executemany on 14k-row PLD batches because the
    # binary protocol skips per-row SQL parsing.
    #
    # The INSERT-SELECT is held inside db.serialized() so the local-variable
    # auto-registration window can't be invalidated by a concurrent
    # execute() against the same connection.
    columns = ["date", "timestamp", value_col]
    if secondary:
        columns.append(secondary)
    ursa_oscar_ts_df = pd.DataFrame.from_records(rows, columns=columns)

    with db.serialized() as conn:
        conn.execute(f"INSERT INTO {table} SELECT * FROM ursa_oscar_ts_df")
    return len(rows)


def delete_for_date(db: DuckDBManager, series: str, target: date_t) -> None:
    """Used by re-import to truncate a series for a given night."""
    if db.read_only:
        raise RuntimeError("timeseries.delete_for_date called on a read-only DB connection")
    if series not in SERIES_SCHEMA:
        raise ValueError(f"Unknown series '{series}'. Valid: {list(SERIES_SCHEMA)}")
    table, _, _ = SERIES_SCHEMA[series]
    db.execute(f"DELETE FROM {table} WHERE date = ?", (target,))


def range_query(
    db: DuckDBManager,
    series: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple]:
    """Return raw rows for charting. Each row: (timestamp, value [, secondary])."""
    if series not in SERIES_SCHEMA:
        raise ValueError(f"Unknown series '{series}'. Valid: {list(SERIES_SCHEMA)}")
    table, value_col, secondary = SERIES_SCHEMA[series]
    if secondary:
        sql = (
            f"SELECT timestamp, {value_col}, {secondary} FROM {table} "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC"
        )
    else:
        sql = (
            f"SELECT timestamp, {value_col} FROM {table} "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC"
        )
    return db.execute(sql, (start_ts, end_ts)).fetchall()
