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


def test_layout_finds_four_night_dirs():
    nights = list_night_dirs(FIXTURE_ROOT)
    dates = [n[0] for n in nights]
    assert dates == [
        date(2026, 5, 7),
        date(2026, 5, 8),
        date(2026, 5, 9),
        date(2026, 5, 10),
    ]


def test_import_4_nights_under_60s_and_under_20mb(tmp_path):
    """Acceptance gate criteria 6 + 7."""
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    started = time.monotonic()
    log = import_path(FIXTURE_ROOT, db, verbose=False)
    elapsed = time.monotonic() - started

    assert log.status == "completed"
    assert log.nights_imported == 4

    # Criterion 6: <60 seconds
    assert elapsed < 60.0, f"Import took {elapsed:.1f}s (>60s budget)"

    # Roundtrip: every night queryable via the repository
    for d in [date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9), date(2026, 5, 10)]:
        n = nights_repo.get_by_date(db, d)
        assert n is not None, f"Missing night {d}"
        assert n.session_count is not None and n.session_count > 0

    # Events landed too
    counts_58 = events_repo.count_for_date(db, date(2026, 5, 8))
    assert counts_58.get("ClearAirway", 0) == 47

    db.close()

    # Criterion 7: ~3 MB/night × 4 = 12 MB target, 20 MB ceiling
    size_bytes = db_file.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    assert size_mb <= 20.0, f"DuckDB file {size_mb:.1f} MB exceeds 20 MB ceiling"


def test_reimport_is_idempotent(tmp_path):
    """Dedup-on-date: re-running the importer overwrites, doesn't duplicate."""
    db_file = tmp_path / "test.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)

    import_path(FIXTURE_ROOT, db)
    import_path(FIXTURE_ROOT, db)

    nights = nights_repo.list_dates(db)
    assert len(nights) == 4
    counts_58 = events_repo.count_for_date(db, date(2026, 5, 8))
    assert counts_58.get("ClearAirway", 0) == 47  # not 94

    db.close()
