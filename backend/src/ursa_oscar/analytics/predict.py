"""Predictive modeling with prediction intervals — Phase 6 Ticket 6.2.

For a target metric Y and predictors X_1..X_p over a training window,
fit a ridge regression for the point estimate and four quantile
regressors for prediction intervals. Then predict at:

  1. **Baseline** — the median of each predictor over the training window
  2. **Counterfactual** — same as baseline, but with operator-supplied
     overrides on specific predictors (e.g., "what if doxepin dose = 6 mg")

The response carries both predictions plus a `delta` so the UI can
render "AHI predicted to decrease by 0.6 — modest improvement" style
callouts without re-doing math client-side.

Method declaration: ``ridge_regression_cv_with_quantile_intervals``.

Sample-size discipline (stricter than 6.1 — Decision 6.2-A):
  - n < 30: refuse — return INSUFFICIENT_DATA envelope
  - 30 ≤ n < 50: confidence_level = "exploratory"
  - 50 ≤ n < 100: confidence_level = "moderate"
  - n ≥ 100: confidence_level = "high"

Rationale per Decision 6.2-A: ridge with 5-fold CV memorizes at n<30,
producing unrealistically narrow prediction intervals. Better to
refuse than mislead. Correlation (Ticket 6.1) tolerates n=15-29 as
exploratory because pairwise math degrades gracefully; prediction
math doesn't.

Why ridge specifically (Decision 6.2-A):
  - Pure OLS fails when predictors are correlated (and CPAP predictors
    are: pressure↔leak, medications↔environment, etc.)
  - L2 regularization handles multicollinearity gracefully
  - Cross-validated alpha selection prevents overfitting on small n
  - Interpretable: selected_alpha tells you something about
    signal-to-noise (small alpha → trust the data; large → trust the prior)
"""
from __future__ import annotations

import logging
from datetime import date as date_t
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import QuantileRegressor, RidgeCV
from sklearn.model_selection import cross_val_score

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric

logger = logging.getLogger(__name__)


# Min/max predictor count for predictive modeling. Looser upper than 6.1
# (multivariate caps at 5) because predictive models can absorb more
# features given the larger sample-size floor (n>=30).
_MIN_PREDICTORS = 2
_MAX_PREDICTORS = 6

# Ridge cross-validation grid. Logarithmic over four orders of magnitude
# covers the practical range for the small-sample regime URSA-OSCAR runs in.
_RIDGE_ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0]

# Five quantile cuts: 2.5% / 25% / 50% / 75% / 97.5%. Endpoint reports
# the 95% interval from the outer pair and the 50% interval from the
# inner pair. The median (0.5) isn't surfaced in the wire shape but
# computed as a sanity check — should track close to ridge.predict.
_QUANTILES = [0.025, 0.25, 0.5, 0.75, 0.975]


def analyze_prediction(
    db: DuckDBManager,
    target_metric: str,
    predictor_metrics: list[str],
    training_start: date_t,
    training_end: date_t,
    counterfactual_inputs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Fit a ridge model + 4 quantile models on (target, predictors)
    over the training window, then predict at baseline and (optionally)
    at counterfactual_inputs. Returns the envelope's ``data`` block
    (caller wraps in ``{ok, data}``)."""
    if (
        len(predictor_metrics) < _MIN_PREDICTORS
        or len(predictor_metrics) > _MAX_PREDICTORS
    ):
        return _envelope_error(
            "BAD_REQUEST",
            (
                f"predictor_metrics must contain {_MIN_PREDICTORS}-"
                f"{_MAX_PREDICTORS} metrics; got {len(predictor_metrics)}"
            ),
            target_metric, predictor_metrics, training_start, training_end,
        )

    # Resolve every metric over the training window. Same pattern as
    # multivariate (6.1) — concat into a single DataFrame keyed by date,
    # then dropna to get the rows usable for both target and all predictors.
    series: dict[str, pd.Series] = {
        "_target": resolve_metric(db, target_metric, training_start, training_end),
    }
    for name in predictor_metrics:
        series[name] = resolve_metric(db, name, training_start, training_end)
    df = pd.concat(series, axis=1).dropna()
    n_training_nights = int(len(df))

    if n_training_nights < 30:
        return _envelope_insufficient_data(
            target_metric, predictor_metrics, training_start, training_end,
            n_training_nights,
        )

    confidence_level = _classify_confidence(n_training_nights)
    sample_caveat = _caveat_for_confidence(confidence_level, n_training_nights)

    Y = df["_target"].to_numpy(dtype=float)
    predictor_names = list(predictor_metrics)
    X = df[predictor_names].to_numpy(dtype=float)

    # Guard against zero-variance target (constant Y → no signal to fit).
    if np.std(Y) == 0:
        return _envelope_error(
            "ZERO_VARIANCE_TARGET",
            (
                f"Target metric '{target_metric}' is constant over the "
                f"training window — nothing to predict. Either widen the "
                f"window or pick a metric with variance."
            ),
            target_metric, predictor_metrics, training_start, training_end,
        )

    # Fit ridge + 4 quantile models. RidgeCV picks alpha by 5-fold CV;
    # alphas grid covers the practical range for small-n regression.
    ridge = RidgeCV(alphas=_RIDGE_ALPHAS, cv=5)
    ridge.fit(X, Y)

    # Cross-validation R² as a model-quality scalar. Negative values are
    # legal and mean "worse than predicting the mean" — surfaced honestly.
    cv_scores = cross_val_score(ridge, X, Y, cv=5, scoring="r2")
    cv_r2 = float(np.mean(cv_scores))

    # Five quantile regressors. solver='highs' is the modern HiGHS LP
    # backend; alpha=0.0 disables sklearn's internal L1 on the quantile
    # coefficients (we want clean quantile fits, not regularized ones).
    quantile_models: dict[float, QuantileRegressor] = {}
    for q in _QUANTILES:
        try:
            qr = QuantileRegressor(quantile=q, solver="highs", alpha=0.0)
            qr.fit(X, Y)
            quantile_models[q] = qr
        except Exception as e:
            # HiGHS occasionally fails on pathological inputs (e.g., all
            # predictors collinear with target). Skip that quantile —
            # the response notes the gap in the interval.
            logger.warning(
                "QuantileRegressor q=%s fit failed (will skip): %s",
                q, e,
            )

    # Build the baseline input vector: per-predictor median over the
    # training window. Predict at baseline first.
    baseline_vec = np.array([float(df[name].median()) for name in predictor_names])
    baseline_prediction = _predict_with_intervals(
        ridge, quantile_models, baseline_vec,
    )

    # Counterfactual: start from baseline, override only the predictors
    # the user named. Predictors not in counterfactual_inputs stay at
    # their baseline value — that's the "all else equal" semantics.
    counterfactual_block: dict[str, Any] | None = None
    if counterfactual_inputs:
        cf_vec = baseline_vec.copy()
        overridden: list[str] = []
        for i, name in enumerate(predictor_names):
            if name in counterfactual_inputs:
                try:
                    cf_vec[i] = float(counterfactual_inputs[name])
                    overridden.append(name)
                except (TypeError, ValueError):
                    logger.warning(
                        "Counterfactual input for %s is not numeric: %r",
                        name, counterfactual_inputs[name],
                    )
        cf_prediction = _predict_with_intervals(
            ridge, quantile_models, cf_vec,
        )
        delta = cf_prediction["point_estimate"] - baseline_prediction["point_estimate"]
        baseline_pt = baseline_prediction["point_estimate"]
        delta_rel_pct = (
            (delta / baseline_pt * 100.0)
            if abs(baseline_pt) > 1e-9 else None
        )
        counterfactual_block = {
            "baseline_prediction": baseline_prediction["point_estimate"],
            "counterfactual_prediction": cf_prediction["point_estimate"],
            "counterfactual_prediction_intervals": {
                "prediction_interval_95": cf_prediction["prediction_interval_95"],
                "prediction_interval_50": cf_prediction["prediction_interval_50"],
            },
            "delta": float(delta),
            "delta_relative_pct": (
                float(delta_rel_pct) if delta_rel_pct is not None else None
            ),
            "overridden_predictors": overridden,
            "interpretation": _interpret_counterfactual(
                delta, baseline_pt, target_metric,
            ),
        }

    # Coefficient table for the model_details section. abs_importance is
    # |coef| normalized to sum to 1 — easy for the UI to render as a bar.
    abs_sum = float(sum(abs(c) for c in ridge.coef_)) or 1.0
    coefficient_data = [
        {
            "predictor": name,
            "coefficient": float(coef),
            "abs_importance": float(abs(coef) / abs_sum),
        }
        for name, coef in zip(predictor_names, ridge.coef_)
    ]

    # The "prediction" block on the wire is the BASELINE prediction by
    # default — the counterfactual block is the delta-from-baseline story.
    # If counterfactual_inputs was provided, the prediction block still
    # reports the baseline so the UI has both numbers in one envelope.
    return {
        "method": "ridge_regression_cv_with_quantile_intervals",
        "target_metric": target_metric,
        "predictor_metrics": predictor_names,
        "training_date_range": {
            "start": training_start.isoformat(),
            "end": training_end.isoformat(),
        },
        "n_training_nights": n_training_nights,
        "confidence_level": confidence_level,
        "sample_caveat": sample_caveat,
        "prediction": baseline_prediction,
        "model_details": {
            "selected_alpha": float(ridge.alpha_),
            "cross_validation_r2": cv_r2,
            "predictor_coefficients": coefficient_data,
            "intercept": float(ridge.intercept_),
            "baseline_inputs": {
                name: float(val) for name, val in zip(predictor_names, baseline_vec)
            },
            "quantiles_fitted": [
                q for q in _QUANTILES if q in quantile_models
            ],
        },
        "counterfactual": counterfactual_block,
    }


# -----------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------


def _predict_with_intervals(
    ridge: RidgeCV,
    quantile_models: dict[float, QuantileRegressor],
    input_vec: np.ndarray,
) -> dict[str, Any]:
    """Run point estimate (ridge) + 95%/50% intervals (quantile pairs)
    at a single input row. Returns the wire-shape `prediction` block."""
    x = input_vec.reshape(1, -1)
    point = float(ridge.predict(x)[0])

    def _q(quantile: float) -> float | None:
        model = quantile_models.get(quantile)
        if model is None:
            return None
        return float(model.predict(x)[0])

    q025, q25, q75, q975 = _q(0.025), _q(0.25), _q(0.75), _q(0.975)

    # If both outer quantiles came back, sort them — degenerate cases
    # can produce crossing quantiles, which is mathematically incorrect
    # but happens with QuantileRegressor on noisy small samples. Sort
    # to give the wire shape a sensible [low, high] regardless.
    interval_95 = _sorted_pair(q025, q975) if (q025 is not None and q975 is not None) else [None, None]
    interval_50 = _sorted_pair(q25, q75) if (q25 is not None and q75 is not None) else [None, None]

    return {
        "point_estimate": point,
        "prediction_interval_95": interval_95,
        "prediction_interval_50": interval_50,
    }


def _sorted_pair(a: float, b: float) -> list[float]:
    return [float(min(a, b)), float(max(a, b))]


def _classify_confidence(n: int) -> str:
    if n < 30:
        return "insufficient"
    if n < 50:
        return "exploratory"
    if n < 100:
        return "moderate"
    return "high"


def _caveat_for_confidence(level: str, n: int) -> str | None:
    if level == "exploratory":
        return (
            f"Only {n} training nights — treat predictions as "
            f"exploratory. Prediction intervals at this sample size may "
            f"be unstable; cross-validation R² may overstate true skill."
        )
    return None


def _interpret_counterfactual(
    delta: float, baseline_point: float, target_metric: str,
) -> str:
    """Machine-readable label for the direction + magnitude of a
    counterfactual delta. The AI assistant and the UI both consume this."""
    if abs(baseline_point) < 1e-9:
        return "indeterminate_zero_baseline"
    rel_pct = abs(delta / baseline_point * 100.0)
    if rel_pct < 5:
        return "negligible_change"
    direction = "decrease" if delta < 0 else "increase"
    magnitude = (
        "modest" if rel_pct < 15
        else "moderate" if rel_pct < 30
        else "large"
    )
    # For "lower is better" metrics (AHI, leak, central_ahi), a decrease
    # is "improvement"; for "higher is better" (alertness), an increase is.
    # We don't bake clinical valence into the label — caller / UI gets
    # to phrase that. The label is direction + magnitude only.
    return f"{magnitude}_{direction}_predicted"


# -----------------------------------------------------------------------
# Error envelopes — match the existing analytics tool conventions.
# -----------------------------------------------------------------------


def _envelope_error(
    code: str,
    message: str,
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
) -> dict[str, Any]:
    return {
        "method": "ridge_regression_cv_with_quantile_intervals",
        "code": code,
        "error": message,
        "target_metric": target_metric,
        "predictor_metrics": list(predictor_metrics),
        "training_date_range": {
            "start": start.isoformat(), "end": end.isoformat(),
        },
        "n_training_nights": 0,
    }


def _envelope_insufficient_data(
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
    n: int,
) -> dict[str, Any]:
    return {
        "method": "ridge_regression_cv_with_quantile_intervals",
        "code": "INSUFFICIENT_DATA",
        "error": (
            f"Need at least 30 training nights to fit a predictive model "
            f"with prediction intervals; got {n}. Predictive modeling "
            f"requires more data than correlation analysis — at n<30 the "
            f"intervals are unreliable. Either widen the training window "
            f"or pick a target metric with more populated nights."
        ),
        "target_metric": target_metric,
        "predictor_metrics": list(predictor_metrics),
        "training_date_range": {
            "start": start.isoformat(), "end": end.isoformat(),
        },
        "n_training_nights": n,
        "confidence_level": "insufficient",
    }
