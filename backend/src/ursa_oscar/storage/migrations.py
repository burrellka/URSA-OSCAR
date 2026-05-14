"""Schema migration runner.

Loads schema.sql, executes idempotent CREATE / ALTER statements, records the
applied version in schema_version, and runs version-gated post-DDL fixups
(e.g., the v3 sequence resync that heals existing desynced databases).
"""
from __future__ import annotations

from importlib.resources import files

from .db import DuckDBManager


SCHEMA_VERSION = 4  # v4 (2026-05-14): sessions + excluded_sessions; backfill from nightly_events


_VERSION_DESCRIPTIONS = {
    1: "Initial DDL",
    2: "Device-Settings expansion (+9 columns on nightly_summary)",
    3: "id columns DEFAULT nextval(); resync sequences with MAX(id)+1 of existing rows",
    4: "Phase 4 Ticket 1: sessions + excluded_sessions tables; backfill sessions from nightly_events",
}


# Tables that carry an autoincrementing surrogate id paired with a sequence.
# Order matters only for readability — the resync logic is per-table.
_SEQUENCED_TABLES: tuple[tuple[str, str], ...] = (
    ("nightly_events", "nightly_events_id_seq"),
    ("manual_logs",    "manual_logs_id_seq"),
    ("import_log",     "import_log_id_seq"),
)


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
        # Apply DDL. All statements use IF NOT EXISTS / SET DEFAULT so
        # re-running is safe.
        conn.execute(schema_sql)

        # v3 post-DDL fixup: resync each id sequence to MAX(id)+1 of its
        # owning table. Heals existing databases where the sequence drifted
        # below the highest committed row (the bug that caused the
        # 2023-04-24 / 2023-04-25 import collisions on event ids 455/487).
        #
        # DuckDB constraints we navigate here:
        #   - ALTER SEQUENCE RESTART -> NotImplementedException
        #   - DROP SEQUENCE while a column DEFAULT references it ->
        #     DependencyException (DuckDB sees the column as depending
        #     on the sequence)
        # Workaround: temporarily drop the column DEFAULT, drop +
        # recreate the sequence with the right starting value, then
        # re-attach the DEFAULT. The whole block runs inside the
        # serialized lock so no INSERT can race the window where the
        # default is missing. Idempotent: on a fresh DB with empty
        # tables each sequence ends back at START WITH 1.
        for table, seq in _SEQUENCED_TABLES:
            row = conn.execute(
                f"SELECT COALESCE(MAX(id), 0) FROM {table}"
            ).fetchone()
            next_id = (row[0] if row else 0) + 1
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
            conn.execute(f"DROP SEQUENCE IF EXISTS {seq}")
            conn.execute(f"CREATE SEQUENCE {seq} START WITH {int(next_id)}")
            conn.execute(
                f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{seq}')"
            )

        # v4 post-DDL fixup: backfill `sessions` rows for any night already
        # in the DB but missing from the new table. This makes the
        # session-exclusion feature work against 0.6.x databases that
        # never saw the importer's session-row writes. We derive timing
        # from nightly_events' (date, session_id, MIN/MAX(timestamp))
        # — an underestimate of true mask-on (events don't span the
        # full session), but good enough until a future re-import
        # refreshes the row with EDF-derived numbers.
        #
        # Idempotent: insert only where (date, session_id) doesn't
        # already exist. On a fresh DB this runs as a no-op (no rows
        # in nightly_events yet either).
        conn.execute("""
            INSERT INTO sessions (date, session_id, start_ts, end_ts, mask_on_minutes)
            SELECT
                e.date,
                e.session_id,
                MIN(e.timestamp) AS start_ts,
                MAX(e.timestamp) AS end_ts,
                EXTRACT(EPOCH FROM (MAX(e.timestamp) - MIN(e.timestamp))) / 60.0
                    AS mask_on_minutes
            FROM nightly_events e
            WHERE NOT EXISTS (
                SELECT 1 FROM sessions s
                 WHERE s.date = e.date AND s.session_id = e.session_id
            )
              AND e.session_id IS NOT NULL
            GROUP BY e.date, e.session_id
        """)

        # Record version if not present at SCHEMA_VERSION.
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
        current_version = current[0] if current else 0

        if current_version < SCHEMA_VERSION:
            description = _VERSION_DESCRIPTIONS.get(
                SCHEMA_VERSION, f"Schema v{SCHEMA_VERSION}"
            )
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (SCHEMA_VERSION, f"Schema v{SCHEMA_VERSION} — {description}"),
            )

    return SCHEMA_VERSION


def current_version(db: DuckDBManager) -> int:
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
    return row[0] if row else 0
