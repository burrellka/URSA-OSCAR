"""Integration tests for Phase 3 analytics + hard-delete endpoints.

Backed by the canonical regression fixture (4-5 nights) imported via the
existing api_client fixture in test_api_endpoints.py. Shared fixture
isn't imported across files — each test in this module re-derives the
test client via the standalone fixture below.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import FIXTURE_ROOT, bypass_auth


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    import_path(FIXTURE_ROOT, seeder)
    seeder.close()

    app = create_app()
    bypass_auth(app)  # Phase 6.4
    with TestClient(app) as client:
        yield client

    _config_mod._settings = None


# =====================================================================
# Analytics endpoints (Items 5A-5D)
# =====================================================================

def test_analytics_available_metrics(api_client):
    r = api_client.get("/api/v1/analytics/available-metrics")
    assert r.status_code == 200
    body = r.json()
    assert "nightly_metrics" in body
    assert "manual_metrics" in body
    assert "total_ahi" in body["nightly_metrics"]
    assert "p95_pressure" in body["nightly_metrics"]


def test_analytics_compare_periods_smoke(api_client):
    """Sanity check that compare-periods returns the expected shape and
    computes a non-trivial delta on the canonical fixture."""
    r = api_client.get(
        "/api/v1/analytics/compare-periods",
        params={
            "period_a_start": "2026-05-07",
            "period_a_end": "2026-05-08",
            "period_b_start": "2026-05-09",
            "period_b_end": "2026-05-10",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period_a"]["n_nights"] == 2
    assert body["period_b"]["n_nights"] == 2
    ahi = body["metrics"]["total_ahi"]
    assert ahi["absolute_delta"] is not None
    # Period A (5/7+5/8) AHI mean is ~11.6; period B (5/9+5/10) is ~5.25.
    # Both periods should produce non-None mean.
    assert ahi["period_a"]["mean"] > 0
    assert ahi["period_b"]["mean"] > 0
    # Interpretation should be a non-empty string.
    assert isinstance(ahi["interpretation"], str)
    assert len(ahi["interpretation"]) > 0


def test_analytics_correlation_two_cpap_metrics(api_client):
    r = api_client.get(
        "/api/v1/analytics/correlation",
        params={
            "metric_a": "total_ahi",
            "metric_b": "p95_pressure",
            "start_date": "2026-05-07",
            "end_date": "2026-05-10",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metric_a"] == "total_ahi"
    assert body["metric_b"] == "p95_pressure"
    assert body["n_pairs"] == 4
    # n=4 is under 30 — sample size warning expected.
    assert body["sample_size_warning"] is not None


def test_analytics_correlation_lag_days(api_client):
    """Lag-days shift must affect n_pairs (shifting reduces overlap)."""
    base = api_client.get(
        "/api/v1/analytics/correlation",
        params={
            "metric_a": "total_ahi", "metric_b": "p95_pressure",
            "start_date": "2026-05-07", "end_date": "2026-05-10",
            "lag_days": 0,
        },
    ).json()
    lagged = api_client.get(
        "/api/v1/analytics/correlation",
        params={
            "metric_a": "total_ahi", "metric_b": "p95_pressure",
            "start_date": "2026-05-07", "end_date": "2026-05-10",
            "lag_days": 2,
        },
    ).json()
    # With lag=2 and a 4-day range, overlap drops or stays same — never
    # exceeds. (DuckDB nightly_summary has data for 5/7..5/10; lag=2
    # tries to pair 5/7-with-5/9, 5/8-with-5/10. Both pairs land if
    # data exists.)
    assert lagged["n_pairs"] <= base["n_pairs"]


def test_analytics_correlation_insufficient_data(api_client):
    r = api_client.get(
        "/api/v1/analytics/correlation",
        params={
            "metric_a": "total_ahi", "metric_b": "p95_pressure",
            "start_date": "2026-05-07", "end_date": "2026-05-07",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["interpretation"] == "insufficient_data"
    assert body["pearson_r"] is None


def test_analytics_trend_smoke(api_client):
    r = api_client.get(
        "/api/v1/analytics/trend",
        params={
            "metric": "total_ahi",
            "start_date": "2026-05-07",
            "end_date": "2026-05-10",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # n=4 is below the threshold of 5 → insufficient_data branch.
    assert body["interpretation"] == "insufficient_data"
    assert body["n_nights"] == 4


def test_analytics_manual_log_summary_empty(api_client):
    r = api_client.get(
        "/api/v1/analytics/manual-log-summary",
        params={"start_date": "2026-05-01", "end_date": "2026-05-31"},
    )
    assert r.status_code == 200
    body = r.json()
    # Fresh seed has zero manual logs — every type should report count 0.
    assert body["total_entries"] == 0
    for t in ("medication", "symptom", "alertness", "sleep_environment", "freeform"):
        assert body["by_type"][t]["count"] == 0


def test_analytics_unknown_metric_400(api_client):
    r = api_client.get(
        "/api/v1/analytics/trend",
        params={
            "metric": "not_a_real_metric",
            "start_date": "2026-05-07", "end_date": "2026-05-10",
        },
    )
    assert r.status_code == 400


# =====================================================================
# 0.13.4 — usage-rate breakdown surfaced on analytics responses.
# =====================================================================


def test_compare_periods_includes_usage_breakdown_full_usage(api_client):
    """Period a + b each span 2 calendar days; the canonical fixture has
    data for every one of those nights, so usage = 100%."""
    r = api_client.get(
        "/api/v1/analytics/compare-periods",
        params={
            "period_a_start": "2026-05-07",
            "period_a_end": "2026-05-08",
            "period_b_start": "2026-05-09",
            "period_b_end": "2026-05-10",
        },
    )
    body = r.json()
    for side in ("period_a", "period_b"):
        usage = body[side]
        assert usage["n_nights_in_range"] == 2
        assert usage["n_nights_with_therapy"] == 2
        assert usage["n_nights_skipped"] == 0
        assert usage["usage_rate_pct"] == 100.0


def test_compare_periods_usage_breakdown_with_gaps(api_client):
    """Widen each period to include dates the operator never used the
    machine. The fixture has 5 nights of data (5/7, 5/8, 5/9, 5/10,
    5/12); 5/1-5/12 is 12 calendar days → 5/12 = 41.7% usage."""
    r = api_client.get(
        "/api/v1/analytics/compare-periods",
        params={
            "period_a_start": "2026-05-01",
            "period_a_end": "2026-05-12",
            "period_b_start": "2026-05-01",
            "period_b_end": "2026-05-12",
        },
    )
    body = r.json()
    usage = body["period_a"]
    assert usage["n_nights_in_range"] == 12
    assert usage["n_nights_with_therapy"] == 5
    assert usage["n_nights_skipped"] == 7
    assert usage["usage_rate_pct"] == 41.7


def test_trend_response_includes_usage_breakdown(api_client):
    """Trend should surface the same breakdown even on the
    insufficient-data branch (n_nights < 5 has nothing to do with
    n_nights_with_therapy)."""
    r = api_client.get(
        "/api/v1/analytics/trend",
        params={
            "metric": "total_ahi",
            "start_date": "2026-05-01",
            "end_date": "2026-05-12",
        },
    )
    body = r.json()
    assert body["n_nights_in_range"] == 12
    assert body["n_nights_with_therapy"] == 5
    assert body["n_nights_skipped"] == 7
    assert body["usage_rate_pct"] == 41.7


def test_usage_breakdown_handles_inverted_range(api_client):
    """Defensive: if start > end (shouldn't happen via API validation
    but the helper still has to be safe), all fields go to zero. Uses
    the existing app DB connection to avoid the DuckDB writer-lock
    conflict that a second connection would hit."""
    from datetime import date

    from ursa_oscar.analytics.usage_rate import compute_usage_breakdown

    u = compute_usage_breakdown(
        api_client.app.state.db,
        date(2026, 5, 10),
        date(2026, 5, 7),
    )
    assert u["n_nights_in_range"] == 0
    assert u["n_nights_with_therapy"] == 0
    assert u["n_nights_skipped"] == 0
    assert u["usage_rate_pct"] == 0.0


# =====================================================================
# Hard-delete purge endpoints
# =====================================================================

def test_delete_preview_single_night(api_client):
    r = api_client.post(
        "/api/v1/nights/preview-delete",
        json={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nights"] == 1
    assert body["events"] == 78  # Canonical 5/8 has 47 CA + 28 OA + 2 A + 1 H = 78
    assert "2026-05-08" in body["dates"]
    assert body["manual_logs"] == 0


def test_delete_single_night_removes_data(api_client):
    # Confirm pre-delete state
    r = api_client.get("/api/v1/night/2026-05-08")
    assert r.status_code == 200

    # Delete
    r = api_client.delete("/api/v1/nights/2026-05-08")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-05-08"
    assert body["events_deleted"] == 78
    assert body["timeseries_rows_deleted"] > 0
    assert body["manual_logs_deleted"] == 0  # Default keep

    # Post-delete state: 404 + events list empty for that date.
    r = api_client.get("/api/v1/night/2026-05-08")
    assert r.status_code == 404
    events = api_client.get("/api/v1/events", params={"date": "2026-05-08"}).json()
    assert events == []


def test_delete_404_on_nonexistent(api_client):
    r = api_client.delete("/api/v1/nights/2099-01-01")
    assert r.status_code == 404


def test_delete_range_reports_before_after_db_size(api_client):
    """Range-delete must include db_size_before_mb + db_size_after_mb."""
    r = api_client.delete(
        "/api/v1/nights",
        params={"start_date": "2026-05-09", "end_date": "2026-05-10"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nights_deleted"] == 2
    assert body["events_deleted"] > 0
    assert body["timeseries_rows_deleted"] > 0
    # Both size fields should be present (numeric; precise reclaim
    # behavior depends on DuckDB version — we just assert reporting).
    assert "db_size_before_mb" in body
    assert "db_size_after_mb" in body


def test_admin_checkpoint(api_client):
    r = api_client.post("/api/v1/admin/checkpoint")
    assert r.status_code == 200
    body = r.json()
    assert "db_size_before_mb" in body
    assert "db_size_after_mb" in body
