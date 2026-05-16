"""Phase 6 Ticket 6.1 — analytical_cache regression tests.

Covers the AnalyticalCache class + its hooks (invalidation on import,
manual-log CRUD, schema bump). Locks down:

  - Fingerprint stability across call ordering
  - Fingerprint changes when underlying data changes
  - Cache hit annotates result with cache_age_seconds
  - Hit/miss bumps cache_hits + last_accessed_at correctly
  - invalidate_by_date_range targets overlapping entries only
  - clear_all wipes everything (used by schema migration)
  - stats() returns sane structure
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime

import pytest

from ursa_oscar.analytics.cache import AnalyticalCache, cached_compute
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


@pytest.fixture
def cache_db(tmp_path):
    db = DuckDBManager(tmp_path / "cache.duckdb", read_only=False)
    apply_migrations(db)
    yield db
    db.close()


def _seed_night(db: DuckDBManager, d: date_t) -> None:
    with db.serialized() as conn:
        conn.execute(
            """
            INSERT INTO nightly_summary (date, session_count, last_updated)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            """,
            (d,),
        )


# -----------------------------------------------------------------------
# Fingerprint
# -----------------------------------------------------------------------


def test_fingerprint_stable_for_same_inputs(cache_db):
    cache = AnalyticalCache(cache_db)
    f1 = cache.compute_fingerprint("tool_a", {"x": 1, "y": 2}, "abc")
    f2 = cache.compute_fingerprint("tool_a", {"y": 2, "x": 1}, "abc")
    assert f1 == f2, "param key order must not affect fingerprint"


def test_fingerprint_changes_when_data_hash_changes(cache_db):
    cache = AnalyticalCache(cache_db)
    f_a = cache.compute_fingerprint("tool_a", {"x": 1}, "hash_v1")
    f_b = cache.compute_fingerprint("tool_a", {"x": 1}, "hash_v2")
    assert f_a != f_b, "different data_version_hash must yield different fingerprint"


def test_fingerprint_distinguishes_tools(cache_db):
    cache = AnalyticalCache(cache_db)
    f_a = cache.compute_fingerprint("tool_a", {"x": 1}, "h")
    f_b = cache.compute_fingerprint("tool_b", {"x": 1}, "h")
    assert f_a != f_b


def test_data_version_hash_picks_up_nightly_summary_change(cache_db):
    cache = AnalyticalCache(cache_db)
    start, end = date_t(2026, 5, 1), date_t(2026, 5, 31)
    h_empty = cache.compute_data_version_hash(start, end)
    _seed_night(cache_db, date_t(2026, 5, 10))
    h_with_row = cache.compute_data_version_hash(start, end)
    assert h_empty != h_with_row


# -----------------------------------------------------------------------
# get / set round-trip
# -----------------------------------------------------------------------


def test_set_then_get_returns_envelope_with_cache_age(cache_db):
    cache = AnalyticalCache(cache_db)
    fp = cache.compute_fingerprint("tool_x", {"q": 1}, "h")
    envelope = {"ok": True, "data": {"r": 0.42, "n": 30}}
    cache.set(
        fingerprint=fp, tool_name="tool_x",
        params={"q": 1, "start_date": "2026-05-01", "end_date": "2026-05-31"},
        result=envelope, data_version_hash="h", compute_duration_ms=12.3,
    )
    hit = cache.get(fp)
    assert hit is not None
    assert hit["ok"] is True
    assert hit["data"]["r"] == 0.42
    assert "cache_age_seconds" in hit["data"]
    assert "computed_at" in hit["data"]


def test_get_returns_none_on_miss(cache_db):
    cache = AnalyticalCache(cache_db)
    assert cache.get("nonexistent-fingerprint") is None


def test_get_bumps_hit_counter(cache_db):
    cache = AnalyticalCache(cache_db)
    fp = cache.compute_fingerprint("tool_x", {"q": 1}, "h")
    cache.set(
        fingerprint=fp, tool_name="tool_x",
        params={"q": 1}, result={"ok": True, "data": {}},
        data_version_hash="h", compute_duration_ms=1.0,
    )
    cache.get(fp); cache.get(fp); cache.get(fp)
    with cache_db.serialized() as conn:
        n_hits = conn.execute(
            "SELECT cache_hits FROM analytical_cache WHERE fingerprint = ?",
            (fp,),
        ).fetchone()[0]
    assert n_hits == 3


# -----------------------------------------------------------------------
# Invalidation
# -----------------------------------------------------------------------


def test_invalidate_by_date_range_clears_overlapping_entries(cache_db):
    cache = AnalyticalCache(cache_db)
    # Three entries with three different date ranges.
    for i, (s, e) in enumerate([
        ("2026-04-01", "2026-04-30"),  # in past — should NOT match
        ("2026-05-01", "2026-05-15"),  # overlaps
        ("2026-05-20", "2026-06-10"),  # overlaps
    ]):
        cache.set(
            fingerprint=f"fp_{i}",
            tool_name="tool_x",
            params={"q": i, "start_date": s, "end_date": e},
            result={"ok": True, "data": {}},
            data_version_hash="h",
            compute_duration_ms=1.0,
        )

    n_cleared = cache.invalidate_by_date_range(
        date_t(2026, 5, 10), date_t(2026, 5, 25),
    )
    assert n_cleared == 2

    with cache_db.serialized() as conn:
        remaining = conn.execute(
            "SELECT params_json FROM analytical_cache ORDER BY tool_name"
        ).fetchall()
    assert len(remaining) == 1
    assert "2026-04-30" in remaining[0][0]


def test_invalidate_conservative_when_no_date_range_in_params(cache_db):
    """Entry whose params don't include start_date/end_date → conservative
    invalidate (better to recompute once than serve stale)."""
    cache = AnalyticalCache(cache_db)
    cache.set(
        fingerprint="no_range_fp", tool_name="weirdly_dateless",
        params={"q": "no_date_fields_here"},
        result={"ok": True, "data": {}},
        data_version_hash="h", compute_duration_ms=1.0,
    )
    n = cache.invalidate_by_date_range(
        date_t(2026, 5, 1), date_t(2026, 5, 31),
    )
    assert n == 1


def test_clear_all_wipes_everything(cache_db):
    cache = AnalyticalCache(cache_db)
    for i in range(5):
        cache.set(
            fingerprint=f"fp_{i}", tool_name="tool_x",
            params={"q": i}, result={"ok": True, "data": {}},
            data_version_hash="h", compute_duration_ms=1.0,
        )
    assert cache.clear_all() == 5
    assert cache.clear_all() == 0  # idempotent


# -----------------------------------------------------------------------
# stats
# -----------------------------------------------------------------------


def test_stats_empty_cache(cache_db):
    cache = AnalyticalCache(cache_db)
    stats = cache.stats()
    assert stats["total_entries"] == 0
    assert stats["total_hits"] == 0
    assert stats["cache_hit_rate"] == 0.0
    assert stats["by_tool"] == {}


def test_stats_populated_cache(cache_db):
    cache = AnalyticalCache(cache_db)
    cache.set(
        fingerprint="fp_a", tool_name="tool_a",
        params={"x": 1}, result={"ok": True, "data": {"big": "x" * 500}},
        data_version_hash="h", compute_duration_ms=10.0,
    )
    cache.set(
        fingerprint="fp_b", tool_name="tool_a",
        params={"x": 2}, result={"ok": True, "data": {}},
        data_version_hash="h", compute_duration_ms=20.0,
    )
    cache.set(
        fingerprint="fp_c", tool_name="tool_b",
        params={"x": 3}, result={"ok": True, "data": {}},
        data_version_hash="h", compute_duration_ms=5.0,
    )
    cache.get("fp_a"); cache.get("fp_a"); cache.get("fp_c")
    stats = cache.stats()
    assert stats["total_entries"] == 3
    assert stats["total_hits"] == 3
    assert 0 < stats["cache_hit_rate"] < 1
    assert stats["largest_entry_bytes"] >= 500
    assert "tool_a" in stats["by_tool"]
    assert stats["by_tool"]["tool_a"]["entries"] == 2
    assert stats["by_tool"]["tool_a"]["hits"] == 2
    assert stats["by_tool"]["tool_b"]["entries"] == 1


# -----------------------------------------------------------------------
# cached_compute context-manager helper
# -----------------------------------------------------------------------


def test_cached_compute_miss_then_hit(cache_db):
    cache = AnalyticalCache(cache_db)
    params = {"target": "ahi", "start_date": "2026-05-01", "end_date": "2026-05-31"}

    with cached_compute(
        cache, tool_name="tool_x", params=params,
        start_date=date_t(2026, 5, 1), end_date=date_t(2026, 5, 31),
    ) as ctx:
        assert ctx.hit is False
        envelope = {"ok": True, "data": {"answer": 42}}
        ctx.store(envelope)

    with cached_compute(
        cache, tool_name="tool_x", params=params,
        start_date=date_t(2026, 5, 1), end_date=date_t(2026, 5, 31),
    ) as ctx:
        assert ctx.hit is True
        assert ctx.cached_result["data"]["answer"] == 42
        assert "cache_age_seconds" in ctx.cached_result["data"]


def test_cached_compute_recompute_flag_forces_miss(cache_db):
    cache = AnalyticalCache(cache_db)
    params = {"target": "ahi", "start_date": "2026-05-01", "end_date": "2026-05-31"}

    with cached_compute(
        cache, tool_name="tool_x", params=params,
        start_date=date_t(2026, 5, 1), end_date=date_t(2026, 5, 31),
    ) as ctx:
        ctx.store({"ok": True, "data": {"answer": 1}})

    with cached_compute(
        cache, tool_name="tool_x", params=params,
        start_date=date_t(2026, 5, 1), end_date=date_t(2026, 5, 31),
        recompute=True,
    ) as ctx:
        assert ctx.hit is False
        ctx.store({"ok": True, "data": {"answer": 2}})

    # Third call should now hit the new value.
    with cached_compute(
        cache, tool_name="tool_x", params=params,
        start_date=date_t(2026, 5, 1), end_date=date_t(2026, 5, 31),
    ) as ctx:
        assert ctx.hit is True
        assert ctx.cached_result["data"]["answer"] == 2


# -----------------------------------------------------------------------
# Schema-bump auto-clear
# -----------------------------------------------------------------------


def test_cache_stats_endpoint(tmp_path, monkeypatch):
    """GET /api/v1/analytics/cache/stats returns the structured stats."""
    import ursa_oscar.config as _config_mod
    from fastapi.testclient import TestClient
    from ursa_oscar.main import create_app

    db_file = tmp_path / "stats.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None
    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        # Seed a couple of entries via the cache directly.
        cache = AnalyticalCache(client.app.state.db)
        cache.set(
            fingerprint="fp_a", tool_name="tool_x",
            params={"q": 1}, result={"ok": True, "data": {}},
            data_version_hash="h", compute_duration_ms=12.3,
        )
        r = client.get("/api/v1/analytics/cache/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_entries"] == 1
        assert "tool_x" in body["by_tool"]

        # Clear endpoint refuses without confirm.
        r_bad = client.post("/api/v1/analytics/cache/clear", json={"confirm": False})
        assert r_bad.status_code == 400

        # Clear with confirm.
        r_clr = client.post("/api/v1/analytics/cache/clear", json={"confirm": True})
        assert r_clr.status_code == 200
        assert r_clr.json()["entries_cleared"] == 1

        # Stats now shows empty.
        r2 = client.get("/api/v1/analytics/cache/stats")
        assert r2.json()["total_entries"] == 0

    _config_mod._settings = None


def test_schema_migration_clears_analytical_cache(tmp_path):
    """A schema bump (v6 -> v7) drops every cache row. Validates the
    apply_migrations hook for the v7 transition. We can't easily fake
    a pre-v7 DB inside this test (apply_migrations always runs to
    SCHEMA_VERSION), so we simulate by pre-populating then re-running
    apply_migrations after directly setting schema_version backwards."""
    db = DuckDBManager(tmp_path / "schema_clear.duckdb", read_only=False)
    apply_migrations(db)
    cache = AnalyticalCache(db)
    for i in range(3):
        cache.set(
            fingerprint=f"fp_{i}", tool_name="tool_x",
            params={"i": i}, result={"ok": True, "data": {}},
            data_version_hash="h", compute_duration_ms=1.0,
        )
    assert cache.stats()["total_entries"] == 3

    # Roll schema_version back to v6 — the next apply_migrations call
    # will see before_version=6 < 7 and trigger the v7 cleanup hook.
    with db.serialized() as conn:
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (6, 'test rollback')"
        )
    apply_migrations(db)

    assert cache.stats()["total_entries"] == 0, (
        "v7 transition should have cleared the cache"
    )
    db.close()
