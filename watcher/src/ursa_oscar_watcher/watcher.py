"""Phase 4 Ticket 3 — the file-watcher daemon.

Design summary:

  - Poll the watched tree's fingerprint every ``poll_interval_seconds``.
  - When the fingerprint changes, reset a quiescence timer to ``now``.
  - When the fingerprint has been stable for ``quiescence_seconds``,
    POST to the API's now-async ``/imports`` endpoint. The API's
    ``skip_existing`` default deduplicates already-imported nights,
    so the watcher never has to track "what's already in the DB" —
    it just trusts the API to ignore known nights.
  - Track the submitted job_id and poll it on subsequent ticks. When
    the job reaches a terminal state, deliver the optional webhook
    payload and clear the tracker for the next cycle.

Failure modes (caught + logged, daemon stays alive):
  - Watch path disappears (SD card removed) → fingerprint=None,
    skip until it comes back.
  - API unreachable → log; keep polling. Next tick re-tries.
  - Webhook fails → log; keep going. Don't lose data over a bad URL.
  - Job stuck in 'running' past job_wait_timeout_seconds → log,
    abandon the job-tracking and free up for the next trigger.
"""
from __future__ import annotations

import logging
import signal
import time
from typing import Optional

from .api_client import ApiClient
from .config import WatcherConfig
from .fingerprint import Fingerprint, compute_fingerprint

logger = logging.getLogger(__name__)


# Terminal statuses for an import job. Anything else means the worker
# is still chewing on it.
_TERMINAL_STATUSES = {"completed", "failed", "orphaned"}


class Watcher:
    """The daemon. Construct with config + (optionally) a custom
    ApiClient for tests; call ``run()`` to enter the poll loop."""

    def __init__(
        self,
        config: WatcherConfig,
        api: Optional[ApiClient] = None,
        clock=time.monotonic,
    ) -> None:
        self.config = config
        self.api = api or ApiClient(config.api_url)
        self._clock = clock
        # State.
        self._stop = False
        self._last_fingerprint: Optional[Fingerprint] = None
        # Wall-clock-ish (via the injected clock) timestamp of the
        # most recent fingerprint change. None when the tree has been
        # quiet since the last successful import (or since startup).
        self._last_change_at: Optional[float] = None
        # The job_id of the most recently submitted import. We poll it
        # on subsequent ticks until terminal, then fire the webhook.
        self._tracked_job_id: Optional[int] = None
        # When _tracked_job_id was first submitted. Used to enforce
        # job_wait_timeout_seconds and avoid hanging on a stuck job.
        self._tracked_job_started: Optional[float] = None

    # ---- public lifecycle ----

    def run(self) -> None:
        """Main entry. Loops forever until SIGINT/SIGTERM."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        logger.info(
            "watcher running: path=%s api=%s poll=%.0fs quiescence=%.0fs webhook=%s force=%s",
            self.config.watch_path,
            self.config.api_url,
            self.config.poll_interval_seconds,
            self.config.quiescence_seconds,
            "yes" if self.config.webhook_url else "no",
            self.config.force_reimport,
        )

        while not self._stop:
            try:
                self.tick()
            except Exception:
                logger.exception("watcher tick failed; sleeping then retrying")
            self._sleep(self.config.poll_interval_seconds)

        logger.info("watcher exiting cleanly")

    def stop(self) -> None:
        """Set the stop flag. The next poll iteration exits cleanly."""
        self._stop = True

    # ---- one iteration of the loop (also unit-testable in isolation) ----

    def tick(self) -> None:
        """One pass: poll the tracked job (if any), poll the fs,
        maybe trigger an import."""
        # 1) If we have a job in flight, check it. Fires webhook + clears
        #    on terminal status. Doesn't block submitting a NEW job —
        #    if a new change arrives mid-import, the next quiescent
        #    period after this job finishes will pick it up.
        self._poll_tracked_job()

        # 2) Compute fingerprint.
        fp = compute_fingerprint(self.config.watch_path)
        now = self._clock()

        if fp is None:
            # Path is unreachable — typical when the SD card is unmounted.
            # Don't reset state aggressively; the operator may re-plug.
            logger.debug("tick: watch path unreachable; skipping")
            return

        if fp != self._last_fingerprint:
            self._last_fingerprint = fp
            # Only treat a non-empty tree as a change worth importing.
            # An empty tree (just-mounted card with no DATALOG yet,
            # or a debug environment) should never trigger an import —
            # we'd just be enqueueing no-op jobs.
            if len(fp) > 0:
                self._last_change_at = now
                logger.info(
                    "tick: fingerprint changed (%d entries); quiescence timer reset",
                    len(fp),
                )
            else:
                logger.debug("tick: fingerprint empty; not arming quiescence timer")
            return

        # 3) Fingerprint stable. Anything pending?
        if self._last_change_at is None:
            return  # already triggered for this state; waiting for new change

        if now - self._last_change_at < self.config.quiescence_seconds:
            return  # not stable long enough yet

        # 4) Quiescent. Trigger an import (if we don't already have one
        #    in flight — the API would dedup the work anyway via
        #    skip_existing, but two jobs racing through the queue is
        #    wasteful + confusing in the UI).
        if self._tracked_job_id is not None:
            logger.debug(
                "tick: would trigger import but job %d still tracked; deferring",
                self._tracked_job_id,
            )
            return

        self._trigger_import(now)

    # ---- internals ----

    def _trigger_import(self, now: float) -> None:
        logger.info(
            "watcher: tree quiescent — POST /imports source_path=%s force=%s",
            self.config.watch_path, self.config.force_reimport,
        )
        try:
            job = self.api.enqueue_import(
                self.config.watch_path,
                force=self.config.force_reimport,
            )
        except Exception:
            logger.exception("watcher: enqueue_import failed — will retry on next change")
            # Don't clear _last_change_at — try again next tick. If the
            # API comes back up before the operator changes the tree,
            # the next quiescence check will fire immediately because
            # the change-time is still set.
            return

        job_id = int(job.get("id", 0))
        if job_id <= 0:
            logger.error("watcher: API returned malformed job: %s", job)
            self._last_change_at = None
            return

        self._tracked_job_id = job_id
        self._tracked_job_started = now
        # Consume the change-time — we won't fire again until the next
        # actual fingerprint change.
        self._last_change_at = None
        logger.info("watcher: enqueued job %d", job_id)

    def _poll_tracked_job(self) -> None:
        if self._tracked_job_id is None:
            return

        # Timeout guard: if the job has been running absurdly long, stop
        # tracking so the watcher can fire on new changes. The job may
        # still complete server-side — we just won't fire the webhook
        # for it.
        if self._tracked_job_started is not None:
            elapsed = self._clock() - self._tracked_job_started
            if elapsed > self.config.job_wait_timeout_seconds:
                logger.warning(
                    "watcher: job %d still not terminal after %.0fs; releasing tracker",
                    self._tracked_job_id, elapsed,
                )
                self._tracked_job_id = None
                self._tracked_job_started = None
                return

        try:
            job = self.api.get_job(self._tracked_job_id)
        except Exception:
            logger.exception(
                "watcher: get_job(%d) failed; will retry next tick",
                self._tracked_job_id,
            )
            return

        status = job.get("status")
        if status not in _TERMINAL_STATUSES:
            return  # still cooking

        logger.info(
            "watcher: job %d reached terminal status %s",
            self._tracked_job_id, status,
        )

        if self.config.webhook_url:
            self._fire_webhook(job)

        self._tracked_job_id = None
        self._tracked_job_started = None

    def _fire_webhook(self, job: dict) -> None:
        result = job.get("result_json") or {}
        payload = {
            "event": "import_completed",
            "job_id": job.get("id"),
            "status": job.get("status"),
            "nights_imported": result.get("nights_imported", 0),
            "nights_skipped": result.get("nights_skipped", 0),
            "nights_skipped_existing": result.get("nights_skipped_existing", 0),
            "earliest_date": result.get("earliest_date"),
            "latest_date": result.get("latest_date"),
            "error_message": job.get("error_message"),
            "source_path": job.get("source_path"),
            "force_reimport": job.get("force_reimport"),
        }
        assert self.config.webhook_url  # narrowed by caller
        self.api.fire_webhook(self.config.webhook_url, payload)

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep — checks ``_stop`` so SIGTERM is responsive."""
        deadline = self._clock() + seconds
        while not self._stop and self._clock() < deadline:
            time.sleep(min(0.5, deadline - self._clock()))

    def _handle_signal(self, signum: int, _frame) -> None:
        logger.info("watcher: received signal %d; shutting down", signum)
        self.stop()
