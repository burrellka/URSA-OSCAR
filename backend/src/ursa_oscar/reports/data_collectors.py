"""Per-section data collectors for the PDF reports — Phase 6 Ticket 6.3.

Each collector function pulls data from the existing analytics layer
(direct compute-function calls, not HTTP round-trips) and returns a
section context dict the Jinja2 template consumes.

Collectors return either:
  - A success dict with the section's data (the template renders the
    full section)
  - A refusal dict ``{"insufficient_data": True, "reason": "...",
    "method": "..."}`` (the template renders the explicit
    "this section requires N nights" fragment)

The refusal pattern propagates 6.1's and 6.2's INSUFFICIENT_DATA
envelopes upward — same language, same threshold semantics. Per
Decision 6.3-E we never omit sections silently; insufficient-data
sections are explicit in the PDF.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_t
from datetime import timedelta
from typing import Any

from ..analytics.correlation import analyze_correlation
from ..analytics.lag import analyze_lag_correlation
from ..analytics.multivariate import analyze_multivariate_correlation
from ..analytics.predict import analyze_prediction
from ..analytics.trend import compute_trend
from ..storage.db import DuckDBManager
from ..storage.repositories import events as events_repo
from ..storage.repositories import nights as nights_repo

logger = logging.getLogger(__name__)


@dataclass
class ReportContext:
    """The fully-assembled context handed to a Jinja2 template render.
    Every field on this dataclass corresponds to a `{{ section.* }}`
    or `{% if section.* %}` branch in the templates."""
    # Header / overview
    template_label: str
    template_key: str
    generated_at_iso: str
    date_range_start: str
    date_range_end: str
    n_nights_in_range: int
    user_profile: dict[str, Any]

    # Section payloads (each is either the success dict or
    # ``{"insufficient_data": True, ...}``).
    overview: dict[str, Any]
    trend_total_ahi: dict[str, Any]
    trend_p95_pressure: dict[str, Any]
    trend_p95_leak: dict[str, Any]
    pairwise_correlations: list[dict[str, Any]]
    multivariate: dict[str, Any]
    lag_analyses: list[dict[str, Any]]
    prediction: dict[str, Any]

    # Methodology — list of MethodologyEntry dicts.
    methodology: list[dict[str, Any]]

    # Tracked across all collectors so the Methodology section is
    # complete by construction (Decision 6.3-D).
    methods_used: list[str]


def collect_overview(
    db: DuckDBManager, start: date_t, end: date_t,
) -> dict[str, Any]:
    """High-level snapshot for the cover-page region."""
    nights = nights_repo.list_in_range(db, start, end)
    if not nights:
        return {
            "insufficient_data": True,
            "reason": f"No nights imported in range {start} → {end}.",
        }
    ahis = [n.total_ahi for n in nights if n.total_ahi is not None]
    durations = [n.total_time_minutes for n in nights if n.total_time_minutes is not None]
    centrals = [n.central_ahi for n in nights if n.central_ahi is not None]
    obstructives = [n.obstructive_ahi for n in nights if n.obstructive_ahi is not None]
    return {
        "n_nights": len(nights),
        "n_nights_with_ahi": len(ahis),
        "mean_total_ahi": (sum(ahis) / len(ahis)) if ahis else None,
        "mean_central_ahi": (sum(centrals) / len(centrals)) if centrals else None,
        "mean_obstructive_ahi": (sum(obstructives) / len(obstructives)) if obstructives else None,
        "mean_hours_per_night": (
            (sum(durations) / len(durations) / 60.0) if durations else None
        ),
        "earliest_date": nights[0].date.isoformat() if nights else None,
        "latest_date": nights[-1].date.isoformat() if nights else None,
    }


def collect_trend(
    db: DuckDBManager, metric: str, start: date_t, end: date_t,
) -> dict[str, Any]:
    """Run ``compute_trend`` for one metric. Returns the analytics dict
    plus a ``method`` key consumable by the methodology collector."""
    try:
        out = compute_trend(db, metric, start, end)
    except Exception as e:
        logger.warning("collect_trend(%s) failed: %s", metric, e)
        return {
            "insufficient_data": True,
            "reason": f"Trend computation failed: {e}",
            "method": "linear_regression_least_squares",
        }
    if out.get("interpretation") == "insufficient_data":
        return {
            "insufficient_data": True,
            "reason": out.get("interpretation_text") or "Insufficient data for trend.",
            "method": "linear_regression_least_squares",
            "n_nights": out.get("n_nights"),
        }
    out["method"] = "linear_regression_least_squares"
    return out


def collect_pairwise_correlations(
    db: DuckDBManager, pairs: list[tuple[str, str]], start: date_t, end: date_t,
) -> list[dict[str, Any]]:
    """Run analyze_correlation for each (metric_a, metric_b) pair."""
    results = []
    for metric_a, metric_b in pairs:
        try:
            out = analyze_correlation(db, metric_a, metric_b, start, end)
        except Exception as e:
            results.append({
                "metric_a": metric_a,
                "metric_b": metric_b,
                "insufficient_data": True,
                "reason": f"Correlation failed: {e}",
                "method": "pairwise_correlation_pearson",
            })
            continue
        out["method"] = "pairwise_correlation_pearson"
        out["metric_a"] = metric_a
        out["metric_b"] = metric_b
        results.append(out)
    return results


def collect_multivariate(
    db: DuckDBManager,
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
) -> dict[str, Any]:
    out = analyze_multivariate_correlation(
        db,
        target_metric=target_metric,
        predictor_metrics=predictor_metrics,
        start=start, end=end,
    )
    if out.get("code") == "INSUFFICIENT_DATA":
        return {
            "insufficient_data": True,
            "reason": out.get("error", "Insufficient data for multivariate analysis."),
            "method": "partial_correlation_pearson",
            "n_observations": out.get("n_observations", 0),
            "target_metric": target_metric,
            "predictor_metrics": predictor_metrics,
        }
    if out.get("code"):
        return {
            "insufficient_data": True,
            "reason": out.get("error", out.get("code")),
            "method": "partial_correlation_pearson",
            "target_metric": target_metric,
            "predictor_metrics": predictor_metrics,
        }
    return out


def collect_lag_analyses(
    db: DuckDBManager,
    pairs: list[tuple[str, str]],
    start: date_t,
    end: date_t,
) -> list[dict[str, Any]]:
    """Run analyze_lag_correlation for each (metric_a, metric_b) pair."""
    results = []
    for metric_a, metric_b in pairs:
        out = analyze_lag_correlation(
            db, metric_a=metric_a, metric_b=metric_b,
            start=start, end=end,
            bootstrap_samples=300,  # keep fast for PDF rendering
        )
        if out.get("code") == "INSUFFICIENT_DATA":
            results.append({
                "insufficient_data": True,
                "reason": out.get("error", "Insufficient data for lag analysis."),
                "method": "cross_correlation_with_bootstrap_ci",
                "metric_a": metric_a,
                "metric_b": metric_b,
            })
            continue
        if out.get("code"):
            results.append({
                "insufficient_data": True,
                "reason": out.get("error", out.get("code")),
                "method": "cross_correlation_with_bootstrap_ci",
                "metric_a": metric_a,
                "metric_b": metric_b,
            })
            continue
        results.append(out)
    return results


def collect_prediction(
    db: DuckDBManager,
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
) -> dict[str, Any]:
    out = analyze_prediction(
        db,
        target_metric=target_metric,
        predictor_metrics=predictor_metrics,
        training_start=start, training_end=end,
    )
    if out.get("code") == "INSUFFICIENT_DATA":
        return {
            "insufficient_data": True,
            "reason": out.get("error", "Insufficient training data."),
            "method": "ridge_regression_cv_with_quantile_intervals",
            "n_training_nights": out.get("n_training_nights", 0),
            "target_metric": target_metric,
            "predictor_metrics": predictor_metrics,
        }
    if out.get("code"):
        return {
            "insufficient_data": True,
            "reason": out.get("error", out.get("code")),
            "method": "ridge_regression_cv_with_quantile_intervals",
            "target_metric": target_metric,
            "predictor_metrics": predictor_metrics,
        }
    return out


# -----------------------------------------------------------------------
# Default predictor sets — sensible defaults the report uses unless
# the operator's request overrides them. Picked to be the cleanest
# combination of "what most operators have data for" with "what's
# clinically interesting to a sleep med doctor".
# -----------------------------------------------------------------------


DEFAULT_PAIRWISE_CORRELATIONS: list[tuple[str, str]] = [
    ("total_ahi", "p95_pressure"),
    ("total_ahi", "p95_leak"),
    ("central_ahi", "p95_pressure"),
    ("obstructive_ahi", "p95_pressure"),
]


DEFAULT_MULTIVARIATE_PREDICTORS: list[str] = ["p95_pressure", "p95_leak"]


DEFAULT_LAG_PAIRS: list[tuple[str, str]] = [
    ("p95_pressure", "total_ahi"),
    ("p95_leak", "total_ahi"),
]


DEFAULT_PREDICTION_PREDICTORS: list[str] = ["p95_pressure", "p95_leak"]


def assemble_context(
    db: DuckDBManager,
    template_key: str,
    template_label: str,
    start: date_t,
    end: date_t,
    user_profile: dict[str, Any] | None,
    generated_at_iso: str,
) -> ReportContext:
    """Run every collector that the template might need and return the
    fully-assembled context. Templates conditionally render sections
    based on which keys are populated."""
    overview = collect_overview(db, start, end)
    n_nights = (
        overview.get("n_nights", 0) if not overview.get("insufficient_data") else 0
    )

    trend_total_ahi = collect_trend(db, "total_ahi", start, end)
    trend_p95_pressure = collect_trend(db, "p95_pressure", start, end)
    trend_p95_leak = collect_trend(db, "p95_leak", start, end)

    pairwise_correlations = collect_pairwise_correlations(
        db, DEFAULT_PAIRWISE_CORRELATIONS, start, end,
    )

    multivariate = collect_multivariate(
        db, target_metric="total_ahi",
        predictor_metrics=DEFAULT_MULTIVARIATE_PREDICTORS,
        start=start, end=end,
    )

    lag_analyses = collect_lag_analyses(db, DEFAULT_LAG_PAIRS, start, end)

    prediction = collect_prediction(
        db, target_metric="total_ahi",
        predictor_metrics=DEFAULT_PREDICTION_PREDICTORS,
        start=start, end=end,
    )

    # Walk every populated section and collect the method strings that
    # appeared in either a successful result or an insufficient-data
    # refusal (both have `method` set). Pass through the methodology
    # registry to resolve descriptions; strict-mode failure if a method
    # appeared in data but isn't registered.
    methods_used: list[str] = []

    def _maybe_add(d: dict[str, Any] | None) -> None:
        if not d:
            return
        m = d.get("method")
        if isinstance(m, str) and m and m not in methods_used:
            methods_used.append(m)

    for t in (trend_total_ahi, trend_p95_pressure, trend_p95_leak):
        _maybe_add(t)
    for c in pairwise_correlations:
        _maybe_add(c)
    _maybe_add(multivariate)
    for la in lag_analyses:
        _maybe_add(la)
    _maybe_add(prediction)

    # Resolve methodology entries (strict mode — Decision 6.3-D).
    from .methodology_registry import collect_methodology_descriptions

    methodology = [dict(m) for m in collect_methodology_descriptions(methods_used)]

    return ReportContext(
        template_label=template_label,
        template_key=template_key,
        generated_at_iso=generated_at_iso,
        date_range_start=start.isoformat(),
        date_range_end=end.isoformat(),
        n_nights_in_range=n_nights,
        user_profile=user_profile or {},
        overview=overview,
        trend_total_ahi=trend_total_ahi,
        trend_p95_pressure=trend_p95_pressure,
        trend_p95_leak=trend_p95_leak,
        pairwise_correlations=pairwise_correlations,
        multivariate=multivariate,
        lag_analyses=lag_analyses,
        prediction=prediction,
        methodology=methodology,
        methods_used=methods_used,
    )
