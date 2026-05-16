"""analyze_multivariate_correlation — Phase 6 Ticket 6.1 Item 2 MCP tool.

Thin proxy over POST /api/v1/analytics/multivariate-correlation. Computes
partial correlations for each predictor against a target, controlling
for the other predictors. Bootstrap 95% CIs and p-values per predictor.

Use when the user wants to disentangle multiple candidate causes of a
single outcome. Pairwise correlation (the existing analyze_correlation)
can't tell you whether doxepin is "really" helping AHI vs. whether
something else (pressure changes, leak, etc.) is doing the work; this
tool can.
"""
from __future__ import annotations

from datetime import date as date_t

import httpx

from ..client import api_post
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def analyze_multivariate_correlation(
    target_metric: str,
    predictor_metrics: list[str],
    start_date: str,
    end_date: str,
    recompute: bool = False,
) -> dict:
    """Partial correlation of each predictor with the target,
    controlling for the other predictors.

    Use this tool when the user asks "is X really driving Y, or is
    something else doing the work?"-shaped questions:

        "Is doxepin really helping my AHI, or is it the pressure changes?"
        "What's driving my central index — medication or mask fit?"
        "Does evening alcohol matter, or is it really about sleep duration?"
        "After controlling for leak rate, does humidity matter?"

    The method is partial correlation (Pearson r between residuals after
    linear regression on the other predictors). The response carries
    method="partial_correlation_pearson" so the answer is reproducible
    and defensible.

    For each predictor the tool returns:
      - partial_r       — strength of the unique relationship
      - p_value         — statistical significance of partial_r
      - ci_95           — bootstrap 95% confidence interval
      - interpretation  — machine label (strength + direction + significance)

    Sample-size discipline is enforced as a refusal:
      - n < 15: returns {ok: false, code: "INSUFFICIENT_DATA"}
      - 15 ≤ n < 30: confidence_level="exploratory", surface that to user
      - 30 ≤ n < 100: confidence_level="moderate"
      - n ≥ 100: confidence_level="high"

    When relaying results to the user:
      - Surface the confidence_level naturally ("moderate confidence —
        47 observations") so they know how much to trust the number
      - If a CI spans zero, say so ("the effect isn't statistically
        distinguishable from noise here")
      - If multicollinear_pairs is non-empty, mention that two predictors
        are near-duplicates — the partial r for either may be unstable
      - Never quote partial_r without its CI when both are available

    Metric naming convention (same as analyze_correlation):

        Bare nightly_summary column — "total_ahi", "central_ahi",
        "obstructive_ahi", "p95_pressure", "p95_leak", etc.

        Manual-log metric — "log_type:filter:field":
            "medication:Doxepin:dose"
            "medication:Melatonin"  (default field=dose)
            "symptom:headache:severity"
            "alertness::score"
            "alertness:morning:score"
            "sleep_environment::temperature_c"

    Args:
        target_metric: outcome to explain
        predictor_metrics: 2-5 candidate predictors to test simultaneously
        start_date: YYYY-MM-DD inclusive
        end_date: YYYY-MM-DD inclusive
        recompute: skip the cache; force a fresh computation. Default false.

    Returns:
        On success:
          {"ok": true, "data": {
              "method": "partial_correlation_pearson",
              "target_metric": "...",
              "predictors": [
                {"metric": "...", "partial_r": -0.42, "p_value": 0.003,
                 "ci_95": [-0.61, -0.18], "interpretation": "moderate_negative"},
                ...
              ],
              "controlled_for": [...],
              "n_observations": 47,
              "confidence_level": "moderate" | "exploratory" | "high",
              "sample_caveat": null | "Only N observations...",
              "multicollinear_pairs": [],
              "cache_age_seconds": 0,
              "computed_at": "..."
          }}

        On refusal (n < 15):
          {"ok": false, "data": {..., "code": "INSUFFICIENT_DATA",
                                 "error": "Need at least 15..."}}
    """
    for label, value in [("start_date", start_date), ("end_date", end_date)]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")
    if not isinstance(predictor_metrics, list):
        return _err("predictor_metrics must be a list of strings", code="INVALID_INPUT")
    if not (2 <= len(predictor_metrics) <= 5):
        return _err(
            f"predictor_metrics must contain 2-5 metrics; got {len(predictor_metrics)}",
            code="INVALID_INPUT",
        )

    try:
        body = api_post(
            "/api/v1/analytics/multivariate-correlation",
            json_body={
                "target_metric": target_metric,
                "predictor_metrics": predictor_metrics,
                "start_date": start_date,
                "end_date": end_date,
                "recompute": bool(recompute),
            },
        )
        # The API endpoint already wraps in {ok, data}; pass through unchanged.
        return body
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return _err(f"Bad request: {e.response.text}", code="INVALID_INPUT")
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")
