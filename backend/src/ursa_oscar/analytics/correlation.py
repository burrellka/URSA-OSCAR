"""Correlation analysis — Phase 3 Item 5B.

Pearson r + p-value between any two metrics with optional lag-days
shift on metric_b. Lag semantics: when lag_days=2, we align each day of
metric_a with metric_b's value 2 days LATER. Useful for "if I take meds
today, does my AHI improve 2 days later?"-shaped questions.

Returns the wire-shape dict for ``/api/v1/analytics/correlation``.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric


def analyze_correlation(
    db: DuckDBManager,
    metric_a: str,
    metric_b: str,
    start: date_t,
    end: date_t,
    lag_days: int = 0,
) -> dict[str, Any]:
    """Compute Pearson r between two metrics over a date range with an
    optional lag offset applied to metric_b. Returns the structured dict.
    """
    # Pull metric_a over the natural range. For metric_b, extend the
    # right-hand bound by lag_days so we capture the lagged values that
    # align to the last days of metric_a.
    a = resolve_metric(db, metric_a, start, end)
    b_end = end + timedelta(days=lag_days) if lag_days > 0 else end
    b_start = start + timedelta(days=lag_days) if lag_days > 0 else start
    b = resolve_metric(db, metric_b, b_start, b_end)

    # Align by shifting metric_b's index back by lag_days, then join.
    if lag_days != 0:
        b = b.copy()
        b.index = [d - timedelta(days=lag_days) for d in b.index]

    df = pd.concat({"a": a, "b": b}, axis=1).dropna()
    n_pairs = int(len(df))

    if n_pairs < 3:
        return {
            "metric_a": metric_a,
            "metric_b": metric_b,
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "lag_days": lag_days,
            "n_pairs": n_pairs,
            "pearson_r": None,
            "p_value": None,
            "interpretation": "insufficient_data",
            "interpretation_text": (
                f"Need at least 3 paired daily values to compute a correlation; "
                f"got {n_pairs}. Either the range is too narrow or one of the "
                f"metrics has too few logged days."
            ),
            "sample_size_warning": "n < 3 — no correlation computed",
        }

    # scipy returns r + p-value. NaN result possible if one series is
    # constant (zero variance); guard accordingly.
    a_arr = df["a"].to_numpy(dtype=float)
    b_arr = df["b"].to_numpy(dtype=float)
    if np.std(a_arr) == 0 or np.std(b_arr) == 0:
        return {
            "metric_a": metric_a,
            "metric_b": metric_b,
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "lag_days": lag_days,
            "n_pairs": n_pairs,
            "pearson_r": None,
            "p_value": None,
            "interpretation": "no_variance",
            "interpretation_text": (
                "One of the metrics has zero variance across the date range "
                "(every day is the same value). Correlation is undefined."
            ),
            "sample_size_warning": None,
        }

    res = stats.pearsonr(a_arr, b_arr)
    r = float(res.statistic)
    p = float(res.pvalue)
    interpretation, interp_text = _interpret_pearson(r, p, n_pairs, metric_a, metric_b)
    warning = "n < 30 — interpret with caution" if n_pairs < 30 else None

    return {
        "metric_a": metric_a,
        "metric_b": metric_b,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "lag_days": lag_days,
        "n_pairs": n_pairs,
        "pearson_r": r,
        "p_value": p,
        "interpretation": interpretation,
        "interpretation_text": interp_text,
        "sample_size_warning": warning,
    }


def _interpret_pearson(
    r: float, p: float, n: int, metric_a: str, metric_b: str,
) -> tuple[str, str]:
    """Return (machine_label, human_sentence)."""
    sign = "positive" if r >= 0 else "negative"
    strength = (
        "negligible" if abs(r) < 0.1
        else "weak" if abs(r) < 0.3
        else "moderate" if abs(r) < 0.5
        else "strong" if abs(r) < 0.7
        else "very_strong"
    )
    label = f"{strength}_{sign}" if strength != "negligible" else "negligible"

    sig = "significant" if p < 0.05 else "not statistically significant"
    text = (
        f"{strength.capitalize()} {sign} correlation between {metric_a} and "
        f"{metric_b} (r={r:.2f}, p={p:.3f}, n={n}). {sig.capitalize()} at p<0.05."
    )
    if n < 30:
        text += (
            " Sample size is small (n<30); treat as exploratory and re-check "
            "as more data accumulates."
        )
    return label, text
