"""Phase 0.13.5 — safe_projection guard tests.

Pure-unit tests against analytics/projections.py. These don't need a
DB fixture; they construct slopes/intercepts directly and verify the
two guards (sample-size + physical bounds) compose correctly.

Coverage:
  - Sample-size rule:
    * n < ABSOLUTE_MIN_SAMPLES → suppressed regardless of projection
    * n < projection * 0.25 → suppressed
    * n >= projection * 0.25 AND >= ABSOLUTE_MIN_SAMPLES → passes
  - Bounds clamp:
    * In-bounds value passes through unchanged
    * Below-lower-bound clamps to lower (e.g., negative AHI → 0)
    * Above-upper-bound clamps to upper (e.g., 200 AHI → 100)
  - Per-metric coverage:
    * AHI metrics → [0, 100]
    * Pressure metrics → [4, 25]
    * Leak metrics → variable upper bounds
  - Unregistered metric → no clamping, raw value returned
  - Explanation strings are operator-readable (non-empty when set)

Plus integration tests against /api/v1/analytics/trend that confirm
the new projection block shape ships through correctly.
"""
from __future__ import annotations

import pytest

from ursa_oscar.analytics.projections import (
    ABSOLUTE_MIN_SAMPLES,
    METRIC_BOUNDS,
    SAMPLE_TO_PROJECTION_RATIO,
    SafeProjection,
    safe_projection,
)


# ---------------------------------------------------------------------------
# Sample-size rule
# ---------------------------------------------------------------------------


def test_below_absolute_min_samples_is_suppressed():
    """Even with short projection horizons, below ABSOLUTE_MIN_SAMPLES
    is always suppressed."""
    result = safe_projection(
        metric="total_ahi",
        slope=-0.5, intercept=20.0, last_x=3.0,
        projection_days=7, n_samples=3,
    )
    assert result.projected_value is None
    assert result.suppressed_reason == "insufficient_samples"
    assert result.explanation and "at least 5" in result.explanation


def test_25pct_ratio_violation_is_suppressed():
    """Need n >= projection_days * 0.25 (with int() truncation). With
    30-day projection: int(30 * 0.25) = 7, so n=6 fails."""
    result = safe_projection(
        metric="total_ahi",
        slope=-0.5, intercept=20.0, last_x=5.0,
        projection_days=30, n_samples=6,
    )
    assert result.projected_value is None
    assert result.suppressed_reason == "insufficient_samples"
    # Should mention the required count (7 for 30-day at 25%, after
    # int() truncation)
    assert result.explanation and "7" in result.explanation


def test_25pct_ratio_satisfied_passes():
    """n=7 with projection_days=30 → just meets the int(0.25 * 30)=7
    threshold."""
    result = safe_projection(
        metric="total_ahi",
        slope=-0.1, intercept=15.0, last_x=6.0,
        projection_days=30, n_samples=7,
    )
    assert result.projected_value is not None
    assert result.suppressed_reason is None


def test_long_projection_horizon_requires_more_samples():
    """60-day projection needs at least int(60 * 0.25)=15 samples.
    n=14 fails; n=15 passes."""
    result_fail = safe_projection(
        metric="total_ahi",
        slope=-0.1, intercept=15.0, last_x=13.0,
        projection_days=60, n_samples=14,
    )
    assert result_fail.projected_value is None

    result_pass = safe_projection(
        metric="total_ahi",
        slope=-0.1, intercept=15.0, last_x=14.0,
        projection_days=60, n_samples=15,
    )
    assert result_pass.projected_value is not None


# ---------------------------------------------------------------------------
# Bounds clamp
# ---------------------------------------------------------------------------


def test_in_bounds_value_passes_through():
    """Reasonable AHI projection: intercept 10, slope -0.05/day,
    last_x=29, project 30 days forward → 10 + (-0.05)*(29+30) = 7.05.
    Well within [0, 100]."""
    result = safe_projection(
        metric="total_ahi",
        slope=-0.05, intercept=10.0, last_x=29.0,
        projection_days=30, n_samples=20,
    )
    assert result.projected_value == pytest.approx(7.05, abs=0.01)
    assert result.raw_projected_value == pytest.approx(7.05, abs=0.01)
    assert result.clamped is False
    assert result.suppressed_reason is None


def test_below_lower_bound_clamps_to_lower():
    """The real-world bug: current AHI 5, slope -1.0/day, 30 days out
    → -25. Should clamp to 0."""
    result = safe_projection(
        metric="total_ahi",
        slope=-1.0, intercept=25.0, last_x=20.0,
        projection_days=30, n_samples=20,
    )
    assert result.projected_value == 0.0
    assert result.raw_projected_value < 0  # the bug value, preserved for transparency
    assert result.clamped is True
    assert result.suppressed_reason == "clamped_to_lower_bound"
    assert result.explanation and "physical floor" in result.explanation


def test_above_upper_bound_clamps_to_upper():
    """Steep positive slope projects AHI above 100 → clamp to 100."""
    result = safe_projection(
        metric="total_ahi",
        slope=5.0, intercept=10.0, last_x=20.0,
        projection_days=30, n_samples=20,
    )
    assert result.projected_value == 100.0
    assert result.raw_projected_value > 100
    assert result.clamped is True
    assert result.suppressed_reason == "clamped_to_upper_bound"
    assert result.explanation and "ceiling" in result.explanation


# ---------------------------------------------------------------------------
# Per-metric bounds coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", [
    "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index", "rera_index",
])
def test_ahi_family_floor_is_zero(metric):
    """All AHI metrics share the [0, 100] bound — negative projections
    clamp to 0."""
    result = safe_projection(
        metric=metric, slope=-2.0, intercept=10.0, last_x=10.0,
        projection_days=30, n_samples=20,
    )
    assert result.projected_value == 0.0
    assert result.clamped is True


@pytest.mark.parametrize("metric", ["median_pressure", "p95_pressure", "p995_pressure"])
def test_pressure_family_clamps_to_device_range(metric):
    """Pressure metrics floor at 4 cmH2O (device minimum) and ceiling
    at 25 cmH2O. Projection that drops below 4 → clamp."""
    result = safe_projection(
        metric=metric, slope=-1.0, intercept=10.0, last_x=10.0,
        projection_days=20, n_samples=15,
    )
    # Raw: 10 - 1.0 * 30 = -20 → clamps to 4 (device minimum)
    assert result.projected_value == 4.0
    assert result.clamped is True


def test_leak_metrics_different_upper_bounds():
    """median_leak ceiling is 60, p95_leak is 80, p995_leak is 100 —
    confirm each metric uses its own bound."""
    # median_leak above 60 → clamp to 60
    r_med = safe_projection(
        metric="median_leak", slope=2.0, intercept=10.0, last_x=10.0,
        projection_days=30, n_samples=20,
    )
    assert r_med.projected_value == 60.0

    # p95_leak — same raw value, different ceiling
    r_p95 = safe_projection(
        metric="p95_leak", slope=2.0, intercept=10.0, last_x=10.0,
        projection_days=30, n_samples=20,
    )
    assert r_p95.projected_value == 80.0


# ---------------------------------------------------------------------------
# Unregistered metric — passthrough
# ---------------------------------------------------------------------------


def test_unregistered_metric_returns_raw_value():
    """A metric not in METRIC_BOUNDS isn't clamped — caller gets the
    raw extrapolation. This is the documented escape hatch for
    metrics we haven't curated bounds for yet."""
    result = safe_projection(
        metric="some_future_metric_not_in_registry",
        slope=10.0, intercept=0.0, last_x=10.0,
        projection_days=30, n_samples=20,
    )
    # 0 + 10 * 40 = 400 — wildly out of any clinical range, but with
    # no bounds we return it unguarded.
    assert result.projected_value == 400.0
    assert result.clamped is False
    assert result.bounds_used is None


# ---------------------------------------------------------------------------
# SafeProjection dataclass contract
# ---------------------------------------------------------------------------


def test_safe_projection_has_all_expected_fields():
    result = safe_projection(
        metric="total_ahi", slope=-0.1, intercept=15.0, last_x=20.0,
        projection_days=30, n_samples=20,
    )
    assert isinstance(result, SafeProjection)
    assert hasattr(result, "projected_value")
    assert hasattr(result, "raw_projected_value")
    assert hasattr(result, "clamped")
    assert hasattr(result, "bounds_used")
    assert hasattr(result, "suppressed_reason")
    assert hasattr(result, "explanation")


# ---------------------------------------------------------------------------
# Integration coverage of the /trend endpoint's new projection block
# lives in test_analytics_and_delete.py, where the api_client fixture
# is defined. This file stays pure-unit so it runs without a DB.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bounds registry coverage
# ---------------------------------------------------------------------------


def test_metric_bounds_registry_has_expected_families():
    """Sanity: confirm the registry covers the four metric families
    we care about. Regression test against accidental removal."""
    # AHI family
    for m in ("total_ahi", "obstructive_ahi", "central_ahi",
              "hypopnea_index", "rera_index"):
        assert m in METRIC_BOUNDS
        lo, hi = METRIC_BOUNDS[m]
        assert lo == 0.0 and hi > 0

    # Pressure family
    for m in ("median_pressure", "p95_pressure", "p995_pressure"):
        assert m in METRIC_BOUNDS
        lo, hi = METRIC_BOUNDS[m]
        assert lo >= 4.0 and hi <= 30.0

    # Leak family
    for m in ("median_leak", "p95_leak", "p995_leak"):
        assert m in METRIC_BOUNDS
        lo, _ = METRIC_BOUNDS[m]
        assert lo == 0.0


def test_all_bounds_lo_lt_hi():
    """Defensive: every bound has lo < hi (no inverted ranges)."""
    for m, (lo, hi) in METRIC_BOUNDS.items():
        assert lo < hi, f"{m} has inverted bounds: ({lo}, {hi})"
