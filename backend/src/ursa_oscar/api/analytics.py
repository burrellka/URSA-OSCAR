"""Analytics endpoints — Phase 3 Items 5A-5D.

Four GET endpoints under ``/api/v1/analytics/*``. Compute lives here in
the API container (ADR-003: MCP is a proxy, not a compute layer). The
MCP Tier-2 tools (compare_periods, analyze_correlation, get_trend,
get_manual_log_summary) call these endpoints and envelope the result.

Endpoints:
    GET /api/v1/analytics/compare-periods
    GET /api/v1/analytics/correlation
    GET /api/v1/analytics/trend
    GET /api/v1/analytics/manual-log-summary
    GET /api/v1/analytics/available-metrics    (helper for UI dropdowns)
"""
from __future__ import annotations

from datetime import date as date_t
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from pydantic import BaseModel, Field

from ..analytics.cache import AnalyticalCache, cached_compute
from ..analytics.correlation import analyze_correlation as compute_correlation
from ..analytics.manual_log_summary import summarize_manual_logs
from ..analytics.metric_resolver import (
    UnknownMetricError,
    known_nightly_metrics,
    list_available_manual_metrics,
)
from ..analytics.lag import (
    analyze_lag_correlation as compute_lag_correlation,
)
from ..analytics.multivariate import (
    analyze_multivariate_correlation as compute_multivariate_correlation,
)
from ..analytics.predict import (
    analyze_prediction as compute_prediction,
)
from ..analytics.period_compare import compare_periods as compute_compare_periods
from ..analytics.trend import compute_trend

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/compare-periods")
def compare_periods_endpoint(
    request: Request,
    period_a_start: date_t = Query(..., description="Inclusive start of period A"),
    period_a_end:   date_t = Query(..., description="Inclusive end of period A"),
    period_b_start: date_t = Query(..., description="Inclusive start of period B"),
    period_b_end:   date_t = Query(..., description="Inclusive end of period B"),
    metrics: Annotated[list[str] | None, Query(
        description="Optional list of metrics to compare. Defaults to the "
                    "standard AHI/pressure/leak/apnea/mask-on set.",
    )] = None,
) -> dict[str, Any]:
    """Compute mean/median + percent-change for each metric between two
    date ranges. Returns the structured comparison dict (see
    analytics/period_compare.py for shape)."""
    if period_a_end < period_a_start:
        raise HTTPException(status_code=400, detail="period_a_end must be >= period_a_start")
    if period_b_end < period_b_start:
        raise HTTPException(status_code=400, detail="period_b_end must be >= period_b_start")

    db = request.app.state.db
    try:
        return compute_compare_periods(
            db, period_a_start, period_a_end, period_b_start, period_b_end, metrics,
        )
    except UnknownMetricError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/correlation")
def correlation_endpoint(
    request: Request,
    metric_a: str = Query(..., description="First metric. Bare nightly_summary column or 'log_type:filter:field'."),
    metric_b: str = Query(..., description="Second metric. Same naming as metric_a."),
    start_date: date_t = Query(..., description="Inclusive start"),
    end_date: date_t = Query(..., description="Inclusive end"),
    lag_days: int = Query(0, ge=-30, le=30, description="Days to lag metric_b vs metric_a"),
) -> dict[str, Any]:
    """Pearson r + p-value between two metrics over a date range with
    optional lag-shift on metric_b. See analytics/correlation.py."""
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = request.app.state.db
    try:
        return compute_correlation(
            db, metric_a, metric_b, start_date, end_date, lag_days,
        )
    except UnknownMetricError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trend")
def trend_endpoint(
    request: Request,
    metric: str = Query(..., description="Metric to fit. Same naming as correlation."),
    start_date: date_t = Query(..., description="Inclusive start"),
    end_date: date_t = Query(..., description="Inclusive end"),
    projection_days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Linear-regression trend on the metric's daily values. Returns
    slope-per-day, R², a 30-day-forward projection, and an
    interpretation label."""
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = request.app.state.db
    try:
        return compute_trend(db, metric, start_date, end_date, projection_days)
    except UnknownMetricError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/manual-log-summary")
def manual_log_summary_endpoint(
    request: Request,
    date: date_t | None = Query(default=None, description="Single date — when set, ignores start_date / end_date."),
    start_date: date_t | None = Query(default=None, description="Inclusive start (used together with end_date)."),
    end_date: date_t | None = Query(default=None, description="Inclusive end."),
    log_type: str | None = Query(default=None, description="Filter to one log_type."),
) -> dict[str, Any]:
    """Aggregate manual_logs by type. Per-type rollups documented in
    analytics/manual_log_summary.py."""
    if date is not None:
        start = end = date
    else:
        if start_date is None or end_date is None:
            raise HTTPException(
                status_code=400,
                detail="Provide either `date` or both `start_date` + `end_date`",
            )
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="end_date must be >= start_date")
        start, end = start_date, end_date

    db = request.app.state.db
    return summarize_manual_logs(db, start, end, log_type)


@router.get("/available-metrics")
def available_metrics_endpoint(request: Request) -> dict[str, Any]:
    """Lists metrics with data behind them for the Trends UI dropdowns.

    Always returns the full nightly-metric set; manual-metric items are
    probed from manual_logs so only types/names with actual entries appear.
    """
    db = request.app.state.db
    return {
        "nightly_metrics": known_nightly_metrics(),
        "manual_metrics": list_available_manual_metrics(db),
    }


# -----------------------------------------------------------------------
# Phase 6 Ticket 6.1 — multivariate (partial) correlation.
# -----------------------------------------------------------------------


class MultivariateCorrelationRequest(BaseModel):
    """POST body for /analytics/multivariate-correlation."""
    target_metric: str = Field(
        description=(
            "Outcome to explain. Bare nightly_summary column "
            "(e.g., 'total_ahi') OR 'log_type:filter:field' "
            "(e.g., 'alertness::score')."
        ),
    )
    predictor_metrics: list[str] = Field(
        description="2-5 candidate predictors. Same naming as target_metric.",
        min_length=2, max_length=5,
    )
    start_date: date_t
    end_date: date_t
    recompute: bool = Field(
        default=False,
        description="Bypass the cache and force a fresh computation.",
    )


@router.post("/multivariate-correlation")
def multivariate_correlation_endpoint(
    body: MultivariateCorrelationRequest, request: Request,
) -> dict[str, Any]:
    """Partial correlation of each predictor with the target, controlling
    for the others. Returns one row per predictor with partial r,
    bootstrap 95% CI, p-value, interpretation, plus envelope metadata
    (method, n_observations, confidence_level, cache_age_seconds).

    Caches by SHA-256 fingerprint of
    (tool_name + sorted_params_json + data_version_hash). Re-running
    the same query against unchanged data returns the cached envelope
    in O(ms). New imports or manual-log mutations in the date range
    invalidate the entry; the next call recomputes.
    """
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = request.app.state.db
    cache = AnalyticalCache(db)
    params = {
        "target_metric": body.target_metric,
        "predictor_metrics": list(body.predictor_metrics),
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
    }

    with cached_compute(
        cache,
        tool_name="analyze_multivariate_correlation",
        params=params,
        start_date=body.start_date,
        end_date=body.end_date,
        recompute=body.recompute,
    ) as ctx:
        if ctx.hit:
            return ctx.cached_result

        try:
            data = compute_multivariate_correlation(
                db,
                target_metric=body.target_metric,
                predictor_metrics=list(body.predictor_metrics),
                start=body.start_date,
                end=body.end_date,
            )
        except UnknownMetricError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Don't cache refused / errored results — recompute on next call
        # so the operator's "I added more data, let me try again" works.
        envelope = {"ok": "code" not in data, "data": data}
        if "code" not in data:
            ctx.store(envelope)
        return envelope


# -----------------------------------------------------------------------
# Phase 6 Ticket 6.2 — predictive modeling with prediction intervals
# and counterfactual scenarios.
# -----------------------------------------------------------------------


class PredictRequest(BaseModel):
    """POST body for /analytics/predict."""
    target_metric: str = Field(
        description=(
            "Outcome to predict. Same naming as analyze_correlation — "
            "bare nightly_summary column or 'log_type:filter:field'."
        ),
    )
    predictor_metrics: list[str] = Field(
        min_length=2, max_length=6,
        description=(
            "2-6 factors the model trains on. More predictors require "
            "more training data; the n<30 floor applies regardless."
        ),
    )
    training_start_date: date_t
    training_end_date: date_t
    counterfactual_inputs: dict[str, float] | None = Field(
        default=None,
        description=(
            "Optional. Dict mapping predictor name to its hypothetical "
            "value. Predictors not in this dict default to their "
            "training-window median (the baseline). If absent entirely, "
            "only the baseline prediction is computed."
        ),
    )
    recompute: bool = Field(
        default=False,
        description="Bypass cache and force a fresh fit.",
    )


@router.post("/predict")
def predict_endpoint(
    body: PredictRequest, request: Request,
) -> dict[str, Any]:
    """Fit a ridge regression with cross-validated alpha on the
    (target, predictors) training data, plus four quantile regressors
    for prediction intervals. Predict at the baseline (median of each
    predictor over the training window) and, if ``counterfactual_inputs``
    is provided, at that override vector too — returning the delta.

    Method: ``ridge_regression_cv_with_quantile_intervals``. Refuses
    with INSUFFICIENT_DATA if the training set has fewer than 30 nights
    after dropna over (target + all predictors).

    Cached via the same fingerprint-based machinery as the other
    analytical endpoints (Ticket 6.1). Importer + manual-log CRUD
    invalidate entries whose training_date_range overlaps the change.
    """
    if body.training_end_date < body.training_start_date:
        raise HTTPException(
            status_code=400,
            detail="training_end_date must be >= training_start_date",
        )

    db = request.app.state.db
    cache = AnalyticalCache(db)
    params = {
        "target_metric": body.target_metric,
        "predictor_metrics": list(body.predictor_metrics),
        "start_date": body.training_start_date.isoformat(),
        "end_date": body.training_end_date.isoformat(),
        "counterfactual_inputs": (
            dict(sorted(body.counterfactual_inputs.items()))
            if body.counterfactual_inputs else None
        ),
    }

    with cached_compute(
        cache,
        tool_name="analyze_prediction",
        params=params,
        start_date=body.training_start_date,
        end_date=body.training_end_date,
        recompute=body.recompute,
    ) as ctx:
        if ctx.hit:
            return ctx.cached_result

        try:
            data = compute_prediction(
                db,
                target_metric=body.target_metric,
                predictor_metrics=list(body.predictor_metrics),
                training_start=body.training_start_date,
                training_end=body.training_end_date,
                counterfactual_inputs=body.counterfactual_inputs,
            )
        except UnknownMetricError as e:
            raise HTTPException(status_code=400, detail=str(e))

        envelope = {"ok": "code" not in data, "data": data}
        if "code" not in data:
            ctx.store(envelope)
        return envelope


# -----------------------------------------------------------------------
# Phase 6 Ticket 6.1 Item 5 — analytical_cache stats + clear endpoints.
# -----------------------------------------------------------------------


@router.get("/cache/stats")
def cache_stats_endpoint(request: Request) -> dict[str, Any]:
    """Aggregate counts for the analytical_cache. Used by the Data
    Management page's "Analytical cache" section + by operators
    debugging stale-result complaints."""
    db = request.app.state.db
    return AnalyticalCache(db).stats()


class CacheClearRequest(BaseModel):
    confirm: bool = Field(
        ..., description="Must be true to actually clear. Belt-and-suspenders.",
    )


@router.post("/cache/clear")
def cache_clear_endpoint(
    body: CacheClearRequest, request: Request,
) -> dict[str, Any]:
    """Wipe every analytical_cache entry. Cache auto-invalidates on
    data changes; manual clearing is rarely necessary. Returns the
    count cleared. Refuses if confirm is not explicitly true."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true to clear the cache",
        )
    db = request.app.state.db
    n = AnalyticalCache(db).clear_all()
    return {"entries_cleared": n}


# -----------------------------------------------------------------------
# Phase 6 Ticket 6.1 — time-shifted lag correlation with bootstrap CI.
# -----------------------------------------------------------------------


class LagCorrelationRequest(BaseModel):
    """POST body for /analytics/lag-correlation."""
    metric_a: str = Field(description="The hypothesized cause metric.")
    metric_b: str = Field(description="The hypothesized effect metric.")
    start_date: date_t
    end_date: date_t
    lag_range_days: list[int] = Field(
        default=[-3, 7],
        min_length=2, max_length=2,
        description=(
            "[lo, hi] inclusive lag window in days. Negative lags act as "
            "a sanity check (effect before cause should be implausible)."
        ),
    )
    bootstrap_samples: int = Field(default=1000, ge=100, le=5000)
    recompute: bool = False


@router.post("/lag-correlation")
def lag_correlation_endpoint(
    body: LagCorrelationRequest, request: Request,
) -> dict[str, Any]:
    """Cross-correlation function across a lag window with bootstrap
    95% CIs at each lag. Useful for "how long after I take doxepin does
    my AHI improve?" or "when does a pressure change take effect?"

    Caches by SHA-256 fingerprint of (tool_name + params + data_version_hash)
    just like the multivariate endpoint."""
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    lo, hi = body.lag_range_days
    if hi < lo:
        raise HTTPException(
            status_code=400,
            detail=f"lag_range_days upper bound ({hi}) must be >= lower bound ({lo})",
        )

    db = request.app.state.db
    cache = AnalyticalCache(db)
    params = {
        "metric_a": body.metric_a,
        "metric_b": body.metric_b,
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
        "lag_range_days": [lo, hi],
        "bootstrap_samples": body.bootstrap_samples,
    }

    with cached_compute(
        cache,
        tool_name="analyze_lag_correlation",
        params=params,
        start_date=body.start_date,
        end_date=body.end_date,
        recompute=body.recompute,
    ) as ctx:
        if ctx.hit:
            return ctx.cached_result

        try:
            data = compute_lag_correlation(
                db,
                metric_a=body.metric_a,
                metric_b=body.metric_b,
                start=body.start_date,
                end=body.end_date,
                lag_range=(lo, hi),
                bootstrap_samples=body.bootstrap_samples,
            )
        except UnknownMetricError as e:
            raise HTTPException(status_code=400, detail=str(e))

        envelope = {"ok": "code" not in data, "data": data}
        if "code" not in data:
            ctx.store(envelope)
        return envelope
