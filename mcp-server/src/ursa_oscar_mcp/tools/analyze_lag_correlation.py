"""analyze_lag_correlation — Phase 6 Ticket 6.1 Item 3 MCP tool.

Thin proxy over POST /api/v1/analytics/lag-correlation. Computes the
cross-correlation function between two metrics across a lag window,
with bootstrap 95% confidence intervals at each lag.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_post
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def analyze_lag_correlation(
    metric_a: str,
    metric_b: str,
    start_date: str,
    end_date: str,
    lag_range_days: list[int] | None = None,
    bootstrap_samples: int = 1000,
    recompute: bool = False,
) -> dict:
    """Cross-correlation function across a lag window with bootstrap CIs.

    Use this tool when the user asks about delayed effects — when one
    thing happens, how long until the effect on another shows up?

        "How long after I take doxepin does it start working?"
        "Does last night's alcohol still affect my AHI two nights later?"
        "When does a pressure change take effect on my central index?"
        "If I exercise today, is my sleep better tonight or tomorrow?"

    The method is the Pearson cross-correlation function: for each
    integer lag in the window, align metric_a[day t] with
    metric_b[day t + lag], compute Pearson r, then resample with
    replacement to compute a bootstrap 95% confidence interval.

    Interpretation:
      - peak_lag_days: the lag at which |r| is largest
      - clinical_note: human-readable summary of the peak
      - For each lag, the CI tells you whether the effect is real:
        * CI excludes zero → real effect at that lag
        * CI spans zero    → not distinguishable from noise

    Negative lag values (effect before cause) are included as a sanity
    check. If you see a strong correlation at lag -2 (AHI two days
    before doxepin "predicts" the dose), something is wrong — surface
    that to the user.

    Sample-size discipline (enforced):
      - n at any lag must be ≥ 15 or that lag is dropped
      - Overall n < 15 → returns {ok: false, code: "INSUFFICIENT_DATA"}
      - confidence_level: "exploratory" (15-29) / "moderate" (30-99) / "high" (100+)

    When relaying to the user:
      - Lead with the peak_lag_days + clinical_note ("doxepin's effect
        peaks 1 day after the dose")
      - Mention the confidence_level naturally ("moderate confidence —
        47 aligned pairs")
      - If CIs span zero across all lags, say the effect isn't real
        ("at this sample size the data doesn't show a reliable
        time-lagged effect")

    Metric naming convention (same as analyze_correlation):
        Bare nightly column — "total_ahi", "p95_pressure", etc.
        Manual-log metric — "medication:Doxepin:dose", "alertness::score", etc.

    Args:
        metric_a: hypothesized cause
        metric_b: hypothesized effect
        start_date: YYYY-MM-DD inclusive
        end_date: YYYY-MM-DD inclusive
        lag_range_days: [lo, hi] inclusive. Default [-3, 7]. Negative
            values are sanity checks; positive values are the
            biologically-plausible direction.
        bootstrap_samples: how many resamples for the CI. Default 1000.
        recompute: bypass the cache. Default false.

    Returns:
        On success:
          {"ok": true, "data": {
              "method": "cross_correlation_with_bootstrap_ci",
              "metric_a": "...", "metric_b": "...",
              "lag_range": [lo, hi],
              "lag_correlations": [
                {"lag_days": k, "r": ..., "p_value": ...,
                 "ci_95": [lo, hi], "n_aligned": ...},
                ...
              ],
              "peak_lag_days": k_peak,
              "peak_correlation": r_peak,
              "peak_p_value": p_peak,
              "interpretation": "moderate_negative_correlation_at_lag_1",
              "clinical_note": "Effect appears strongest...",
              "n_observations": int,
              "confidence_level": "exploratory" | "moderate" | "high",
              "sample_caveat": null | "Only N pairs...",
              "cache_age_seconds": int,
              "computed_at": "..."
          }}

        On refusal (n < 15):
          {"ok": false, "data": {..., "code": "INSUFFICIENT_DATA"}}
    """
    for label, value in [("start_date", start_date), ("end_date", end_date)]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")

    rng = lag_range_days if lag_range_days is not None else [-3, 7]
    if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, int) for v in rng)):
        return _err(
            "lag_range_days must be a 2-element list of ints, e.g. [-3, 7]",
            code="INVALID_INPUT",
        )
    if rng[1] < rng[0]:
        return _err(
            f"lag_range_days upper bound ({rng[1]}) must be >= lower ({rng[0]})",
            code="INVALID_INPUT",
        )

    try:
        return api_post(
            "/api/v1/analytics/lag-correlation",
            json_body={
                "metric_a": metric_a,
                "metric_b": metric_b,
                "start_date": start_date,
                "end_date": end_date,
                "lag_range_days": rng,
                "bootstrap_samples": int(bootstrap_samples),
                "recompute": bool(recompute),
            },
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
