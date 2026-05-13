"""Schema migration runner.

Loads schema.sql, executes idempotent CREATE statements, records the applied
version in schema_version. Future migrations are appended as additional .sql
files plus version entries.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .db import DuckDBManager


SCHEMA_VERSION = 2  # v2 (2026-05-13): Device-Settings expansion (+9 columns on nightly_summary)


def _read_schema_sql() -> str:
    """Read schema.sql packaged alongside this module."""
    return (files(__package__) / "schema.sql").read_text(encoding="utf-8")


def apply_migrations(db: DuckDBManager) -> int:
    """Apply migrations up to SCHEMA_VERSION. Returns the version now in force.

    Idempotent — running twice is a no-op once at the target version.
    """
    if db.read_only:
        raise RuntimeError("apply_migrations called on a read-only DB connection")

    schema_sql = _read_schema_sql()

    # Held under serialization for safety. Migrations run at startup before
    # the API is accepting traffic, so in practice there's no contention —
    # but using the same idiom as the rest of the codebase keeps the
    # connection-access invariant consistent.
    with db.serialized() as conn:
        # Apply DDL. All statements use IF NOT EXISTS so re-running is safe.
        conn.execute(schema_sql)

        # Record version if not present at SCHEMA_VERSION.
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
        current_version = current[0] if current else 0

        if current_version < SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (
                    SCHEMA_VERSION,
                    f"Schema v{SCHEMA_VERSION} — "
                    + (
                        "initial DDL"
                        if SCHEMA_VERSION == 1
                        else "Device-Settings expansion (+9 columns)"
                    ),
                ),
            )

    return SCHEMA_VERSION


def current_version(db: DuckDBManager) -> int:
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
    return row[0] if row else 0
