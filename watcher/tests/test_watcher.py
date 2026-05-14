"""Phase 4 Ticket 3 — watcher loop regression coverage.

Tests use a fake clock + fake API to exercise the tick logic
deterministically. The fingerprint scanner runs against real
tmp_path trees so we get end-to-end coverage of the os.scandir
walk + dir/file mtime tracking.

Test inventory:
  1. Empty tree → no trigger
  2. New DATALOG night appears → quiescence timer starts
  3. Tree changes during quiescence → timer resets
  4. Tree stable for N seconds past quiescence → import triggered
  5. While job is in-flight, no new trigger fires
  6. Job reaches terminal status → webhook fires (when configured)
  7. Job reaches terminal status → no webhook when not configured
  8. API enqueue failure → no job tracked, change-time preserved for retry
  9. Job stuck running past job_wait_timeout → tracker released
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from ursa_oscar_watcher.config import WatcherConfig
from ursa_oscar_watcher.fingerprint import compute_fingerprint
from ursa_oscar_watcher.watcher import Watcher


# -------------------------------------------------------------------------
# Helpers — fake clock + fake API client.
# -------------------------------------------------------------------------


class FakeClock:
    """Monotonic clock under test control. Returns whatever `now` is."""
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeApi:
    """Records calls + returns canned responses. The watcher only ever
    calls enqueue_import / get_job / fire_webhook."""

    def __init__(self) -> None:
        self.enqueue_calls: list[dict[str, Any]] = []
        self.get_job_calls: list[int] = []
        self.webhook_calls: list[tuple[str, dict[str, Any]]] = []
        # State the FakeApi exposes to callers.
        self.next_job_id = 1
        self.job_status_by_id: dict[int, str] = {}
        self.job_payload_by_id: dict[int, dict[str, Any]] = {}
        # When set, enqueue_import raises this exception once then
        # clears itself.
        self.next_enqueue_error: Exception | None = None

    def enqueue_import(self, source_path: str, *, force: bool = False) -> dict[str, Any]:
        if self.next_enqueue_error is not None:
            err = self.next_enqueue_error
            self.next_enqueue_error = None
            raise err
        self.enqueue_calls.append({"source_path": source_path, "force": force})
        job_id = self.next_job_id
        self.next_job_id += 1
        self.job_status_by_id[job_id] = "queued"
        self.job_payload_by_id[job_id] = {
            "id": job_id,
            "status": "queued",
            "source_path": source_path,
            "upload_dir": None,
            "force_reimport": force,
            "result_json": None,
            "error_message": None,
        }
        return self.job_payload_by_id[job_id]

    def get_job(self, job_id: int) -> dict[str, Any]:
        self.get_job_calls.append(job_id)
        if job_id not in self.job_payload_by_id:
            raise ValueError(f"unknown job {job_id}")
        payload = dict(self.job_payload_by_id[job_id])
        payload["status"] = self.job_status_by_id[job_id]
        return payload

    def fire_webhook(self, webhook_url: str, payload: dict[str, Any]) -> None:
        self.webhook_calls.append((webhook_url, payload))

    def transition_job(self, job_id: int, status: str, result: dict | None = None) -> None:
        """Test helper — flip a job's status and optionally attach a
        result payload (mirrors the worker writing result_json)."""
        self.job_status_by_id[job_id] = status
        if result is not None:
            self.job_payload_by_id[job_id]["result_json"] = result


def make_watcher(
    tmp_path: Path,
    *,
    webhook_url: str | None = None,
    force_reimport: bool = False,
    quiescence_seconds: float = 30.0,
    job_wait_timeout_seconds: float = 600.0,
) -> tuple[Watcher, FakeApi, FakeClock]:
    """Construct a Watcher wired to a tmp watch path + fake API."""
    clock = FakeClock()
    api = FakeApi()
    config = WatcherConfig(
        api_url="http://test",
        watch_path=str(tmp_path),
        poll_interval_seconds=1.0,
        quiescence_seconds=quiescence_seconds,
        webhook_url=webhook_url,
        force_reimport=force_reimport,
        job_wait_timeout_seconds=job_wait_timeout_seconds,
    )
    return Watcher(config, api=api, clock=clock), api, clock


def add_night(tmp_path: Path, name: str, files: list[str]) -> None:
    """Create a YYYYMMDD-style dir under DATALOG/ with given files."""
    datalog = tmp_path / "DATALOG"
    datalog.mkdir(exist_ok=True)
    night = datalog / name
    night.mkdir()
    for f in files:
        (night / f).write_bytes(b"x")


# -------------------------------------------------------------------------
# Fingerprint scanner — covered indirectly by watcher tests; one direct
# test to lock the behavior independently of the watcher.
# -------------------------------------------------------------------------


def test_fingerprint_changes_when_new_night_added(tmp_path):
    add_night(tmp_path, "20260513", ["foo.edf"])
    fp1 = compute_fingerprint(str(tmp_path))
    assert fp1 is not None
    assert len(fp1) == 1
    assert fp1[0][0] == "20260513"

    add_night(tmp_path, "20260514", ["bar.edf"])
    fp2 = compute_fingerprint(str(tmp_path))
    assert fp2 is not None
    assert len(fp2) == 2
    assert fp1 != fp2


def test_fingerprint_unreachable_path_returns_none(tmp_path):
    nope = tmp_path / "does_not_exist"
    assert compute_fingerprint(str(nope)) is None


# -------------------------------------------------------------------------
# Watcher tick logic.
# -------------------------------------------------------------------------


def test_empty_tree_does_not_trigger(tmp_path):
    watcher, api, clock = make_watcher(tmp_path)
    # Two ticks separated by enough time to clear quiescence — both
    # should be no-ops because the fingerprint never showed a change.
    watcher.tick()
    clock.advance(60.0)
    watcher.tick()
    assert api.enqueue_calls == []


def test_new_night_starts_quiescence_timer(tmp_path):
    watcher, api, _clock = make_watcher(tmp_path, quiescence_seconds=30.0)
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    # First detection: tree changed; quiescence timer set, no import yet.
    assert api.enqueue_calls == []


def test_change_during_quiescence_resets_timer(tmp_path):
    watcher, api, clock = make_watcher(tmp_path, quiescence_seconds=30.0)
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    # Half-way through quiescence: tree changes again → reset timer.
    clock.advance(15.0)
    add_night(tmp_path, "20260514", ["b.edf"])
    watcher.tick()
    # 15s after the second change — NOT yet quiescent (would need 30).
    clock.advance(15.0)
    watcher.tick()
    assert api.enqueue_calls == []


def test_quiescence_elapsed_triggers_import(tmp_path):
    watcher, api, clock = make_watcher(tmp_path, quiescence_seconds=30.0)
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()  # detect change
    clock.advance(31.0)
    watcher.tick()  # quiescent → trigger
    assert len(api.enqueue_calls) == 1
    assert api.enqueue_calls[0]["source_path"] == str(tmp_path)
    assert api.enqueue_calls[0]["force"] is False


def test_force_reimport_passed_through(tmp_path):
    watcher, api, clock = make_watcher(
        tmp_path, quiescence_seconds=10.0, force_reimport=True,
    )
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()
    assert api.enqueue_calls[0]["force"] is True


def test_no_duplicate_trigger_while_job_in_flight(tmp_path):
    watcher, api, clock = make_watcher(tmp_path, quiescence_seconds=10.0)
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()  # trigger 1 — job tracked

    # New change arrives; quiescence elapses again.
    clock.advance(1.0)
    add_night(tmp_path, "20260514", ["b.edf"])
    watcher.tick()  # quiescence timer reset
    clock.advance(11.0)
    watcher.tick()  # would trigger, but job 1 still in flight
    assert len(api.enqueue_calls) == 1  # NOT 2

    # Once job 1 reaches terminal, a tick will free the tracker.
    api.transition_job(1, "completed", result={"nights_imported": 1})
    watcher.tick()  # poll-and-clear
    # And then the next quiescence will fire the next import.
    # But the change-time was already consumed — we need a fresh
    # change to re-trigger.
    add_night(tmp_path, "20260515", ["c.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()
    assert len(api.enqueue_calls) == 2


def test_webhook_fires_on_completion(tmp_path):
    watcher, api, clock = make_watcher(
        tmp_path,
        webhook_url="http://hook.example/notify",
        quiescence_seconds=10.0,
    )
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()  # enqueue job 1

    # Worker finishes.
    api.transition_job(1, "completed", result={
        "nights_imported": 1,
        "nights_skipped": 0,
        "nights_skipped_existing": 0,
        "earliest_date": "2026-05-13",
        "latest_date": "2026-05-13",
        "status": "completed",
    })
    watcher.tick()  # poll → webhook
    assert len(api.webhook_calls) == 1
    url, payload = api.webhook_calls[0]
    assert url == "http://hook.example/notify"
    assert payload["event"] == "import_completed"
    assert payload["job_id"] == 1
    assert payload["status"] == "completed"
    assert payload["nights_imported"] == 1
    assert payload["latest_date"] == "2026-05-13"


def test_no_webhook_when_url_not_configured(tmp_path):
    watcher, api, clock = make_watcher(
        tmp_path, webhook_url=None, quiescence_seconds=10.0,
    )
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()
    api.transition_job(1, "completed", result={"nights_imported": 1})
    watcher.tick()
    assert api.webhook_calls == []


def test_enqueue_failure_preserves_change_time(tmp_path):
    """When the API is down, the watcher should NOT consume the change-
    time — we want to retry on the next tick rather than wait for
    another tree change."""
    watcher, api, clock = make_watcher(tmp_path, quiescence_seconds=10.0)
    api.next_enqueue_error = RuntimeError("API down")
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()  # tries to enqueue, fails — change-time stays set
    assert api.enqueue_calls == []
    # Retry on next tick succeeds.
    watcher.tick()
    assert len(api.enqueue_calls) == 1


def test_stuck_job_releases_after_timeout(tmp_path):
    """If a job stays in 'running' beyond job_wait_timeout_seconds, the
    watcher abandons the tracker so it can fire on new changes."""
    watcher, api, clock = make_watcher(
        tmp_path, quiescence_seconds=10.0, job_wait_timeout_seconds=120.0,
    )
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()  # enqueue
    api.transition_job(1, "running")
    # Advance past the timeout.
    clock.advance(125.0)
    watcher.tick()
    # Tracker released; subsequent quiescent triggers should fire again.
    add_night(tmp_path, "20260514", ["b.edf"])
    watcher.tick()
    clock.advance(11.0)
    watcher.tick()
    assert len(api.enqueue_calls) == 2


def test_unreachable_watch_path_is_a_noop(tmp_path):
    """Pulling the SD card mid-poll should NOT trigger anything weird —
    just skip the tick until the path comes back."""
    add_night(tmp_path, "20260513", ["a.edf"])
    watcher, api, clock = make_watcher(tmp_path, quiescence_seconds=10.0)
    watcher.tick()
    # Simulate the watch path going away by deleting it.
    import shutil
    shutil.rmtree(tmp_path)
    clock.advance(11.0)
    watcher.tick()  # fingerprint=None → no-op
    assert api.enqueue_calls == []
