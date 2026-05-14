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

from ..analytics.correlation import analyze_correlation as compute_correlation
from ..analytics.manual_log_summary import summarize_manual_logs
from ..analytics.metric_resolver import (
    UnknownMetricError,
    known_nightly_metrics,
    list_available_manual_metrics,
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
