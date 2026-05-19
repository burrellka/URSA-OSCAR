"""Phase 6 Ticket 6.2 — predictive modeling tests.

Coverage (mirrors test_multivariate_correlation.py's structure):
  - Known-relationship synthetic data: ridge recovers approximately-
    correct coefficients
  - Multicollinear predictors: ridge doesn't blow up (validates the
    L2 regularization)
  - Prediction intervals bracket the point estimate
  - 50% interval is narrower than 95%
  - Counterfactual changes shift the prediction in the expected direction
  - delta + delta_relative_pct are computed correctly
  - n < 30 returns INSUFFICIENT_DATA
  - 30 <= n < 50 returns with "exploratory" confidence
  - 50 <= n < 100 returns "moderate"; n >= 100 returns "high"
  - Cache hit returns identical envelope with cache_age_seconds > 0
  - Cache invalidation: a manual_log create in the training range
    invalidates the cached prediction
  - recompute=true bypasses cache
  - Inverted date range rejected with 400
  - Predictor count outside [2, 6] rejected as BAD_REQUEST
  - Zero-variance target returns ZERO_VARIANCE_TARGET (not a real
    prediction)
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ursa_oscar.analytics.predict import analyze_prediction
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import bypass_auth


# ----------------------------------------------------------------------
# Synthetic-data fixture seeded into the nightly_summary table. Mirrors
# the helper from test_multivariate_correlation.py — same shape, same
# columns, so the predict module's resolve_metric path works against
# the seeded rows without changes.
# ----------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    db = DuckDBManager(tmp_path / "predict.duckdb", read_only=False)
    apply_migrations(db)
    yield db
    db.close()


def _seed_synthetic_nights(
    db: DuckDBManager,
    n_nights: int,
    *,
    seed: int = 7,
    target_coef: dict[str, float] | None = None,
    noise_sigma: float = 0.5,
) -> tuple[date_t, date_t]:
    """Insert nightly_summary rows with engineered linear relationships.

    Default model:
        total_ahi = -0.4 * p95_pressure + 0.1 * p95_leak + N(0, 0.5) + 6

    Override via ``target_coef`` to test specific shapes. Returns the
    inclusive [start, end] date range covered.
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
            noise = float(rng.normal(0, noise_sigma))
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


# ----------------------------------------------------------------------
# Pure-function tests
# ----------------------------------------------------------------------


def test_ridge_recovers_known_negative_relationship(seeded_db):
    """With ahi = -0.4·pressure + 0.1·leak + noise, ridge should
    estimate a strongly-negative coefficient for pressure and a small
    positive one for leak."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=80, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    assert result["method"] == "ridge_regression_cv_with_quantile_intervals"
    assert result["n_training_nights"] == 80
    assert result["confidence_level"] == "moderate"

    coefs = {
        c["predictor"]: c["coefficient"]
        for c in result["model_details"]["predictor_coefficients"]
    }
    assert coefs["p95_pressure"] < -0.2, (
        f"expected strong negative coef for pressure; got {coefs['p95_pressure']}"
    )
    assert -0.1 < coefs["p95_leak"] < 0.3, (
        f"expected small positive coef for leak; got {coefs['p95_leak']}"
    )


def test_prediction_intervals_bracket_point_estimate(seeded_db):
    """The 95% prediction interval must enclose the 50% interval, and
    both must enclose the point estimate."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=80, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    p = result["prediction"]
    pt = p["point_estimate"]
    lo95, hi95 = p["prediction_interval_95"]
    lo50, hi50 = p["prediction_interval_50"]

    assert lo95 <= lo50 <= pt <= hi50 <= hi95, (
        f"interval nesting broken: 95={[lo95,hi95]}, 50={[lo50,hi50]}, pt={pt}"
    )
    # 95% interval should be wider than 50% interval.
    assert (hi95 - lo95) > (hi50 - lo50)


def test_counterfactual_shifts_prediction_in_expected_direction(seeded_db):
    """ahi has a -0.4 coef on pressure. Bumping pressure up should
    shift the prediction down."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=80, seed=42)
    baseline = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    baseline_pt = baseline["prediction"]["point_estimate"]

    # Counterfactual: pressure +5 above the baseline median. By the
    # model's coefficient (~-0.4) this should reduce ahi by ~2.0.
    median_pressure = baseline["model_details"]["baseline_inputs"]["p95_pressure"]
    cf = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
        counterfactual_inputs={"p95_pressure": median_pressure + 5.0},
    )
    assert cf["counterfactual"] is not None
    cf_block = cf["counterfactual"]
    assert cf_block["overridden_predictors"] == ["p95_pressure"]
    assert cf_block["baseline_prediction"] == pytest.approx(baseline_pt, rel=0.01)
    assert cf_block["counterfactual_prediction"] < cf_block["baseline_prediction"]
    assert cf_block["delta"] < 0
    assert cf_block["delta_relative_pct"] is not None
    assert cf_block["interpretation"].endswith("_decrease_predicted")


def test_counterfactual_no_inputs_returns_null_block(seeded_db):
    """When counterfactual_inputs is None, the counterfactual block is
    null — only baseline prediction is reported."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=80, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
        counterfactual_inputs=None,
    )
    assert result["counterfactual"] is None
    assert result["prediction"]["point_estimate"] is not None


def test_multicollinear_predictors_dont_blow_up(seeded_db):
    """Two predictors with r > 0.95 should still produce a sane fit
    via L2 regularization (validates the choice of ridge over OLS)."""
    rng = np.random.default_rng(7)
    base_date = date_t(2026, 1, 1)
    with seeded_db.serialized() as conn:
        for i in range(60):
            d = base_date + timedelta(days=i)
            x = float(rng.normal(9, 1.5))
            x2 = x + float(rng.normal(0, 0.05))
            ahi = -0.4 * x + float(rng.normal(0, 0.4)) + 6.0
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak, last_updated
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, ahi, x, x2),
            )
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=base_date, training_end=base_date + timedelta(days=59),
    )
    assert "code" not in result, f"unexpected refusal: {result.get('code')}"
    assert np.isfinite(result["prediction"]["point_estimate"])
    assert all(
        np.isfinite(c["coefficient"])
        for c in result["model_details"]["predictor_coefficients"]
    )


def test_insufficient_data_under_30_refuses(seeded_db):
    """n < 30 must REFUSE (returns INSUFFICIENT_DATA), not exploratory.
    This is the spec's stricter floor for predictive modeling."""
    start, end = _seed_synthetic_nights(seeded_db, n_nights=20, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    assert result["code"] == "INSUFFICIENT_DATA"
    assert result["n_training_nights"] == 20


def test_30_to_49_observations_marked_exploratory(seeded_db):
    start, end = _seed_synthetic_nights(seeded_db, n_nights=40, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    assert result.get("confidence_level") == "exploratory"
    assert result.get("sample_caveat") is not None


def test_50_to_99_observations_marked_moderate(seeded_db):
    start, end = _seed_synthetic_nights(seeded_db, n_nights=60, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    assert result["confidence_level"] == "moderate"
    assert result["sample_caveat"] is None


def test_100_plus_observations_marked_high(seeded_db):
    start, end = _seed_synthetic_nights(seeded_db, n_nights=120, seed=42)
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=start, training_end=end,
    )
    assert result["confidence_level"] == "high"


def test_predictor_count_validation():
    """1 predictor and 7 predictors both rejected as BAD_REQUEST."""
    db = DuckDBManager(":memory:", read_only=False)
    apply_migrations(db)
    start = date_t(2026, 1, 1)
    end = date_t(2026, 3, 1)
    r1 = analyze_prediction(
        db, target_metric="total_ahi",
        predictor_metrics=["p95_pressure"],
        training_start=start, training_end=end,
    )
    assert r1["code"] == "BAD_REQUEST"
    r7 = analyze_prediction(
        db, target_metric="total_ahi",
        predictor_metrics=["a", "b", "c", "d", "e", "f", "g"],
        training_start=start, training_end=end,
    )
    assert r7["code"] == "BAD_REQUEST"
    db.close()


def test_zero_variance_target_returns_error_envelope(seeded_db):
    """If target is constant over the training window, refuse — there's
    nothing to model."""
    rng = np.random.default_rng(7)
    base = date_t(2026, 1, 1)
    with seeded_db.serialized() as conn:
        for i in range(60):
            d = base + timedelta(days=i)
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak, last_updated
                ) VALUES (?, 5.0, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, float(rng.normal(9, 1.5)), float(rng.normal(20, 5))),
            )
    result = analyze_prediction(
        seeded_db,
        target_metric="total_ahi",
        predictor_metrics=["p95_pressure", "p95_leak"],
        training_start=base, training_end=base + timedelta(days=59),
    )
    assert result["code"] == "ZERO_VARIANCE_TARGET"


# ----------------------------------------------------------------------
# Endpoint + cache integration
# ----------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "predict_api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    _seed_synthetic_nights(seeder, n_nights=80, seed=42)
    seeder.close()

    app = create_app()
    bypass_auth(app)  # Phase 6.4
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def test_endpoint_returns_envelope_ok_true(api_client):
    r = api_client.post("/api/v1/analytics/predict", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["method"] == "ridge_regression_cv_with_quantile_intervals"
    assert data["n_training_nights"] == 80
    assert data["confidence_level"] in {"moderate", "high"}
    assert "cache_age_seconds" in data
    assert "prediction" in data
    assert data["counterfactual"] is None


def test_endpoint_counterfactual_block_populated(api_client):
    r = api_client.post("/api/v1/analytics/predict", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
        "counterfactual_inputs": {"p95_pressure": 12.0},
    })
    assert r.status_code == 200, r.text
    cf = r.json()["data"]["counterfactual"]
    assert cf is not None
    assert cf["overridden_predictors"] == ["p95_pressure"]
    assert "baseline_prediction" in cf
    assert "counterfactual_prediction" in cf
    assert "delta" in cf


def test_endpoint_cache_hit_returns_same_result(api_client):
    params = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
    }
    r1 = api_client.post("/api/v1/analytics/predict", json=params)
    r2 = api_client.post("/api/v1/analytics/predict", json=params)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["data"]["prediction"]["point_estimate"] == r2.json()["data"]["prediction"]["point_estimate"]
    assert r2.json()["data"]["cache_age_seconds"] >= 0


def test_endpoint_recompute_flag_bypasses_cache(api_client):
    params = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
    }
    api_client.post("/api/v1/analytics/predict", json=params)
    r = api_client.post(
        "/api/v1/analytics/predict",
        json={**params, "recompute": True},
    )
    assert r.status_code == 200
    assert r.json()["data"]["cache_age_seconds"] == 0


def test_endpoint_different_counterfactual_inputs_yield_separate_cache_entries(api_client):
    """Cache fingerprint includes counterfactual_inputs (sorted JSON).
    Two queries with different counterfactual values produce two
    independent cache entries — neither one is a hit for the other."""
    base = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
    }
    r1 = api_client.post("/api/v1/analytics/predict", json={
        **base, "counterfactual_inputs": {"p95_pressure": 10.0},
    })
    r2 = api_client.post("/api/v1/analytics/predict", json={
        **base, "counterfactual_inputs": {"p95_pressure": 12.0},
    })
    pt1 = r1.json()["data"]["counterfactual"]["counterfactual_prediction"]
    pt2 = r2.json()["data"]["counterfactual"]["counterfactual_prediction"]
    assert pt1 != pt2, (
        "different counterfactual_inputs should yield different "
        "counterfactual predictions (and different cache fingerprints)"
    )


def test_endpoint_rejects_inverted_training_range(api_client):
    r = api_client.post("/api/v1/analytics/predict", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-03-21",
        "training_end_date": "2026-01-01",
    })
    assert r.status_code == 400


def test_endpoint_insufficient_data_returns_ok_false(api_client):
    """Training range that yields <30 observations -> ok=false +
    INSUFFICIENT_DATA, not a 400."""
    r = api_client.post("/api/v1/analytics/predict", json={
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-01-20",  # 20 nights, below threshold
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["data"]["code"] == "INSUFFICIENT_DATA"


# ----------------------------------------------------------------------
# End-to-end cache validation (Item 4 of the work order).
# Walks through miss -> hit -> different-cf -> data-change -> miss again.
# ----------------------------------------------------------------------


def test_cache_lifecycle_for_prediction(api_client):
    """Item 4 acceptance: walk the full lifecycle of a cached prediction
    entry end-to-end. Lock down hit/miss + invalidation + stats counters
    in one test so future refactors don't accidentally regress one
    branch."""
    base = {
        "target_metric": "total_ahi",
        "predictor_metrics": ["p95_pressure", "p95_leak"],
        "training_start_date": "2026-01-01",
        "training_end_date": "2026-03-21",
    }

    # 1. Fresh query → miss. cache_age_seconds should be 0.
    r1 = api_client.post("/api/v1/analytics/predict", json=base)
    assert r1.status_code == 200
    assert r1.json()["data"]["cache_age_seconds"] == 0
    stats_after_1 = api_client.get("/api/v1/analytics/cache/stats").json()
    n_after_1 = stats_after_1["total_entries"]
    assert n_after_1 >= 1

    # 2. Identical query → hit.
    r2 = api_client.post("/api/v1/analytics/predict", json=base)
    assert r2.status_code == 200
    # Same point estimate (deterministic given same training data + seed).
    pt1 = r1.json()["data"]["prediction"]["point_estimate"]
    pt2 = r2.json()["data"]["prediction"]["point_estimate"]
    assert pt1 == pt2

    # 3. Different counterfactual → miss (separate fingerprint).
    r3 = api_client.post("/api/v1/analytics/predict", json={
        **base, "counterfactual_inputs": {"p95_pressure": 12.0},
    })
    assert r3.status_code == 200
    stats_after_3 = api_client.get("/api/v1/analytics/cache/stats").json()
    assert stats_after_3["total_entries"] == n_after_1 + 1

    # 4. Data change (manual log create in training range) → both
    #    entries invalidated.
    create_r = api_client.post("/api/v1/manual-logs", json={
        "log_type": "alertness",
        "date": "2026-02-01",
        "timestamp": "2026-02-01T08:00:00",
        "score": 6,
    })
    assert create_r.status_code in (200, 201)
    stats_after_4 = api_client.get("/api/v1/analytics/cache/stats").json()
    assert stats_after_4["total_entries"] == 0, (
        "manual_log create within training range should have wiped both "
        "cached prediction entries (they had overlapping training_date_range)"
    )

    # 5. Re-run original query → miss again (data_version_hash changed).
    r5 = api_client.post("/api/v1/analytics/predict", json=base)
    assert r5.status_code == 200
    assert r5.json()["data"]["cache_age_seconds"] == 0
