"""Multivariate (partial) correlation — Phase 6 Ticket 6.1 Item 2.

For a target metric Y and a list of predictor metrics X_1, ..., X_p,
compute the **partial correlation** of each predictor with the target,
controlling for the other predictors:

  1. Regress X_i on (all X_j != X_i) → residuals X_i_resid
  2. Regress Y    on (all X_j != X_i) → residuals Y_resid
  3. Pearson r(X_i_resid, Y_resid) = partial r for X_i

Interpretation: "how much variation in Y is explained by X_i *after*
the other predictors have already accounted for what they can." This
is the method that answers "is doxepin really helping my AHI, or is
the pressure change doing the work?" — pairwise correlation can't
disentangle that.

Confidence intervals come from bootstrap resampling. Default 1000
samples; resample the rows with replacement, recompute partial r,
take the 2.5th and 97.5th percentiles.

Sample-size discipline per work-order Decision 3:
  - n < 15: refuse — return INSUFFICIENT_DATA envelope
  - 15 ≤ n < 30: confidence_level = "exploratory"
  - 30 ≤ n < 100: confidence_level = "moderate"
  - n ≥ 100: confidence_level = "high"

Method declaration per Decision 2: every response carries
``method = "partial_correlation_pearson"`` so the AI assistant and
downstream PDF reports cite exactly what was computed.
"""
from __future__ import annotations

import logging
from datetime import date as date_t
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric

logger = logging.getLogger(__name__)


# Minimum partner correlation that triggers a multicollinearity warning.
# Pairs above this threshold produce unstable partial correlations;
# we still compute but flag the result.
_MULTICOLLINEARITY_R_THRESHOLD = 0.9

# Bootstrap defaults — keep tight enough that the operator doesn't wait
# minutes for a single query. 1000 samples × 4 predictors × 4 regressions
# each runs in well under a second on the operator's hardware.
_BOOTSTRAP_SAMPLES = 1000


def analyze_multivariate_correlation(
    db: DuckDBManager,
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
    bootstrap_samples: int = _BOOTSTRAP_SAMPLES,
    rng_seed: int | None = None,
) -> dict[str, Any]:
    """Compute partial correlations for each predictor with the target,
    controlling for the other predictors. Returns the envelope's
    ``data`` block (caller wraps in ``{ok, data}``).

    ``rng_seed`` is hooked in for deterministic test runs. Production
    calls pass ``None`` for fresh bootstraps each time.
    """
    if len(predictor_metrics) < 2 or len(predictor_metrics) > 5:
        return _envelope_error(
            "BAD_REQUEST",
            f"predictor_metrics must contain 2-5 metrics; got {len(predictor_metrics)}",
            target_metric, predictor_metrics, start, end,
        )

    # Resolve every metric over the date range. Build a single DataFrame
    # keyed by date so per-day alignment is automatic.
    series: dict[str, pd.Series] = {"_target": resolve_metric(db, target_metric, start, end)}
    for name in predictor_metrics:
        series[name] = resolve_metric(db, name, start, end)
    df = pd.concat(series, axis=1).dropna()
    n_observations = int(len(df))

    if n_observations < 15:
        return _envelope_insufficient_data(
            target_metric, predictor_metrics, start, end, n_observations,
        )

    confidence_level = _classify_confidence(n_observations)
    sample_caveat = _caveat_for_confidence(confidence_level, n_observations)

    Y = df["_target"].to_numpy(dtype=float)
    predictor_names = list(predictor_metrics)
    X_full = df[predictor_names].to_numpy(dtype=float)  # shape (n, p)

    # Detect multicollinearity between predictors. Flagged in the
    # response; the partial correlation is still reported but operators
    # should know the math is wobbly when two predictors carry near-
    # identical information.
    multicollinear_pairs = _detect_multicollinearity(X_full, predictor_names)

    rng = np.random.default_rng(rng_seed) if rng_seed is not None else np.random.default_rng()

    predictors_out: list[dict[str, Any]] = []
    for i, name in enumerate(predictor_names):
        x_i = X_full[:, i]
        other_idx = [j for j in range(len(predictor_names)) if j != i]
        if other_idx:
            X_others = X_full[:, other_idx]
            x_resid = _residualize(x_i, X_others)
            y_resid = _residualize(Y, X_others)
        else:
            # Only one predictor — partial correlation reduces to pairwise.
            x_resid = x_i - np.mean(x_i)
            y_resid = Y - np.mean(Y)

        # Constant-residual guards. Both can happen if a predictor is a
        # near-perfect linear combination of the others (multicollinearity)
        # or if a predictor has no variance over the window.
        if np.std(x_resid) == 0 or np.std(y_resid) == 0:
            predictors_out.append({
                "metric": name,
                "partial_r": None,
                "p_value": None,
                "ci_95": [None, None],
                "interpretation": "indeterminate_zero_variance",
                "note": (
                    "Predictor has no residual variance after controlling "
                    "for the others — likely collinear with another predictor."
                ),
            })
            continue

        partial_r, p_value = stats.pearsonr(x_resid, y_resid)

        # Bootstrap CI — resample rows with replacement; recompute partial
        # r each time.
        boot_rs: list[float] = []
        n = len(Y)
        for _ in range(bootstrap_samples):
            idx = rng.integers(0, n, n)
            x_b = X_full[idx, i]
            Y_b = Y[idx]
            if other_idx:
                X_other_b = X_full[idx][:, other_idx]
                try:
                    xb_resid = _residualize(x_b, X_other_b)
                    yb_resid = _residualize(Y_b, X_other_b)
                except Exception:
                    continue
            else:
                xb_resid = x_b - np.mean(x_b)
                yb_resid = Y_b - np.mean(Y_b)
            if np.std(xb_resid) == 0 or np.std(yb_resid) == 0:
                continue
            r_b, _ = stats.pearsonr(xb_resid, yb_resid)
            if np.isfinite(r_b):
                boot_rs.append(float(r_b))

        ci_low, ci_high = (None, None)
        if len(boot_rs) >= bootstrap_samples * 0.5:
            ci_low, ci_high = (
                float(np.percentile(boot_rs, 2.5)),
                float(np.percentile(boot_rs, 97.5)),
            )

        predictors_out.append({
            "metric": name,
            "partial_r": float(partial_r),
            "p_value": float(p_value),
            "ci_95": [ci_low, ci_high],
            "interpretation": _interpret_correlation(float(partial_r), float(p_value), ci_low, ci_high),
        })

    return {
        "method": "partial_correlation_pearson",
        "target_metric": target_metric,
        "predictors": predictors_out,
        "controlled_for": predictor_names,  # each predictor is controlled by the others
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "n_observations": n_observations,
        "confidence_level": confidence_level,
        "sample_caveat": sample_caveat,
        "bootstrap_samples": bootstrap_samples,
        "multicollinear_pairs": multicollinear_pairs,
    }


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _residualize(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Return ``y - X @ beta`` from ordinary-least-squares regression
    of y on X (intercept included via sklearn default)."""
    model = LinearRegression()
    model.fit(X, y)
    return y - model.predict(X)


def _detect_multicollinearity(
    X: np.ndarray, names: list[str],
) -> list[dict[str, Any]]:
    """Find predictor pairs with |r| >= 0.9. Surfaced as a warning on
    the response so the operator can see when partial correlation
    might be unstable."""
    p = X.shape[1]
    out: list[dict[str, Any]] = []
    for i in range(p):
        for j in range(i + 1, p):
            xi, xj = X[:, i], X[:, j]
            if np.std(xi) == 0 or np.std(xj) == 0:
                continue
            r, _ = stats.pearsonr(xi, xj)
            if abs(r) >= _MULTICOLLINEARITY_R_THRESHOLD:
                out.append({
                    "metric_a": names[i],
                    "metric_b": names[j],
                    "r": float(r),
                    "note": (
                        "These predictors are near-collinear; partial "
                        "correlations involving them may be unstable. "
                        "Consider removing one."
                    ),
                })
    return out


def _classify_confidence(n: int) -> str:
    if n < 30:
        return "exploratory"
    if n < 100:
        return "moderate"
    return "high"


def _caveat_for_confidence(level: str, n: int) -> str | None:
    if level == "exploratory":
        return (
            f"Only {n} observations — treat results as exploratory. "
            f"Patterns at this sample size are hypotheses, not findings."
        )
    return None


def _interpret_correlation(
    r: float, p_value: float, ci_low: float | None, ci_high: float | None,
) -> str:
    abs_r = abs(r)
    direction = "positive" if r >= 0 else "negative"
    strength = (
        "negligible" if abs_r < 0.1
        else "weak" if abs_r < 0.3
        else "moderate" if abs_r < 0.5
        else "strong"
    )
    significant = (p_value < 0.05) if p_value is not None else False
    ci_spans_zero = (
        ci_low is not None and ci_high is not None
        and ci_low <= 0 <= ci_high
    )
    parts = [strength, direction]
    if not significant or ci_spans_zero:
        parts.append("not_significant")
    return "_".join(parts)


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
        "method": "partial_correlation_pearson",
        "code": code,
        "error": message,
        "target_metric": target_metric,
        "predictors": [],
        "controlled_for": list(predictor_metrics),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "n_observations": 0,
    }


def _envelope_insufficient_data(
    target_metric: str,
    predictor_metrics: list[str],
    start: date_t,
    end: date_t,
    n: int,
) -> dict[str, Any]:
    return {
        "method": "partial_correlation_pearson",
        "code": "INSUFFICIENT_DATA",
        "error": (
            f"Need at least 15 observations to compute partial correlations "
            f"with confidence intervals; got {n}. Either the date range is "
            f"too narrow or one of the metrics has too few logged days."
        ),
        "target_metric": target_metric,
        "predictors": [],
        "controlled_for": list(predictor_metrics),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "n_observations": n,
        "confidence_level": "insufficient",
    }
