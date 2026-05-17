"""analyze_prediction — Phase 6 Ticket 6.2 MCP tool.

Thin proxy over POST /api/v1/analytics/predict. Fits a ridge
regression with cross-validation + four quantile regressors for
prediction intervals, then predicts at baseline (median of each
training-window predictor) and, when counterfactual_inputs are
provided, at the override vector with a delta block.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_post
from ..envelope import _err, _ok  # noqa: F401  (re-export for tests)
from ..server import mcp


@mcp.tool()
def analyze_prediction(
    target_metric: str,
    predictor_metrics: list[str],
    training_start_date: str,
    training_end_date: str,
    counterfactual_inputs: dict[str, float] | None = None,
    recompute: bool = False,
) -> dict:
    """Predict a metric tonight + answer 'what if' counterfactual
    questions.

    Use this tool when the user asks "what will happen if..." or
    "predict my X" or wants to evaluate a hypothetical scenario:

        "What's my AHI likely to be tonight?"
        "If I take doxepin tonight, what does my AHI prediction change to?"
        "What if I bump my pressure max from 12 to 14?"
        "Predict my morning alertness if I sleep with the room darker."

    Method: ridge regression with k-fold cross-validation
    (sklearn.linear_model.RidgeCV, cv=5) for the point estimate +
    four quantile regressors (0.025, 0.25, 0.75, 0.975) for the
    prediction intervals. The response declares
    ``method = "ridge_regression_cv_with_quantile_intervals"`` so the
    answer is reproducible and defensible.

    Wire surface returned on success:
        {ok: true, data: {
            method, target_metric, predictor_metrics,
            training_date_range, n_training_nights, confidence_level,
            sample_caveat,
            prediction: {
                point_estimate,
                prediction_interval_95: [low, high],   # 95% chance the actual falls in here
                prediction_interval_50: [low, high]    # 50% chance
            },
            model_details: {
                selected_alpha, cross_validation_r2,
                predictor_coefficients: [...],
                intercept, baseline_inputs: {...},
                quantiles_fitted: [...]
            },
            counterfactual: null | {
                baseline_prediction, counterfactual_prediction,
                counterfactual_prediction_intervals: {...},
                delta, delta_relative_pct,
                overridden_predictors, interpretation
            },
            cache_age_seconds, computed_at
        }}

    Sample-size discipline is STRICTER than correlation analysis:
        n < 30:  refused -> {ok: false, code: "INSUFFICIENT_DATA"}
        30 <= n < 50:  confidence_level = "exploratory"
        50 <= n < 100: confidence_level = "moderate"
        n >= 100:      confidence_level = "high"

    Why stricter: at n<30, ridge regression "memorizes" the training
    data and produces prediction intervals that look narrow but
    aren't trustworthy. Better to refuse than mislead.

    When relaying to the user:
      - NEVER quote the point_estimate alone. Always include the
        prediction interval ("4.2, with a 50% chance it falls between
        3.4 and 5.1, and 95% between 1.8 and 7.1")
      - Surface the confidence_level naturally ("based on 47 training
        nights, moderate confidence")
      - If the 95% interval is wider than 4x the point estimate, say
        so explicitly: "the model isn't confident here"
      - If cross_validation_r2 < 0.4, mention it: "the model fits
        the data poorly (R² = 0.31), so treat this as exploratory"
      - For counterfactuals, report direction and magnitude of the
        delta but DON'T tell the user to actually take the action —
        that's a clinical decision the user makes with their provider
      - If counterfactual intervals overlap baseline intervals
        substantially, say "the effect may not be large enough to
        clearly detect"

    Predictor count rules:
      - 2 minimum (need something to control for to model anything)
      - 6 maximum (more predictors require more training data than
        the n>=30 floor practically gives)

    Args:
        target_metric: what to predict (e.g., "total_ahi", "alertness:morning:score")
        predictor_metrics: 2-6 factors. Same naming as analyze_correlation.
        training_start_date: YYYY-MM-DD inclusive. Lower bound of training data.
        training_end_date: YYYY-MM-DD inclusive. Upper bound.
        counterfactual_inputs: optional dict mapping predictor name to
            hypothetical value. Predictors not in this dict default to
            their training-window median. Omit entirely for the plain
            baseline prediction.
        recompute: bypass cache and refit. Default false.

    Returns:
        See "Wire surface" above. On refusal (n<30 training data):
        {ok: false, data: {..., code: "INSUFFICIENT_DATA", error: "..."}}
    """
    for label, value in [
        ("training_start_date", training_start_date),
        ("training_end_date", training_end_date),
    ]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")
    if not isinstance(predictor_metrics, list):
        return _err(
            "predictor_metrics must be a list of strings", code="INVALID_INPUT",
        )
    if not (2 <= len(predictor_metrics) <= 6):
        return _err(
            f"predictor_metrics must contain 2-6 metrics; got {len(predictor_metrics)}",
            code="INVALID_INPUT",
        )
    if counterfactual_inputs is not None and not isinstance(counterfactual_inputs, dict):
        return _err(
            "counterfactual_inputs must be a dict (or omitted)",
            code="INVALID_INPUT",
        )

    body: dict = {
        "target_metric": target_metric,
        "predictor_metrics": predictor_metrics,
        "training_start_date": training_start_date,
        "training_end_date": training_end_date,
        "recompute": bool(recompute),
    }
    if counterfactual_inputs is not None:
        body["counterfactual_inputs"] = counterfactual_inputs

    try:
        return api_post("/api/v1/analytics/predict", json_body=body)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
