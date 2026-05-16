"""Unit tests for ``storage.migrations``.

0.9.9 — covers the observability logging added in apply_migrations and
the backfill-row-count surface. Pre-0.9.9, schema transitions ran
silently — operators had to probe the DB to confirm migrations had
landed. These tests lock the log lines down so future bumps stay
observable from ``docker logs ursa-oscar-api`` alone.
"""
from __future__ import annotations

import logging

import pytest

from ursa_oscar.storage import migrations
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import (
    SCHEMA_VERSION,
    _read_schema_version_safe,
    apply_migrations,
)


def test_apply_migrations_logs_transition_on_fresh_db(tmp_path, caplog):
    """Fresh DB goes 0 -> SCHEMA_VERSION. Expect a single
    'Schema migrated v0 -> v<N>' log line at INFO."""
    db = DuckDBManager(tmp_path / "fresh.duckdb", read_only=False)
    try:
        with caplog.at_level(logging.INFO, logger=migrations.__name__):
            v = apply_migrations(db)
        assert v == SCHEMA_VERSION

        transition_records = [
            r for r in caplog.records
            if r.name == migrations.__name__
            and r.getMessage().startswith(f"Schema migrated v0 -> v{SCHEMA_VERSION}")
        ]
        assert len(transition_records) == 1, (
            f"expected one schema-transition log; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
    finally:
        db.close()


def test_apply_migrations_is_silent_on_no_op(tmp_path, caplog):
    """Re-running apply_migrations at the same SCHEMA_VERSION emits NO
    'Schema migrated' log line — operators shouldn't see migration
    spam on every API restart."""
    db_path = tmp_path / "noop.duckdb"
    db = DuckDBManager(db_path, read_only=False)
    apply_migrations(db)  # first run brings to SCHEMA_VERSION
    db.close()

    db = DuckDBManager(db_path, read_only=False)
    try:
        with caplog.at_level(logging.INFO, logger=migrations.__name__):
            apply_migrations(db)
        transition_records = [
            r for r in caplog.records
            if r.name == migrations.__name__
            and "Schema migrated" in r.getMessage()
        ]
        assert transition_records == [], (
            f"no-op apply_migrations should not log a transition; got "
            f"{[r.getMessage() for r in transition_records]}"
        )
    finally:
        db.close()


def test_apply_migrations_logs_backfill_row_count_when_nonzero(tmp_path, caplog):
    """When the v6 backfill helper touches sessions rows, emit an INFO
    line with the count. Lets the operator see 'X rows backfilled'
    from container logs without DB probing."""
    db = DuckDBManager(tmp_path / "backfill.duckdb", read_only=False)
    try:
        # Fresh DB. Insert a synthetic session row + matching timeseries
        # data so the v6 backfill helper has something to do.
        apply_migrations(db)  # brings schema to v6
        with db.serialized() as conn:
            conn.execute(
                "INSERT INTO sessions (date, session_id, start_ts, end_ts, mask_on_minutes) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2026-05-01", 1,
                 "2026-05-01 22:00:00", "2026-05-02 06:00:00", 480.0),
            )
            # NULL pressure_median makes this row eligible for backfill.
            # Add a couple of pressure-timeseries rows in the session window
            # so quantile_cont produces a real value.
            for hh in (22, 23):
                conn.execute(
                    "INSERT INTO pressure_timeseries (date, timestamp, pressure, epap) "
                    "VALUES (?, ?, ?, ?)",
                    ("2026-05-01", f"2026-05-01 {hh}:30:00", 8.0, 5.0),
                )

        # Run apply_migrations again — fresh sessions row should get
        # backfilled, and the log line should fire.
        with caplog.at_level(logging.INFO, logger=migrations.__name__):
            apply_migrations(db)

        backfill_records = [
            r for r in caplog.records
            if r.name == migrations.__name__
            and "Backfilled per-session pressure stats" in r.getMessage()
        ]
        assert len(backfill_records) == 1, (
            f"expected one backfill log entry; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert "1 session row(s)" in backfill_records[0].getMessage()
    finally:
        db.close()


def test_apply_migrations_silent_when_backfill_finds_no_work(tmp_path, caplog):
    """No rows needing backfill -> no log line. Server restart on an
    already-backfilled DB stays quiet."""
    db_path = tmp_path / "nothing.duckdb"
    db = DuckDBManager(db_path, read_only=False)
    apply_migrations(db)  # fresh DB, no sessions rows -> 0 backfilled
    db.close()

    db = DuckDBManager(db_path, read_only=False)
    try:
        with caplog.at_level(logging.INFO, logger=migrations.__name__):
            apply_migrations(db)
        backfill_records = [
            r for r in caplog.records
            if r.name == migrations.__name__
            and "Backfilled per-session pressure stats" in r.getMessage()
        ]
        assert backfill_records == []
    finally:
        db.close()


def test_read_schema_version_safe_returns_zero_on_fresh_db(tmp_path):
    """The pre-migration version lookup must NOT raise when the
    schema_version table doesn't exist yet — fresh DBs are the
    expected first-boot case."""
    db = DuckDBManager(tmp_path / "raw.duckdb", read_only=False)
    try:
        assert _read_schema_version_safe(db) == 0
    finally:
        db.close()
