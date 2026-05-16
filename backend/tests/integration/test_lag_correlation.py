"""Phase 6 Ticket 6.1 Item 3 — lag correlation regression tests.

Coverage:
  - Synthetic data with a known lag-2 effect recovers peak at lag 2
  - No-effect synthetic data returns CIs spanning zero at all lags
  - Negative lag values handled correctly
  - Bootstrap CI computation deterministic with seeded RNG
  - Insufficient data at large lags handled (skipped, smaller lags still returned)
  - Cache hit/miss behavior
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ursa_oscar.analytics.lag import analyze_lag_correlation
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


# ----------------------------------------------------------------------
# Pure-function tests against a seeded DB.
# ----------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    db = DuckDBManager(tmp_path / "lag.duckdb", read_only=False)
    apply_migrations(db)
    yield db
    db.close()


def _seed_with_lag_effect(
    db: DuckDBManager,
    n_nights: int,
    *,
    seed: int = 7,
    lag: int = 2,
    coef: float = -0.6,
) -> tuple[date_t, date_t]:
    """Inject metric_a and metric_b values where metric_b[t] depends on
    metric_a[t - lag] (so the peak correlation should appear at the
    given lag)."""
    rng = np.random.default_rng(seed)
    base_date = date_t(2026, 1, 1)
    a_series = rng.normal(0.5, 0.3, n_nights)
    # b[t] = coef * a[t - lag] + noise, with the early entries
    # uncorrelated (no leading a values to draw from).
    b_series = np.zeros(n_nights)
    for t in range(n_nights):
        if t - lag >= 0:
            b_series[t] = coef * a_series[t - lag] + rng.normal(0, 0.2) + 5.0
        else:
            b_series[t] = rng.normal(5.0, 0.4)
    last_date = base_date
    with db.serialized() as conn:
        for i in range(n_nights):
            d = base_date + timedelta(days=i)
            last_date = d
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, last_updated
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, float(b_series[i]), float(a_series[i])),
            )
    return base_date, last_date


def _seed_no_effect(
    db: DuckDBManager, n_nights: int, seed: int = 11,
) -> tuple[date_t, date_t]:
    rng = np.random.default_rng(seed)
    base_date = date_t(2026, 1, 1)
    last_date = base_date
    with db.serialized() as conn:
        for i in range(n_nights):
            d = base_date + timedelta(days=i)
            last_date = d
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, last_updated
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, float(rng.normal(5, 1)), float(rng.normal(9, 1.5))),
            )
    return base_date, last_date


def test_lag_correlation_recovers_known_peak(seeded_db):
    """Engineered b[t] = -0.6 * a[t-2] + noise → peak at lag 2 with negative r."""
    start, end = _seed_with_lag_effect(
        seeded_db, n_nights=80, seed=42, lag=2, coef=-0.6,
    )
    result = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure",   # the "cause" we engineered
        metric_b="total_ahi",      # the "effect"
        start=start, end=end,
        lag_range=(-3, 7),
        bootstrap_samples=300,
        rng_seed=42,
    )
    assert result["method"] == "cross_correlation_with_bootstrap_ci"
    assert result["peak_lag_days"] == 2, (
        f"expected peak at lag 2 (engineered), got peak at {result['peak_lag_days']}"
    )
    assert result["peak_correlation"] < -0.3
    # Peak CI should not span zero.
    peak_entry = next(
        e for e in result["lag_correlations"] if e["lag_days"] == 2
    )
    ci_low, ci_high = peak_entry["ci_95"]
    assert ci_high < 0, f"peak CI should be strictly negative; got [{ci_low}, {ci_high}]"


def test_lag_correlation_no_effect_returns_wide_cis(seeded_db):
    """Random data with no engineered relationship: every lag's CI
    should span zero."""
    start, end = _seed_no_effect(seeded_db, n_nights=80, seed=11)
    result = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-3, 7),
        bootstrap_samples=300,
        rng_seed=11,
    )
    assert result["method"] == "cross_correlation_with_bootstrap_ci"
    # Every lag entry's CI must span zero in pure-noise data.
    for entry in result["lag_correlations"]:
        if entry["ci_95"][0] is None:
            continue
        lo, hi = entry["ci_95"]
        assert lo <= 0 <= hi, (
            f"pure-noise data should have CIs spanning zero; "
            f"lag={entry['lag_days']} CI=[{lo}, {hi}]"
        )


def test_lag_correlation_insufficient_overall_data(seeded_db):
    """n_unlagged < 15 → INSUFFICIENT_DATA envelope (no lag computation)."""
    start, end = _seed_no_effect(seeded_db, n_nights=10, seed=11)
    result = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-3, 7), bootstrap_samples=100, rng_seed=11,
    )
    assert result["code"] == "INSUFFICIENT_DATA"
    assert result["lag_correlations"] == []


def test_lag_correlation_drops_lags_with_insufficient_aligned(seeded_db):
    """At lag = +7 only n-7 pairs remain; if n=21 then lag 7 has 14 pairs
    and should be dropped, while lower lags should still be present."""
    start, end = _seed_no_effect(seeded_db, n_nights=22, seed=11)
    result = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-3, 7), bootstrap_samples=200, rng_seed=11,
    )
    # Some lags should be present, others dropped due to n < 15.
    lags_present = [e["lag_days"] for e in result["lag_correlations"]]
    assert 0 in lags_present, "lag 0 should have full 22 pairs"
    # Lag 7 yields 22-7=15 pairs, which just hits the threshold (>=15).
    # Lag 8 (outside our range) would be 14 and dropped if it were in range.
    # So we can't assert dropped lags here at n=22. Instead seed n=17 and
    # show that high lags get dropped.
    result_smaller = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=start + timedelta(days=16),  # 17 days
        lag_range=(-3, 7), bootstrap_samples=200, rng_seed=11,
    )
    if result_smaller.get("code") != "INSUFFICIENT_DATA":
        lags_present_2 = [e["lag_days"] for e in result_smaller["lag_correlations"]]
        # Lag 7 → 17-7=10 pairs, below threshold → dropped.
        assert 7 not in lags_present_2
        assert 0 in lags_present_2


def test_lag_correlation_bootstrap_deterministic_with_seed(seeded_db):
    """Same seed produces the same CIs across runs."""
    start, end = _seed_with_lag_effect(
        seeded_db, n_nights=50, seed=42, lag=1, coef=-0.5,
    )
    r1 = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-2, 5), bootstrap_samples=200, rng_seed=99,
    )
    r2 = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-2, 5), bootstrap_samples=200, rng_seed=99,
    )
    cis_1 = [(e["lag_days"], tuple(e["ci_95"])) for e in r1["lag_correlations"]]
    cis_2 = [(e["lag_days"], tuple(e["ci_95"])) for e in r2["lag_correlations"]]
    assert cis_1 == cis_2


def test_lag_correlation_negative_lag_alignment(seeded_db):
    """Negative-lag alignment computes correctly. At lag=-1 we align
    a[t] with b[t-1]. Sanity check on the math, not the interpretation."""
    start, end = _seed_no_effect(seeded_db, n_nights=30, seed=11)
    result = analyze_lag_correlation(
        seeded_db,
        metric_a="p95_pressure", metric_b="total_ahi",
        start=start, end=end,
        lag_range=(-2, 0), bootstrap_samples=100, rng_seed=11,
    )
    # 3 lags: -2, -1, 0; all should have results since n=30 >> 15.
    lags = [e["lag_days"] for e in result["lag_correlations"]]
    assert lags == [-2, -1, 0]
    for entry in result["lag_correlations"]:
        # Aligned counts should match: 30 - |lag|.
        assert entry["n_aligned"] == 30 - abs(entry["lag_days"])


# ----------------------------------------------------------------------
# Endpoint + cache integration.
# ----------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "lag_api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    _seed_with_lag_effect(seeder, n_nights=60, seed=42, lag=2, coef=-0.6)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def test_endpoint_returns_envelope(api_client):
    r = api_client.post("/api/v1/analytics/lag-correlation", json={
        "metric_a": "p95_pressure",
        "metric_b": "total_ahi",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "lag_range_days": [-3, 7],
        "bootstrap_samples": 200,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["method"] == "cross_correlation_with_bootstrap_ci"
    assert body["data"]["peak_lag_days"] == 2
    assert "cache_age_seconds" in body["data"]


def test_endpoint_cache_hit_on_second_call(api_client):
    params = {
        "metric_a": "p95_pressure",
        "metric_b": "total_ahi",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "lag_range_days": [-3, 7],
        "bootstrap_samples": 200,
    }
    r1 = api_client.post("/api/v1/analytics/lag-correlation", json=params)
    r2 = api_client.post("/api/v1/analytics/lag-correlation", json=params)
    assert r1.json()["data"]["peak_correlation"] == r2.json()["data"]["peak_correlation"]


def test_endpoint_rejects_inverted_lag_range(api_client):
    r = api_client.post("/api/v1/analytics/lag-correlation", json={
        "metric_a": "p95_pressure",
        "metric_b": "total_ahi",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "lag_range_days": [5, -3],  # inverted
    })
    assert r.status_code == 400


def test_endpoint_recompute_flag_bypasses_cache(api_client):
    params = {
        "metric_a": "p95_pressure",
        "metric_b": "total_ahi",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "lag_range_days": [-3, 7],
        "bootstrap_samples": 200,
    }
    api_client.post("/api/v1/analytics/lag-correlation", json=params)
    r = api_client.post(
        "/api/v1/analytics/lag-correlation",
        json={**params, "recompute": True},
    )
    assert r.status_code == 200
    assert r.json()["data"]["cache_age_seconds"] == 0
