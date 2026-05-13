"""Shared pytest fixtures for URSA-OSCAR backend tests."""
from __future__ import annotations

from datetime import date as date_t
from pathlib import Path

import pytest

from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


# Resolves to backend/tests/regression/fixtures/nights/oscar-reference/ — the
# actual layout per Phase 1 V1 Option 2. Aligned with the SD-card-as-copied
# structure rather than the spec's two-sibling layout.
FIXTURE_ROOT = (
    Path(__file__).parent / "regression" / "fixtures" / "nights" / "oscar-reference"
).resolve()

FIXTURE_NIGHT_DIRS = ["20260507", "20260508", "20260509", "20260510"]
FIXTURE_DATES = [date_t(2026, 5, 7), date_t(2026, 5, 8), date_t(2026, 5, 9), date_t(2026, 5, 10)]


@pytest.fixture
def fixture_root() -> Path:
    """Absolute path to the regression fixture tree's `oscar-reference` dir."""
    return FIXTURE_ROOT


@pytest.fixture
def temp_db(tmp_path: Path) -> DuckDBManager:
    """A fresh in-process DuckDB with migrations applied. Closed at teardown."""
    db = DuckDBManager(tmp_path / "test.duckdb", read_only=False)
    apply_migrations(db)
    yield db
    db.close()
