"""Repository for the async-import job queue (`import_jobs` table).

Phase 4 Ticket 2 — durable backing store for the in-process import worker.
Each import — whether path-based or folder-upload — lands here in status
'queued'. The worker (an asyncio task started at app lifespan) picks the
oldest queued row, flips status to 'running', invokes import_path(), then
writes status to 'completed' or 'failed' with the ImportLogEntry serialized
into result_json or an error_message respectively.

All write operations wrap a `db.serialized()` block so the API endpoints
and the worker don't race each other on the same row.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ...models.domain import ImportJob, ImportJobStatus
from ..db import DuckDBManager


_FIELDS = (
    "id", "status", "source_path", "upload_dir", "force_reimport",
    "created_at", "started_at", "completed_at",
    "result_json", "error_message",
)


def enqueue(
    db: DuckDBManager,
    *,
    source_path: Optional[str] = None,
    upload_dir: Optional[str] = None,
    force_reimport: bool = False,
) -> ImportJob:
    """Insert a new job in 'queued' status. Exactly one of source_path or
    upload_dir must be set. Returns the row with the DB-assigned id."""
    if (source_path is None) == (upload_dir is None):
        raise ValueError("Exactly one of source_path / upload_dir must be set")
    if db.read_only:
        raise RuntimeError("import_jobs.enqueue called on a read-only DB")
    with db.serialized() as conn:
        row = conn.execute(
            """
            INSERT INTO import_jobs (status, source_path, upload_dir, force_reimport)
            VALUES ('queued', ?, ?, ?)
            RETURNING id, status, source_path, upload_dir, force_reimport,
                      created_at, started_at, completed_at, result_json, error_message
            """,
            (source_path, upload_dir, force_reimport),
        ).fetchone()
    return _row_to_model(row)


def claim_next_queued(db: DuckDBManager) -> Optional[ImportJob]:
    """Atomically transition the oldest queued job to 'running' and return
    it. Returns None if no queued jobs exist. The single-writer DuckDB
    pattern + serialized() lock make this safe — no two workers can claim
    the same row, though we only run one worker today regardless.
    """
    if db.read_only:
        raise RuntimeError("import_jobs.claim_next_queued called on a read-only DB")
    with db.serialized() as conn:
        row = conn.execute(
            """
            SELECT id FROM import_jobs
             WHERE status = 'queued'
             ORDER BY created_at ASC
             LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        job_id = row[0]
        # Stamp started_at server-side so it reflects when the worker
        # actually picked up, not when the API enqueued.
        conn.execute(
            """
            UPDATE import_jobs
               SET status = 'running', started_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (job_id,),
        )
        updated = conn.execute(
            f"""
            SELECT {', '.join(_FIELDS)}
              FROM import_jobs
             WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    return _row_to_model(updated)


def mark_completed(
    db: DuckDBManager,
    job_id: int,
    result: dict,
) -> None:
    """Worker calls this after a successful import_path() invocation.
    `result` is the ImportLogEntry as a plain dict (model_dump)."""
    if db.read_only:
        raise RuntimeError("import_jobs.mark_completed called on a read-only DB")
    payload = json.dumps(result, default=_json_default)
    with db.serialized() as conn:
        conn.execute(
            """
            UPDATE import_jobs
               SET status = 'completed',
                   completed_at = CURRENT_TIMESTAMP,
                   result_json = ?
             WHERE id = ?
            """,
            (payload, job_id),
        )


def mark_failed(db: DuckDBManager, job_id: int, error: str) -> None:
    """Worker calls this when import_path() raises. `error` is a short
    diagnostic string — full traceback goes to container stderr."""
    if db.read_only:
        raise RuntimeError("import_jobs.mark_failed called on a read-only DB")
    with db.serialized() as conn:
        conn.execute(
            """
            UPDATE import_jobs
               SET status = 'failed',
                   completed_at = CURRENT_TIMESTAMP,
                   error_message = ?
             WHERE id = ?
            """,
            (error[:1000], job_id),  # cap the message length defensively
        )


def mark_orphaned_on_startup(db: DuckDBManager) -> int:
    """Called once at API startup. Any row left in 'running' across a
    restart is treated as orphaned — the worker that owned it is gone,
    and we don't know whether the import committed or rolled back. The
    operator sees the row in the UI and can decide what to do.

    Returns the count of orphaned rows so the lifespan handler can log it.
    """
    if db.read_only:
        return 0
    with db.serialized() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM import_jobs WHERE status = 'running'"
        ).fetchone()[0]
        if before == 0:
            return 0
        conn.execute(
            """
            UPDATE import_jobs
               SET status = 'orphaned',
                   completed_at = CURRENT_TIMESTAMP,
                   error_message = 'API restarted while this job was running'
             WHERE status = 'running'
            """
        )
    return int(before or 0)


def get(db: DuckDBManager, job_id: int) -> Optional[ImportJob]:
    """Single-row lookup by id. Returns None on miss."""
    with db.serialized() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_FIELDS)} FROM import_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _row_to_model(row) if row else None


def list_jobs(
    db: DuckDBManager,
    *,
    status: Optional[ImportJobStatus] = None,
    limit: int = 50,
) -> list[ImportJob]:
    """List recent jobs, newest first. Optional status filter — the
    Import page calls this twice: once with status filter for the
    'Active jobs' section (queued|running), once unfiltered for the
    'Recent imports' tail."""
    where = ""
    params: tuple = ()
    if status is not None:
        where = "WHERE status = ?"
        params = (status,)
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT {', '.join(_FIELDS)}
              FROM import_jobs
              {where}
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
    return [_row_to_model(r) for r in rows]


def list_active(db: DuckDBManager) -> list[ImportJob]:
    """Shortcut — returns rows with status in (queued, running),
    oldest first. The UI's 'Active jobs' section consumes this."""
    with db.serialized() as conn:
        rows = conn.execute(
            f"""
            SELECT {', '.join(_FIELDS)}
              FROM import_jobs
             WHERE status IN ('queued', 'running')
             ORDER BY created_at ASC
            """
        ).fetchall()
    return [_row_to_model(r) for r in rows]


# --- helpers ---------------------------------------------------------------


def _row_to_model(row) -> ImportJob:
    if row is None:
        return None  # type: ignore  # narrowed by callers
    data = dict(zip(_FIELDS, row))
    # DuckDB returns JSON columns as already-parsed dicts in some drivers;
    # in others they come back as raw strings. Coerce to dict either way.
    rj = data.get("result_json")
    if isinstance(rj, str):
        try:
            data["result_json"] = json.loads(rj)
        except (json.JSONDecodeError, TypeError):
            data["result_json"] = None
    return ImportJob.model_validate(data)


def _json_default(o):
    """JSON encoder hook for non-serializable types in ImportLogEntry —
    dates and datetimes mostly. Falls back to str(o) for anything
    unexpected so a stray value never blows up the worker."""
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)
