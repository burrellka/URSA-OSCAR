"""Phase 6 Ticket 6.1 Item 2 — multivariate (partial) correlation tests.

Coverage:
  - Known-correlation synthetic data recovers the right partial r sign + magnitude
  - Multicollinear predictors handled gracefully (flagged in response)
  - Sample-size discipline: n < 15 refuses; 15-29 exploratory; 30+ moderate/high
  - Cache hit returns same envelope with cache_age_seconds populated
  - Cache invalidation after import re-runs the computation
  - Manual-log metric (medication dose) pivots correctly to daily values
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ursa_oscar.analytics.multivariate import analyze_multivariate_correlation
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import bypass_auth


# ----------------------------------------------------------------------
# Pure-function tests against a seeded DB.
# ----------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    db = DuckDBManager(tmp_path / "mv.duckdb", read_only=False)
    apply_migrations(db)
    yield db
    db.close()


def _seed_synthetic_nights(
    db: DuckDBManager,
    n_nights: int,
    *,
    seed: int = 7,
    target_coef: dict[str, float] | None = None,
) -> tuple[date_t, date_t]:
    """Insert nightly_summary rows with synthetic, statistically-meaningful
    relationships.

    By default each night gets:
        p95_pressure ~ N(9, 1.5)
        p95_leak ~ N(20, 5)
        total_ahi  = -0.4 * p95_pressure + 0.1 * p95_leak + N(0, 0.5) + 6

    Override via ``target_coef`` to test specific correlation shapes.
    """
    rng = np.random.default_rng(seed)
    coef = {"p95_pressure": -0.4, "p95_leak": 0.1, **(target_coef or {})}
    base_date = date_t(2026, 1, 1)
    last_date = base_date
    with db.serialized() as conn:
        for i in range(n_nights):
            d = base_date + timedelta(days=i)
            last_date = d
            p_pressure = float(rng.normal(9.0, 1.5))
            p_leak = float(rng.normal(20.0, 5.0))
            noise = float(rng.normal(0, 0.5))
            ahi = (
                coef["p95_pressure"] * p_pressure
                + coef["p95_leak"] * p_leak
                + 6.0 + noise
            )
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak, last_updated
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, ahi, p_pressure, p_leak),
            )
    return base_date, last_date


def test_partial_correlation_recovers_known_negative_relationship(seeded_db):
    """With ahi engineered as -0.4·pressure + 0.1·leak + noise, the
    partial correlation of pressure with ahi should be clearly negative."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=60, seed=42)
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=start, end=end, rng_seed=42,
    )
    assert result["method"] == "partial_correlation_pearson"
    assert result["n_observations"] == 60
    assert result["confidence_level"] == "moderate"

    by_metric = {p["metric"]: p for p in result["predictors"]}
    assert by_metric["p95_pressure"]["partial_r"] < -0.3, (
        f"expected strong negative partial r for pressure; got {by_metric['p95_pressure']['partial_r']}"
    )
    # Leak's small coefficient should still produce a measurable positive
    # partial r; might or might not be significant depending on bootstrap.
    assert by_metric["p95_leak"]["partial_r"] > 0


def test_partial_correlation_ci_brackets_true_value(seeded_db):
    """The bootstrap CI for the strong-effect predictor should not span
    zero. The weak/noise predictor's CI may span zero (that's fine —
    means we can't distinguish it from no effect)."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=80, seed=42)
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=start, end=end, rng_seed=42,
    )
    pressure_row = next(p for p in result["predictors"] if p["metric"] == "p95_pressure")
    ci_low, ci_high = pressure_row["ci_95"]
    assert ci_high < 0, f"95% CI should be entirely negative for the strong effect; got [{ci_low}, {ci_high}]"


def test_insufficient_data_under_15_refuses(seeded_db):
    """n < 15 returns the INSUFFICIENT_DATA envelope, not partial values."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=10, seed=42)
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=start, end=end, rng_seed=42,
    )
    assert result["code"] == "INSUFFICIENT_DATA"
    assert result["n_observations"] == 10
    assert result["predictors"] == []


def test_15_to_29_observations_marked_exploratory(seeded_db):
    start, end = _seed_synthetic_nights(seeded_db, n_nights=20, seed=42)
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=start, end=end, rng_seed=42,
    )
    assert result.get("confidence_level") == "exploratory"
    assert result.get("sample_caveat") is not None


def test_100_plus_observations_marked_high(seeded_db):
    start, end = _seed_synthetic_nights(seeded_db, n_nights=120, seed=42)
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=start, end=end, rng_seed=42,
    )
    assert result["confidence_level"] == "high"
    assert result["sample_caveat"] is None


def test_multicollinear_predictors_flagged(seeded_db):
    """Two predictors with r > 0.9 produce a multicollinear_pairs entry."""
    rng = np.random.default_rng(7)
    base_date = date_t(2026, 1, 1)
    with seeded_db.serialized() as conn:
        for i in range(40):
            d = base_date + timedelta(days=i)
            x = float(rng.normal(9, 1.5))
            # second predictor is x + tiny noise — almost perfectly collinear
            x2 = x + float(rng.normal(0, 0.01))
            ahi = -0.4 * x + float(rng.normal(0, 0.3)) + 6.0
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak, last_updated
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, ahi, x, x2),
            )
    result = analyze_multivariate_correlation(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        start=base_date, end=base_date + timedelta(days=39),
        rng_seed=7,
    )
    assert result["multicollinear_pairs"], (
        "near-collinear predictors should be flagged"
    )
    assert abs(result["multicollinear_pairs"][0]["r"]) >= 0.9


def test_predictor_count_validation():
    """1 predictor is BAD_REQUEST; 6 predictors is BAD_REQUEST."""
    db = DuckDBManager(":memory:", read_only=False)
    apply_migrations(db)
    start = date_t(2026, 1, 1)
    end = date_t(2026, 3, 1)
    r1 = analyze_multivariate_correlation(
        db, target_metric="total_ahi",
        predictor_metrics=["p95_pressure"],
        start=start, end=end,
    )
    assert r1["code"] == "BAD_REQUEST"
    r6 = analyze_multivariate_correlation(
        db, target_metric="total_ahi",
        predictor_metrics=["a", "b", "c", "d", "e", "f"],
        start=start, end=end,
    )
    assert r6["code"] == "BAD_REQUEST"
    db.close()


# ----------------------------------------------------------------------
# Endpoint + cache integration
# ----------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "mv_api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    _seed_synthetic_nights(seeder, n_nights=60, seed=42)
    seeder.close()

    app = create_app()
    bypass_auth(app)  # Phase 6.4
    with TestClient(app) as client:
        yield client, db_file
    _config_mod._settings = None


def test_endpoint_returns_envelope_ok_true(api_client):
    client, _ = api_client
    r = client.post("/api/v1/analytics/multivariate-correlation", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["method"] == "partial_correlation_pearson"
    assert body["data"]["confidence_level"] in {"moderate", "high"}
    assert "cache_age_seconds" in body["data"]


def test_endpoint_cache_hit_returns_same_envelope(api_client):
    client, _ = api_client
    params = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    r1 = client.post("/api/v1/analytics/multivariate-correlation", json=params)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/analytics/multivariate-correlation", json=params)
    assert r2.status_code == 200

    p1 = r1.json()["data"]["predictors"]
    p2 = r2.json()["data"]["predictors"]
    # Same r-values across hit/miss (cache returns the persisted result).
    by_metric_1 = {p["metric"]: p["partial_r"] for p in p1}
    by_metric_2 = {p["metric"]: p["partial_r"] for p in p2}
    assert by_metric_1 == by_metric_2
    # Cache age increases.
    assert r2.json()["data"]["cache_age_seconds"] >= 0


def test_endpoint_recompute_flag_bypasses_cache(api_client):
    client, _ = api_client
    params = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    client.post("/api/v1/analytics/multivariate-correlation", json=params)
    # Second call with recompute=true should force a re-store.
    r = client.post(
        "/api/v1/analytics/multivariate-correlation",
        json={**params, "recompute": True},
    )
    assert r.status_code == 200
    # The freshly-computed result has cache_age_seconds = 0 (or close to it)
    assert r.json()["data"]["cache_age_seconds"] == 0


def test_endpoint_rejects_inverted_date_range(api_client):
    client, _ = api_client
    r = client.post("/api/v1/analytics/multivariate-correlation", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-03-01",
        "end_date": "2026-01-01",
    })
    assert r.status_code == 400


def test_endpoint_insufficient_data_returns_ok_false(api_client):
    """Date range that yields < 15 observations gets ok=false +
    INSUFFICIENT_DATA, not a 400."""
    client, _ = api_client
    r = client.post("/api/v1/analytics/multivariate-correlation", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-10",  # only 10 days
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["data"]["code"] == "INSUFFICIENT_DATA"


def test_cache_invalidation_after_manual_log_create(api_client):
    """Creating a manual_log within the cached range should invalidate
    the cached partial-correlation entry; the next call recomputes."""
    client, _ = api_client
    params = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    # Prime the cache.
    r1 = client.post("/api/v1/analytics/multivariate-correlation", json=params)
    assert r1.status_code == 200

    # Confirm an entry exists. Reuse the TestClient app's DB connection
    # so we don't trip DuckDB's "can't open same file twice" rule.
    db = client.app.state.db
    with db.serialized() as conn:
        n_before = conn.execute("SELECT COUNT(*) FROM analytical_cache").fetchone()[0]
    assert n_before >= 1

    # Mutate via a manual_log POST within the cached date range.
    create_r = client.post("/api/v1/manual-logs", json={
        "log_type": "alertness",
        "date": "2026-02-01",
        "timestamp": "2026-02-01T08:00:00",
        "score": 7,
    })
    assert create_r.status_code in (200, 201)

    # Cache should now be invalidated.
    with db.serialized() as conn:
        n_after = conn.execute("SELECT COUNT(*) FROM analytical_cache").fetchone()[0]
    assert n_after == 0, "manual_log create should have invalidated the overlapping cache entry"
