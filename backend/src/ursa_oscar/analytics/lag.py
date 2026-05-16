"""Time-shifted lag correlation — Phase 6 Ticket 6.1 Item 3.

For two metrics A and B over a date range, compute the Pearson r at
each integer lag in a window (default ``[-3, +7]`` days). At each
lag, also compute a bootstrap 95% confidence interval by resampling
the aligned pairs with replacement.

The interpretive value:
  - A real causal effect of A on B at lag k should show a strong
    correlation at lag k whose CI does NOT span zero.
  - Negative lag values (effect before cause) serve as a sanity check
    — if you see strong correlation at lag -2 that should be
    impossible-by-construction, something is wrong with the analysis.
  - The bootstrap CI makes the "noise vs real effect" distinction
    visible: a spurious correlation at lag -2 should have a wide
    CI spanning zero, while a real effect at lag +1 should have a
    narrow CI excluding zero.

Sample-size discipline (Decision 3):
  - n_aligned < 15 at a lag → that lag is dropped from results
  - Overall response confidence_level computed from MAX aligned n
    across lags (so a sparse high-lag doesn't downgrade the report)

Method declaration:
  ``method = "cross_correlation_with_bootstrap_ci"``
"""
from __future__ import annotations

import logging
from datetime import date as date_t
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ..storage.db import DuckDBManager
from .metric_resolver import resolve_metric

logger = logging.getLogger(__name__)


_DEFAULT_LAG_RANGE = (-3, 7)
_DEFAULT_BOOTSTRAP_SAMPLES = 1000
_MIN_PAIRS_PER_LAG = 15


def analyze_lag_correlation(
    db: DuckDBManager,
    metric_a: str,
    metric_b: str,
    start: date_t,
    end: date_t,
    lag_range: tuple[int, int] = _DEFAULT_LAG_RANGE,
    bootstrap_samples: int = _DEFAULT_BOOTSTRAP_SAMPLES,
    rng_seed: int | None = None,
) -> dict[str, Any]:
    """Compute Pearson r at each lag in ``lag_range`` (inclusive of both
    endpoints), with bootstrap 95% CI per lag. Returns the structured
    envelope's ``data`` block.

    ``rng_seed`` enables deterministic test runs.
    """
    lag_lo, lag_hi = lag_range
    if lag_hi < lag_lo:
        return _envelope_error(
            "BAD_REQUEST",
            f"lag_range upper bound ({lag_hi}) must be >= lower bound ({lag_lo})",
            metric_a, metric_b, start, end, lag_range,
        )
    if abs(lag_hi - lag_lo) > 60:
        return _envelope_error(
            "BAD_REQUEST",
            "lag_range spans more than 60 days; tighten the range",
            metric_a, metric_b, start, end, lag_range,
        )

    # Pull both series. Resolve over the FULL combined window so we
    # have data on both ends for the lagged alignments.
    a_series = resolve_metric(db, metric_a, start, end)
    b_series = resolve_metric(db, metric_b, start, end)

    # Inner-join by date — only days with both metrics present.
    df = pd.concat({"a": a_series, "b": b_series}, axis=1).dropna().sort_index()
    n_unlagged = int(len(df))

    if n_unlagged < _MIN_PAIRS_PER_LAG:
        return _envelope_insufficient_data(
            metric_a, metric_b, start, end, lag_range, n_unlagged,
        )

    rng = np.random.default_rng(rng_seed) if rng_seed is not None else np.random.default_rng()

    a_arr = df["a"].to_numpy(dtype=float)
    b_arr = df["b"].to_numpy(dtype=float)

    lag_results: list[dict[str, Any]] = []
    max_n_aligned = 0
    for lag in range(lag_lo, lag_hi + 1):
        a_aligned, b_aligned = _align(a_arr, b_arr, lag)
        if len(a_aligned) < _MIN_PAIRS_PER_LAG:
            # Skip this lag entirely — the user gets a result for lags
            # where there's enough data, but a window edge where there
            # isn't doesn't poison the response.
            continue
        if np.std(a_aligned) == 0 or np.std(b_aligned) == 0:
            lag_results.append({
                "lag_days": lag,
                "r": None,
                "p_value": None,
                "ci_95": [None, None],
                "n_aligned": int(len(a_aligned)),
                "note": "no_variance",
            })
            continue

        r, p = stats.pearsonr(a_aligned, b_aligned)
        ci_low, ci_high = _bootstrap_ci(
            a_aligned, b_aligned, n_samples=bootstrap_samples, rng=rng,
        )

        lag_results.append({
            "lag_days": lag,
            "r": float(r),
            "p_value": float(p),
            "ci_95": [ci_low, ci_high],
            "n_aligned": int(len(a_aligned)),
        })
        max_n_aligned = max(max_n_aligned, int(len(a_aligned)))

    if not lag_results:
        return _envelope_insufficient_data(
            metric_a, metric_b, start, end, lag_range, n_unlagged,
        )

    # Find the peak lag (max |r|) for a high-level summary.
    valid = [r for r in lag_results if r["r"] is not None]
    peak_lag = None
    peak_r = None
    peak_p = None
    clinical_note = None
    interpretation = "no_significant_lag_correlation"
    if valid:
        peak_entry = max(valid, key=lambda x: abs(x["r"]))
        peak_lag = peak_entry["lag_days"]
        peak_r = peak_entry["r"]
        peak_p = peak_entry["p_value"]
        ci_lo, ci_hi = peak_entry["ci_95"]
        peak_significant = (
            ci_lo is not None and ci_hi is not None
            and not (ci_lo <= 0 <= ci_hi)
        )
        if peak_significant:
            strength = (
                "weak" if abs(peak_r) < 0.3
                else "moderate" if abs(peak_r) < 0.5
                else "strong"
            )
            direction = "negative" if peak_r < 0 else "positive"
            interpretation = f"{strength}_{direction}_correlation_at_lag_{peak_lag}"
            clinical_note = _build_clinical_note(peak_lag, peak_r, metric_a, metric_b)
        else:
            interpretation = (
                "no_significant_lag_correlation"
                if peak_r is not None and abs(peak_r) < 0.3
                else f"peak_at_lag_{peak_lag}_but_ci_spans_zero"
            )
            clinical_note = (
                "Strongest signal is at lag {} (r={:.3f}), but its 95% "
                "confidence interval spans zero — not distinguishable "
                "from noise at this sample size."
            ).format(peak_lag, peak_r)

    confidence_level = _classify_confidence(max_n_aligned)
    sample_caveat = _caveat_for_confidence(confidence_level, max_n_aligned)

    return {
        "method": "cross_correlation_with_bootstrap_ci",
        "metric_a": metric_a,
        "metric_b": metric_b,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "lag_range": [lag_lo, lag_hi],
        "lag_correlations": lag_results,
        "peak_lag_days": peak_lag,
        "peak_correlation": peak_r,
        "peak_p_value": peak_p,
        "interpretation": interpretation,
        "clinical_note": clinical_note,
        "n_observations": max_n_aligned,
        "confidence_level": confidence_level,
        "sample_caveat": sample_caveat,
        "bootstrap_samples": bootstrap_samples,
    }


# -----------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------


def _align(
    a: np.ndarray, b: np.ndarray, lag: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Align ``a`` with ``b`` shifted by ``lag``.

    lag > 0: pair a[t] with b[t + lag] — "a today predicts b N days later"
    lag = 0: pair a[t] with b[t]
    lag < 0: pair a[t] with b[t + lag] — "b N days ago correlates with a today"
    """
    n = len(a)
    if lag >= 0:
        a_aligned = a[: n - lag]
        b_aligned = b[lag:]
    else:
        a_aligned = a[-lag:]
        b_aligned = b[: n + lag]
    return a_aligned, b_aligned


def _bootstrap_ci(
    a: np.ndarray, b: np.ndarray, n_samples: int, rng: np.random.Generator,
) -> tuple[float | None, float | None]:
    """Compute the 2.5/97.5 percentile range of the bootstrap r."""
    rs: list[float] = []
    n = len(a)
    for _ in range(n_samples):
        idx = rng.integers(0, n, n)
        a_b = a[idx]
        b_b = b[idx]
        if np.std(a_b) == 0 or np.std(b_b) == 0:
            continue
        r_b, _ = stats.pearsonr(a_b, b_b)
        if np.isfinite(r_b):
            rs.append(float(r_b))
    # Half-bootstrap threshold to call CI usable.
    if len(rs) < n_samples * 0.5:
        return None, None
    return float(np.percentile(rs, 2.5)), float(np.percentile(rs, 97.5))


def _classify_confidence(n: int) -> str:
    if n < 15:
        return "insufficient"
    if n < 30:
        return "exploratory"
    if n < 100:
        return "moderate"
    return "high"


def _caveat_for_confidence(level: str, n: int) -> str | None:
    if level == "exploratory":
        return (
            f"Only {n} aligned pairs at the strongest lag — treat as "
            f"exploratory. Patterns at this sample size are hypotheses."
        )
    return None


def _build_clinical_note(
    lag: int, r: float, metric_a: str, metric_b: str,
) -> str:
    direction = "decreases" if r < 0 else "increases"
    if lag == 0:
        return (
            f"Same-day effect: when {metric_a} rises, {metric_b} "
            f"{direction} on the same day (r={r:+.2f})."
        )
    if lag > 0:
        return (
            f"Effect appears strongest {lag} day(s) after {metric_a} "
            f"changes — {metric_b} {direction} {lag} day(s) later "
            f"(r={r:+.2f})."
        )
    return (
        f"Strongest signal is at lag {lag} (effect BEFORE cause). "
        f"This is biologically implausible; review the analysis."
    )


# -----------------------------------------------------------------------
# Error envelopes
# -----------------------------------------------------------------------


def _envelope_error(
    code: str,
    message: str,
    metric_a: str,
    metric_b: str,
    start: date_t,
    end: date_t,
    lag_range: tuple[int, int],
) -> dict[str, Any]:
    return {
        "method": "cross_correlation_with_bootstrap_ci",
        "code": code,
        "error": message,
        "metric_a": metric_a,
        "metric_b": metric_b,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "lag_range": list(lag_range),
        "lag_correlations": [],
        "n_observations": 0,
    }


def _envelope_insufficient_data(
    metric_a: str,
    metric_b: str,
    start: date_t,
    end: date_t,
    lag_range: tuple[int, int],
    n: int,
) -> dict[str, Any]:
    return {
        "method": "cross_correlation_with_bootstrap_ci",
        "code": "INSUFFICIENT_DATA",
        "error": (
            f"Need at least 15 paired daily values to compute lag "
            f"correlations; got {n}. Either the date range is too "
            f"narrow or one of the metrics has too few logged days."
        ),
        "metric_a": metric_a,
        "metric_b": metric_b,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "lag_range": list(lag_range),
        "lag_correlations": [],
        "n_observations": n,
        "confidence_level": "insufficient",
    }
