"""Schema migration runner.

Loads schema.sql, executes idempotent CREATE / ALTER statements, records the
applied version in schema_version, and runs version-gated post-DDL fixups
(e.g., the v3 sequence resync that heals existing desynced databases).

0.9.9 — emits an INFO log line whenever a schema-version transition
actually happens, so ``docker logs ursa-oscar-api`` is sufficient to
confirm a migration ran on first-boot at a new version. No log line
when re-running at the same version (idempotent path stays quiet).
"""
from __future__ import annotations

import logging
import time
from importlib.resources import files

from .db import DuckDBManager

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 6  # v6 (2026-05-16): per-session pressure-stat cache (15 cols)


_VERSION_DESCRIPTIONS = {
    1: "Initial DDL",
    2: "Device-Settings expansion (+9 columns on nightly_summary)",
    3: "id columns DEFAULT nextval(); resync sequences with MAX(id)+1 of existing rows",
    4: "Phase 4 Ticket 1: sessions + excluded_sessions tables; backfill sessions from nightly_events",
    5: "Phase 4 Ticket 2: import_jobs queue table for the in-process async worker",
    6: "Phase 5.5: per-session pressure-stat cache (15 cols on sessions); auto-backfill from timeseries",
}


# Tables that carry an autoincrementing surrogate id paired with a sequence.
# Order matters only for readability — the resync logic is per-table.
_SEQUENCED_TABLES: tuple[tuple[str, str], ...] = (
    ("nightly_events", "nightly_events_id_seq"),
    ("manual_logs",    "manual_logs_id_seq"),
    ("import_log",     "import_log_id_seq"),
    # 0.8.0 — import_jobs queue, same sequence-resync invariant.
    ("import_jobs",    "import_jobs_id_seq"),
)


def _read_schema_sql() -> str:
    """Read schema.sql packaged alongside this module."""
    return (files(__package__) / "schema.sql").read_text(encoding="utf-8")


def apply_migrations(db: DuckDBManager) -> int:
    """Apply migrations up to SCHEMA_VERSION. Returns the version now in force.

    Idempotent — running twice is a no-op once at the target version.

    0.9.9: emits an INFO log line on actual version transitions
    (``Schema migrated vN -> vM``) and on non-trivial backfill row
    counts. Repeated invocations at the same SCHEMA_VERSION are silent
    so server-restart logs stay clean.
    """
    if db.read_only:
        raise RuntimeError("apply_migrations called on a read-only DB connection")

    # 0.9.9 — capture the BEFORE-version so we can log only when a real
    # transition happens. On a fresh DB this is 0; on an existing DB at
    # the target version this is SCHEMA_VERSION (and we'll stay silent).
    before_version = _read_schema_version_safe(db)
    t_start = time.perf_counter()

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

        # v6 post-DDL fixup: backfill per-session pressure stats for any
        # sessions row where the cache is empty. Idempotent — skips rows
        # where pressure_median is already set, so re-running
        # apply_migrations is a no-op once everything's filled. Delegates
        # to the shared helper so the importer's per-night invocation
        # uses identical computation logic.
        #
        # DuckDBManager uses an RLock, so nesting `db.serialized()` inside
        # this outer `with db.serialized() as conn:` is safe (re-entrant).
        # quantile_cont is DuckDB-native + fast; for the operator's ~30
        # nights × ~2 sessions × 4 channels it completes in well under a
        # second. For a multi-year archive (1000+ nights × 2-3 sessions)
        # expect a few seconds on first 0.9.8 startup, then never again.
        backfill_touched = backfill_session_pressure_stats(db)

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

    # 0.9.9 — emit observability log lines, but only when something
    # actually changed.
    elapsed_s = time.perf_counter() - t_start
    if before_version < SCHEMA_VERSION:
        description = _VERSION_DESCRIPTIONS.get(SCHEMA_VERSION, "")
        logger.info(
            "Schema migrated v%d -> v%d in %.2fs%s",
            before_version, SCHEMA_VERSION, elapsed_s,
            f" — {description}" if description else "",
        )
    if backfill_touched > 0:
        # The v6 backfill (and any future post-DDL backfill that uses
        # the same helper) reports per-row work to operator-visible logs.
        # Zero-row backfills stay quiet so server restarts don't spam.
        logger.info(
            "Backfilled per-session pressure stats for %d session row(s)",
            backfill_touched,
        )

    return SCHEMA_VERSION


def _read_schema_version_safe(db: DuckDBManager) -> int:
    """Best-effort current-version lookup. Returns 0 if the
    schema_version table doesn't exist yet (fresh DB) — that's the
    expected first-boot case, not an error."""
    try:
        with db.serialized() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version"
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def current_version(db: DuckDBManager) -> int:
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
    return row[0] if row else 0


# -------------------------------------------------------------------------
# v6 backfill helper.
# -------------------------------------------------------------------------


def backfill_session_pressure_stats(
    db: DuckDBManager,
    date_filter=None,
) -> int:
    """Populate the 15 pressure-stat columns for any sessions row where
    pressure_median IS NULL. Returns the number of rows touched.

    Called from apply_migrations (no date filter — backfill everything
    that's missing), from the importer (with a single-date filter so
    only that night's sessions get computed), and from the standalone
    ``backend/scripts/backfill_session_pressure.py`` operator script.
    Fully idempotent — skips rows where pressure_median is already set,
    so re-running is a no-op once everything's filled.

    DuckDBManager uses an RLock, so calling this from inside
    apply_migrations' already-held serialized() context is safe.
    """
    # Local import to avoid a circular import at module load (sessions
    # repo imports the Session domain model which imports… nothing
    # circular today, but keeps the dep arrow one-way).
    from .repositories import sessions as sessions_repo

    where = "WHERE pressure_median IS NULL"
    params: list = []
    if date_filter is not None:
        where += " AND date = ?"
        params.append(date_filter)

    with db.serialized() as conn:
        rows = conn.execute(
            f"SELECT date, session_id, start_ts, end_ts FROM sessions {where} "
            "ORDER BY date, session_id",
            params if params else None,
        ).fetchall()

    if not rows:
        return 0

    touched = 0
    for date, session_id, start_ts, end_ts in rows:
        stats = sessions_repo.compute_pressure_stats(
            db, date, start_ts, end_ts,
        )
        sessions_repo.set_pressure_stats(db, date, session_id, stats)
        touched += 1
    return touched
