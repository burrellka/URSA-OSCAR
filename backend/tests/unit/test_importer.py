"""Importer smoke + acceptance-criterion tests.

Acceptance gate criterion 6: Import of 4-night fixture directory completes
in <60 seconds. We measure here.

Acceptance gate criterion 7: DuckDB file grows by ~3 MB/night. With 4
fixture nights and a 20 MB ceiling, we assert ≤20 MB final size.
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

from ursa_oscar.ingestion.airsense11_layout import list_night_dirs
from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from ursa_oscar.storage.repositories import events as events_repo
from ursa_oscar.storage.repositories import nights as nights_repo
from tests.conftest import FIXTURE_ROOT


def test_layout_finds_canonical_night_dirs():
    """The 4 canonical-targets nights must be present, in date order.
    Extra fixture nights are allowed (the regression set grows organically;
    see canonical_targets.py docstring)."""
    nights = list_night_dirs(FIXTURE_ROOT)
    dates = [n[0] for n in nights]
    canonical = {date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9), date(2026, 5, 10)}
    assert canonical.issubset(set(dates))
    assert dates == sorted(dates)


def test_import_canonical_nights_under_60s_and_under_20mb(tmp_path):
    """Phase 1 acceptance gate criteria 6 + 7, generalized for a growing
    fixture set. The 4 canonical nights MUST land; extras are allowed."""
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    started = time.monotonic()
    log = import_path(FIXTURE_ROOT, db, verbose=False)
    elapsed = time.monotonic() - started

    assert log.status in ("completed", "partial")
    assert log.nights_imported >= 4

    # Criterion 6: <60 seconds (scaled budget — 15s/night ceiling)
    budget = max(60.0, 15.0 * log.nights_imported)
    assert elapsed < budget, f"Import took {elapsed:.1f}s (>{budget:.0f}s budget)"

    # Roundtrip: every canonical night queryable via the repository
    for d in [date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9), date(2026, 5, 10)]:
        n = nights_repo.get_by_date(db, d)
        assert n is not None, f"Missing night {d}"
        assert n.session_count is not None and n.session_count > 0

    # Events landed too
    counts_58 = events_repo.count_for_date(db, date(2026, 5, 8))
    assert counts_58.get("ClearAirway", 0) == 47

    db.close()

    # Criterion 7: ~3 MB/night ceiling — proportional to night count.
    size_bytes = db_file.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    ceiling_mb = max(20.0, 5.0 * log.nights_imported)
    assert size_mb <= ceiling_mb, f"DuckDB file {size_mb:.1f} MB exceeds {ceiling_mb:.0f} MB ceiling"


def test_reimport_is_idempotent(tmp_path):
    """Dedup-on-date: re-running the importer overwrites, doesn't duplicate."""
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    # First run: skip_existing=False so all canonical nights actually import,
    # even though the default is True. Otherwise this test would be ambiguous
    # (was the second run a noop because of dedup, or because of skip?).
    import_path(FIXTURE_ROOT, db, skip_existing=False)
    import_path(FIXTURE_ROOT, db, skip_existing=False)

    nights = nights_repo.list_dates(db)
    # Generalized for a growing fixture set — re-import must not multiply
    # the night count. We check the canonical 4 are present and that
    # re-import didn't double-insert (counts match first run).
    canonical = {date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9), date(2026, 5, 10)}
    assert canonical.issubset(set(nights))
    counts_58 = events_repo.count_for_date(db, date(2026, 5, 8))
    assert counts_58.get("ClearAirway", 0) == 47  # not 94

    db.close()


def test_reimport_with_skip_existing_skips_known_nights(tmp_path):
    """0.6.3 dedup — when skip_existing=True (the default), the second run
    must NOT touch nights that are already in the DB. We verify two things:
      1. nights_skipped_existing equals the canonical 4 (every fixture
         night was already known on the second run).
      2. nights_imported on the second run is 0.

    Performance follows by construction — if we don't re-parse, we don't
    re-do the per-night EDF + session-aggregate + summary-builder work.
    """
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    # Force a clean first import.
    first = import_path(FIXTURE_ROOT, db, skip_existing=False)
    assert first.nights_imported >= 4
    assert first.nights_skipped_existing == 0

    # Second run is the new default — skip_existing=True. Every canonical
    # night must show up under nights_skipped_existing.
    second = import_path(FIXTURE_ROOT, db)  # uses default skip_existing=True
    assert second.nights_imported == 0
    assert second.nights_skipped_existing >= 4
    assert second.status == "completed"

    db.close()


def test_force_reimport_overrides_skip_existing(tmp_path):
    """skip_existing=False (the wire shape of ?force=true) must re-parse
    every night even when the DB already has rows for them. This is the
    escape hatch for after-an-importer-fix re-runs."""
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    first = import_path(FIXTURE_ROOT, db, skip_existing=False)
    nights_first = first.nights_imported

    forced = import_path(FIXTURE_ROOT, db, skip_existing=False)
    assert forced.nights_imported == nights_first
    assert forced.nights_skipped_existing == 0

    db.close()


def test_status_discriminator_partial_when_some_errored_and_some_deduped(tmp_path):
    """Phase 5 Ticket 0.5 — locks down the operator-discovered bug.

    Scenario from the field: re-import an SD card where 28 nights are
    already in the DB and 36 night dirs exist on the card but have no
    usable EDF data (empty sessions). Pre-0.5 the importer reported
    ``status="failed"`` because nights_imported=0 and skipped>0; that
    was misleading because the import ran fine — the 28 known nights
    are happily in the DB. The fix routes this case to ``"partial"``
    so the result tile renders amber (look at the skip list) instead
    of red (everything broke).

    This test fakes both a successful first import (populating
    nights_skipped_existing on the second run) AND empty / no-session
    night dirs by pointing the second run at a fixture root with at
    least one empty-DATALOG directory in addition to the canonical
    nights.
    """
    import os

    db_file = tmp_path / "status.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    # First pass — seed the DB with the canonical 4 nights.
    import_path(FIXTURE_ROOT, db, skip_existing=False)

    # Build a second-pass source dir that contains BOTH the canonical
    # fixture nights AND a deliberately-empty YYYYMMDD/ dir. The fixture
    # is DATALOG-flat (YYYYMMDD/ directly under the root, no DATALOG/
    # wrapper); same convention here. The second-pass importer hits the
    # "some skipped (no usable sessions), some already known" code path.
    import shutil
    second_pass = tmp_path / "second_pass"
    second_pass.mkdir(parents=True)
    for d in os.listdir(FIXTURE_ROOT):
        src = FIXTURE_ROOT / d
        if src.is_dir():
            # copytree (vs. symlink) for Windows-test robustness — symlink
            # perms are flaky on Windows CI / non-admin shells.
            shutil.copytree(src, second_pass / d)
    # Deliberately-empty night dir — importer's discover_sessions returns
    # empty for it and it ends up on the skipped list with reason
    # "No usable sessions found."
    (second_pass / "20990101").mkdir()

    log = import_path(second_pass, db, skip_existing=True)
    assert log.nights_imported == 0
    assert log.nights_skipped_existing >= 4  # the canonical 4
    assert log.nights_skipped >= 1  # the empty 2099-01-01
    # The key assertion — partial, not failed.
    assert log.status == "partial", (
        f"expected partial (some deduped + some errored); got {log.status}. "
        f"Pre-fix this reported failed which alarmed the operator unnecessarily."
    )
    assert log.error_message is not None
    assert "already known" in log.error_message.lower()
    db.close()


def test_status_discriminator_failed_only_when_truly_nothing_succeeded(tmp_path):
    """Failed should now require 0 imported AND 0 deduped — i.e. nothing
    in the DB ever recognized any of the attempted dirs."""
    import os

    db_file = tmp_path / "fail.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    # Source containing only broken / empty dirs and NOTHING in the DB.
    bad_source = tmp_path / "bad" / "DATALOG"
    bad_source.mkdir(parents=True)
    (bad_source / "20990101").mkdir()
    (bad_source / "20990102").mkdir()

    log = import_path(tmp_path / "bad", db)
    assert log.nights_imported == 0
    assert log.nights_skipped_existing == 0
    assert log.nights_skipped == 2
    assert log.status == "failed"
    db.close()


def test_status_discriminator_completed_for_pure_dedup(tmp_path):
    """The clean re-import case — all nights already known, nothing new,
    nothing errored — should stay 'completed'."""
    db_file = tmp_path / "dedup.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    import_path(FIXTURE_ROOT, db, skip_existing=False)
    log = import_path(FIXTURE_ROOT, db, skip_existing=True)
    assert log.nights_imported == 0
    assert log.nights_skipped == 0
    assert log.nights_skipped_existing >= 4
    assert log.status == "completed"
    db.close()
