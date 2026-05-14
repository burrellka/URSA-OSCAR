"""Async import worker.

Phase 4 Ticket 2 — long-running imports (especially the multi-hundred-MB
folder uploads from 0.6.x) shouldn't block HTTP requests. The worker is
an in-process asyncio task started at app lifespan; it polls the
import_jobs table for queued work, processes one job at a time, and
writes the result back to the same row.

Why in-process rather than Celery / Redis / external broker:
- Single operator, single API container — no need to scale out
- Import_path() is CPU-bound (EDF parse + numpy), not IO-bound, so a
  worker process inside the API container is fine; we just run it in
  a thread pool so it doesn't block the asyncio event loop
- DuckDB is the durable backing store anyway; reusing it for job state
  avoids introducing another service to operate

Concurrency: ONE worker, ONE job at a time. DuckDB's single-writer
invariant means parallel imports against the same file would serialize
through the writer lock anyway. Explicit serialization here lets us
surface "queued" vs "running" clearly in the UI.

Failure semantics:
- Worker exceptions land in mark_failed() with a short error string;
  full traceback streams to container stderr.
- API restarts mid-import → row stays in 'running'. mark_orphaned_on_startup
  flips those to 'orphaned' so the operator sees them and can decide
  whether the import actually committed (DuckDB transactions are
  atomic per night) or needs a retry.
- Worker task crashes → the lifespan supervisor logs it and the next
  startup re-arms a fresh worker. Queued jobs accumulate until then.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import traceback
from pathlib import Path
from typing import Optional

from ..ingestion.airsense11_layout import locate_import_root
from ..ingestion.importer import import_path
from ..storage.db import DuckDBManager
from ..storage.repositories import import_jobs as jobs_repo

logger = logging.getLogger(__name__)


# Poll interval for the queued-job check loop. 1s is fast enough for
# the UI to feel responsive (the operator sees 'queued' → 'running'
# within ~1s of submitting) without busy-spinning DuckDB.
_POLL_INTERVAL_SECONDS = 1.0


class ImportWorker:
    """Lifespan-managed async import processor.

    Usage from main.py's lifespan:

        worker = ImportWorker(db)
        worker.start()
        app.state.import_worker = worker
        try:
            yield
        finally:
            await worker.stop()
    """

    def __init__(self, db: DuckDBManager) -> None:
        self._db = db
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Spawn the worker task. Safe to call once per app lifecycle."""
        if self._task is not None and not self._task.done():
            return
        # Mark any 'running' rows from a previous lifecycle as 'orphaned'
        # so they don't sit forever waiting on a worker that's gone.
        n_orphaned = jobs_repo.mark_orphaned_on_startup(self._db)
        if n_orphaned > 0:
            logger.warning(
                "ImportWorker.start: marked %d running job(s) as orphaned "
                "(API restarted while they were in flight)",
                n_orphaned,
            )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="import-worker")
        logger.info("ImportWorker started")

    async def stop(self) -> None:
        """Signal the worker to exit and await its task. Times out at
        30s — a hung import_path() shouldn't block container shutdown
        indefinitely. If we do time out, the in-flight job stays in
        'running' and gets orphaned on the next start."""
        self._stop_event.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "ImportWorker.stop: worker didn't exit within 30s; "
                "cancelling. In-flight job will be marked orphaned next startup."
            )
            self._task.cancel()
        self._task = None
        logger.info("ImportWorker stopped")

    async def _run(self) -> None:
        """Main loop: poll for queued jobs, process each one, exit when stop_event fires."""
        while not self._stop_event.is_set():
            try:
                job = jobs_repo.claim_next_queued(self._db)
            except Exception:
                logger.exception("ImportWorker: claim_next_queued failed; retrying")
                await self._sleep_or_stop(_POLL_INTERVAL_SECONDS)
                continue

            if job is None:
                # No work — sleep until next poll or stop signal.
                await self._sleep_or_stop(_POLL_INTERVAL_SECONDS)
                continue

            await self._process(job)

    async def _process(self, job) -> None:
        """Run one job to completion (or failure). Errors caught and
        logged so the worker loop never dies; the failed row's
        error_message captures the diagnostic for the operator."""
        logger.info(
            "ImportWorker: processing job %d (source=%s upload=%s force=%s)",
            job.id, job.source_path, job.upload_dir, job.force_reimport,
        )
        try:
            # import_path is synchronous + CPU-heavy. Off-load to the
            # default thread pool so the asyncio loop stays responsive
            # for /jobs polling + other HTTP traffic.
            result = await asyncio.to_thread(
                self._run_import_sync, job,
            )
            jobs_repo.mark_completed(self._db, job.id, result)
            logger.info(
                "ImportWorker: job %d completed (status=%s nights_imported=%d)",
                job.id, result.get("status"), result.get("nights_imported", 0),
            )
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("ImportWorker: job %d failed:\n%s", job.id, tb)
            jobs_repo.mark_failed(
                self._db, job.id,
                f"{type(e).__name__}: {e}",
            )
        finally:
            # Clean up the multipart-upload tempdir if the job carried
            # one. Path-based imports point at the operator's mounted
            # source and we never touch those.
            if job.upload_dir:
                try:
                    shutil.rmtree(job.upload_dir, ignore_errors=True)
                except Exception:
                    logger.exception(
                        "ImportWorker: tempdir cleanup failed for %s", job.upload_dir,
                    )

    def _run_import_sync(self, job) -> dict:
        """Synchronous import work — runs in the thread pool."""
        if job.source_path:
            # Path-based: the operator's mounted folder. import_path's
            # own layout detector handles SD-root vs DATALOG/ cases.
            target = Path(job.source_path)
        else:
            # Upload-based: the endpoint wrote multipart files into a
            # tempdir; we still need to find the actual data root
            # inside that tempdir (skip past the webkitdirectory
            # picked-folder wrapper). locate_import_root does the BFS.
            tempdir = Path(job.upload_dir or "")
            target = locate_import_root(tempdir)
            logger.info(
                "ImportWorker: job %d upload_dir=%s, resolved import_root=%s",
                job.id, tempdir, target,
            )
        entry = import_path(
            target, self._db,
            include_timeseries=True,
            skip_existing=not job.force_reimport,
        )
        return entry.model_dump()

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep, but wake up early if stop_event fires. Lets shutdown be
        responsive even on idle polls."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
