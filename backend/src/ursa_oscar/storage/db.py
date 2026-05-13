"""DuckDB connection management.

Single-writer pattern (Design v1.1 Decision 2): only the API container opens
the DB read+write. The MCP container is now an API proxy (ADR-003) so it
doesn't open the file at all. The watcher signals the API to write rather
than writing itself.

DuckDB's Python wrapper is not thread-safe — two concurrent calls against
the same connection corrupt the result iterator. FastAPI runs sync handlers
on a threadpool, so all DB access in this process goes through a single
`threading.RLock` (`_lock`) held by `DuckDBManager`. `execute()` acquires
the lock, runs the statement, **materializes** the result into a list, then
releases — so callers can read .fetchone()/.fetchall() outside the lock
without racing another in-flight query. Multi-statement paths (transactions,
bulk loads with auto-increment loops) use `db.serialized()` to hold the
lock across the whole sequence.

Single-user homelab traffic absorbs the serialization cheaply: queries are
10ms–2s, throughput cap is "one query at a time" which is exactly the safe
upper bound on a non-thread-safe connection anyway.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb


class _MaterializedResult:
    """List-backed stand-in for duckdb.DuckDBPyConnection's result.

    `DuckDBManager.execute()` consumes the underlying cursor inside the
    manager's RLock before returning this wrapper, so callers can hold the
    result across multiple .fetchone()/.fetchall() calls without racing
    other in-flight queries against the shared connection.

    Implements just the fetch API the repos actually use.
    """

    __slots__ = ("_rows", "_idx")

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self._idx = 0

    def fetchone(self) -> tuple | None:
        if self._idx >= len(self._rows):
            return None
        r = self._rows[self._idx]
        self._idx += 1
        return r

    def fetchall(self) -> list[tuple]:
        rest = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rest


class DuckDBManager:
    """Wraps a single DuckDB connection with explicit mode flags + RLock.

    Use one instance per service container. Production wiring is in main.py
    (creates a write connection); MCP server is API-proxy-only and doesn't
    instantiate this.
    """

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self._path = Path(path)
        self._read_only = read_only
        self._conn: duckdb.DuckDBPyConnection | None = None
        # Serializes ALL DB access from this process. RLock so a single
        # thread can nest calls (e.g. db.serialized() then db.execute()).
        # See module docstring for the threading rationale.
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def read_only(self) -> bool:
        return self._read_only

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Open the connection if not already open. Idempotent.

        The raw connection returned here is NOT thread-safe. Direct callers
        must hold `self._lock` for the full duration of any SQL they run
        against it — use `db.serialized()` to scope that.
        """
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(
                database=str(self._path),
                read_only=self._read_only,
            )
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def serialized(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Hold the DB lock for a multi-statement block.

        Use this when you need to issue several statements against the same
        connection atomically with respect to other threads — transactions,
        bulk loads with auto-increment lookups, schema migrations.
        """
        with self._lock:
            yield self.connect()

    # Backwards-compatible single-statement API.
    #
    # We acquire the lock, run the query, eagerly drain the cursor into a
    # list, then release. The returned _MaterializedResult mimics
    # .fetchone()/.fetchall(), so existing call sites keep working without
    # change. Critically, by the time the caller reads results the cursor
    # is already gone, so a concurrent execute() can't corrupt it.
    def execute(self, sql: str, params: tuple | list | None = None) -> _MaterializedResult:
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(sql) if params is None else conn.execute(sql, params)
            try:
                rows = cursor.fetchall()
            except duckdb.InvalidInputException:
                # Statement returned no result set (some DDL paths).
                rows = []
            return _MaterializedResult(rows)

    def executemany(self, sql: str, rows: list[tuple] | list[list]) -> duckdb.DuckDBPyConnection:
        with self._lock:
            return self.connect().executemany(sql, rows)
