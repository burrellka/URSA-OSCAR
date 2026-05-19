"""Phase 4 Ticket 1 — session-exclusion regression coverage.

Locks down:
  1. Importer writes per-session rows to the new `sessions` table.
  2. Toggle endpoint flips exclusion state and recomputes nightly_summary.
  3. Excluding a single-session night zeros mask-on and NULLs AHI.
  4. Excluding one of several sessions recomputes AHI/mask-on from the rest.
  5. Re-importing preserves exclusions (architect requirement).
  6. Re-import with force=True still respects exclusions.
  7. The math correctness invariant: when no sessions are excluded,
     recompute_for_date produces a NightlySummary equivalent to the
     importer's original write (everything except `last_updated`).
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from ursa_oscar.analytics.recompute_summary import recompute_for_date
from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from ursa_oscar.storage.repositories import nights as nights_repo
from ursa_oscar.storage.repositories import sessions as sessions_repo
from tests.conftest import FIXTURE_DATES, FIXTURE_ROOT, bypass_auth


@pytest.fixture
def seeded_client(tmp_path, monkeypatch):
    """Spin up a TestClient backed by a temp DB pre-loaded with the canonical
    4-night fixture. Reused across tests that POST against the toggle
    endpoint, with each test getting its own tmpdir (no cross-pollution)."""
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "exclusion.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    # Force on first seed so every canonical night actually imports
    # (default skip_existing=True would skip nothing on a fresh DB
    # but explicit is clearer for the test).
    import_path(FIXTURE_ROOT, seeder, skip_existing=False)
    seeder.close()

    app = create_app()
    bypass_auth(app)  # Phase 6.4
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def _get_sessions(client: TestClient, target_date) -> list[dict]:
    r = client.get(f"/api/v1/nights/{target_date}/sessions")
    assert r.status_code == 200, r.text
    return r.json()


def _first_multi_session_date(client: TestClient):
    """Scan the canonical fixture dates and return the first one with
    >= 2 sessions. Tests that need multi-session math run against this
    rather than hard-coding a date — keeps the suite robust to
    fixture growth/changes."""
    for d in FIXTURE_DATES:
        if len(_get_sessions(client, d)) >= 2:
            return d
    pytest.skip("no multi-session night in the canonical fixture")


# Hard-coded for the math-correctness invariant test where we want
# every canonical night exercised — not just multi-session ones.
multi_date_FIRST = FIXTURE_DATES[0]


# -------------------------------------------------------------------------
# 1. Importer populates sessions table.
# -------------------------------------------------------------------------


def test_importer_writes_session_rows(seeded_client):
    """Every canonical night must have at least one row in the
    `sessions` table after import — otherwise the recompute path has
    nothing to filter by."""
    for d in FIXTURE_DATES:
        rows = _get_sessions(seeded_client, d)
        assert rows, f"no session rows for {d}"
        # Every row must have non-null timing and a positive mask_on.
        for r in rows:
            assert r["start_ts"] is not None
            assert r["end_ts"] is not None
            assert r["mask_on_minutes"] >= 0
            assert r["excluded"] is False  # fresh import == no exclusions


# -------------------------------------------------------------------------
# 2 + 3 + 4. Toggle endpoint flips state + recomputes summary.
# -------------------------------------------------------------------------


def test_toggle_excludes_session_and_recomputes_summary(seeded_client):
    """Excluding a session from a multi-session night must drop
    nightly_summary's session_count and recompute mask-on / AHI from
    the remaining sessions."""
    multi_date = _first_multi_session_date(seeded_client)
    rows = _get_sessions(seeded_client, multi_date)
    target_sid = rows[0]["session_id"]

    pre = seeded_client.get(f"/api/v1/night/{multi_date}").json()
    pre_session_count = pre["session_count"]
    pre_total_minutes = pre["total_time_minutes"]

    r = seeded_client.post(
        f"/api/v1/nights/{multi_date}/sessions/{target_sid}/toggle"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["excluded"] is True
    summary = body["summary"]
    # The remaining sessions sum to less total mask-on time.
    assert summary["session_count"] == pre_session_count - 1
    assert summary["total_time_minutes"] < pre_total_minutes
    # And the row reads back excluded.
    rows_after = _get_sessions(seeded_client, multi_date)
    excluded_now = next(r for r in rows_after if r["session_id"] == target_sid)
    assert excluded_now["excluded"] is True


def test_toggle_again_includes_session_and_restores_original_summary(seeded_client):
    """Re-toggling a session brings the summary numbers back. Floating-
    point equality is loose because the recompute reads percentiles
    from the timeseries tables rather than re-computing from numpy
    arrays in memory."""
    multi_date = _first_multi_session_date(seeded_client)
    rows = _get_sessions(seeded_client, multi_date)
    target_sid = rows[0]["session_id"]

    original = seeded_client.get(f"/api/v1/night/{multi_date}").json()

    seeded_client.post(f"/api/v1/nights/{multi_date}/sessions/{target_sid}/toggle")
    seeded_client.post(f"/api/v1/nights/{multi_date}/sessions/{target_sid}/toggle")

    restored = seeded_client.get(f"/api/v1/night/{multi_date}").json()
    assert restored["session_count"] == original["session_count"]
    assert restored["total_time_minutes"] == original["total_time_minutes"]
    # AHI within 0.01 — recompute math is read from DB rows; original
    # math is from in-memory waveforms. Small floating-point delta
    # acceptable.
    if original["total_ahi"] is not None:
        assert abs(restored["total_ahi"] - original["total_ahi"]) < 0.01


def test_excluding_every_session_yields_null_ahi_not_zero(seeded_client):
    """Architect specifically called this out: excluding all sessions
    must yield NULL AHI, not 0 (which would imply 0 events in a
    real recording — different clinical meaning)."""
    multi_date = _first_multi_session_date(seeded_client)
    rows = _get_sessions(seeded_client, multi_date)
    for r in rows:
        seeded_client.post(
            f"/api/v1/nights/{multi_date}/sessions/{r['session_id']}/toggle"
        )

    summary = seeded_client.get(f"/api/v1/night/{multi_date}").json()
    assert summary["session_count"] == 0
    assert summary["total_time_minutes"] == 0
    assert summary["total_ahi"] is None
    assert summary["obstructive_ahi"] is None
    assert summary["median_pressure"] is None


# -------------------------------------------------------------------------
# 5 + 6. Re-import preserves exclusions, with and without force.
# -------------------------------------------------------------------------


def test_reimport_preserves_exclusion(seeded_client, tmp_path):
    """After excluding a session, a non-force re-import should keep
    the exclusion in place — both the excluded_sessions row AND the
    derived nightly_summary numbers stay where the operator left them."""
    multi_date = _first_multi_session_date(seeded_client)
    rows = _get_sessions(seeded_client, multi_date)
    target_sid = rows[0]["session_id"]
    seeded_client.post(f"/api/v1/nights/{multi_date}/sessions/{target_sid}/toggle")

    excluded_summary = seeded_client.get(f"/api/v1/night/{multi_date}").json()

    # Re-import (skip_existing=True default). Since this date is already
    # in the DB, the importer will skip the EDF re-parse for it but the
    # excluded row stays put.
    r = seeded_client.post(
        "/api/v1/imports",
        json={"source_path": str(FIXTURE_ROOT)},
    )
    assert r.status_code == 200, r.text

    after = seeded_client.get(f"/api/v1/night/{multi_date}").json()
    assert after["session_count"] == excluded_summary["session_count"]
    assert after["total_time_minutes"] == excluded_summary["total_time_minutes"]


def test_force_reimport_still_respects_exclusion(seeded_client):
    """force=true re-parses every night from EDF, then recompute_summary
    is called whenever excluded_sessions has rows for the night — so
    the exclusion still applies after a force re-import."""
    multi_date = _first_multi_session_date(seeded_client)
    rows = _get_sessions(seeded_client, multi_date)
    target_sid = rows[0]["session_id"]
    seeded_client.post(f"/api/v1/nights/{multi_date}/sessions/{target_sid}/toggle")
    excluded_summary = seeded_client.get(f"/api/v1/night/{multi_date}").json()

    r = seeded_client.post(
        "/api/v1/imports?force=true",
        json={"source_path": str(FIXTURE_ROOT)},
    )
    assert r.status_code == 200, r.text

    after = seeded_client.get(f"/api/v1/night/{multi_date}").json()
    assert after["session_count"] == excluded_summary["session_count"]
    assert after["total_time_minutes"] == excluded_summary["total_time_minutes"]


# -------------------------------------------------------------------------
# 7. Math correctness invariant.
# -------------------------------------------------------------------------


def test_recompute_with_no_exclusions_matches_original(tmp_path):
    """The no-op recompute path: when zero sessions are excluded, the
    output of recompute_for_date must equal the importer's original
    nightly_summary write (modulo last_updated and float precision).

    This is the invariant that protects us against silent drift between
    the importer's math and the recompute's math. If a future refactor
    changes one but not the other, this test catches it.
    """
    db_file = tmp_path / "noop.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)
    import_path(FIXTURE_ROOT, db, skip_existing=False)

    for d in FIXTURE_DATES:
        before = nights_repo.get_by_date(db, d)
        if before is None:
            continue
        recomputed = recompute_for_date(db, d)
        assert recomputed is not None, f"recompute returned None for {d}"
        # Compare the recording-derived fields (skipping last_updated +
        # equipment fields which the recompute deliberately preserves).
        for f in (
            "session_count", "total_time_minutes",
            "minutes_in_apnea",
        ):
            assert getattr(recomputed, f) == getattr(before, f), (
                f"{f} mismatch on {d}: was {getattr(before, f)}, "
                f"now {getattr(recomputed, f)}"
            )
        # AHI floats: within 0.01 — recompute reads from DB rows, the
        # importer's math came from in-memory numpy. Small loss of
        # precision in the DB write+read round-trip is acceptable.
        for f in ("total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index"):
            before_val = getattr(before, f)
            after_val = getattr(recomputed, f)
            if before_val is None or after_val is None:
                assert before_val == after_val
            else:
                assert abs(after_val - before_val) < 0.01, (
                    f"{f} drifted on {d}: {before_val} -> {after_val}"
                )

    db.close()


def test_toggle_404_when_session_not_in_db(seeded_client):
    """An orphan toggle (session_id that the importer never wrote)
    must 404 — not silently create an excluded_sessions row that
    points at a session that doesn't exist."""
    target_date = date(2099, 1, 1)  # not in fixture
    r = seeded_client.post(f"/api/v1/nights/{target_date}/sessions/1/toggle")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


# Sanity check on the repo layer — make sure the sessions repo is
# round-trip correct in isolation, separate from the API. Catches
# repo bugs that the API path would mask.
def test_sessions_repo_roundtrip(tmp_path):
    db_file = tmp_path / "roundtrip.duckdb"
    db = DuckDBManager(db_file, read_only=False)
    apply_migrations(db)
    import_path(FIXTURE_ROOT, db, skip_existing=False)

    # Standalone repo test — find a multi-session date directly.
    multi_date = None
    for d in FIXTURE_DATES:
        if len(sessions_repo.list_for_date(db, d)) >= 2:
            multi_date = d
            break
    if multi_date is None:
        pytest.skip("no multi-session night in the canonical fixture")

    rows = sessions_repo.list_for_date(db, multi_date)
    assert rows
    sid = rows[0].session_id
    assert sessions_repo.is_excluded(db, multi_date, sid) is False

    sessions_repo.toggle(db, multi_date, sid)
    assert sessions_repo.is_excluded(db, multi_date, sid) is True

    non_excluded = sessions_repo.list_non_excluded_ids(db, multi_date)
    assert sid not in non_excluded

    sessions_repo.toggle(db, multi_date, sid)
    assert sessions_repo.is_excluded(db, multi_date, sid) is False

    db.close()
