"""Methodology registry for the provider PDF reports — Phase 6 Ticket 6.3.

Maps each analytical ``method`` string (the literal value returned by
6.1's correlation / lag tools and 6.2's predict tool) to a human-readable
description block: name, what it computes, its limitations, and the
sample-size convention.

Per Decision 6.3-D this registry is non-optional — every PDF includes a
Methodology section that pulls descriptions for every method actually
used in the report. New analytical methods added in Phase 7+ MUST be
registered here or the report generator raises on assembly.

The text is written for a clinician reading the PDF. Clinical-peer
register: assumes familiarity with correlation / regression / confidence
intervals; doesn't over-explain.
"""
from __future__ import annotations

from typing import TypedDict


class MethodologyEntry(TypedDict):
    name: str
    description: str
    limitations: str
    sample_size_note: str


# Keyed by the `method` field returned by analytical compute functions.
# When ``analyze_correlation`` returns ``method: "pairwise_correlation_pearson"``
# the PDF's Methodology section renders this entry.
METHODOLOGY_REGISTRY: dict[str, MethodologyEntry] = {
    "pairwise_correlation_pearson": {
        "name": "Pearson Correlation",
        "description": (
            "Pearson correlation measures the linear relationship between "
            "two variables, ranging from −1 (perfect negative) to +1 "
            "(perfect positive). A value of 0 indicates no linear "
            "relationship. Significance is reported via p-value, the "
            "probability of observing the correlation by chance if the "
            "variables were unrelated. P < 0.05 is conventionally "
            "considered statistically significant."
        ),
        "limitations": (
            "Pearson correlation captures only LINEAR relationships. Two "
            "variables can be strongly related (e.g., U-shaped) and show "
            "a near-zero Pearson correlation. Correlation does not "
            "establish causation."
        ),
        "sample_size_note": (
            "Confidence in the correlation strength increases with sample "
            "size. With fewer than 30 paired data points the correlation "
            "should be treated as exploratory."
        ),
    },
    "partial_correlation_pearson": {
        "name": "Partial Correlation (multivariate)",
        "description": (
            "Partial correlation measures the relationship between two "
            "variables while statistically controlling for the influence "
            "of other measured variables. The method uses residual "
            "regression: each variable is regressed on all other variables, "
            "then Pearson correlation is computed on the residuals. This "
            "isolates the unique association between two variables that "
            "is NOT explained by the other measured factors. 95% confidence "
            "intervals are computed via bootstrap resampling (1000 "
            "iterations)."
        ),
        "limitations": (
            "Partial correlation can only control for variables that are "
            "measured and included. Unmeasured confounders are not "
            "addressed. The method assumes linear relationships and can "
            "be sensitive to multicollinearity among predictors. The "
            "report flags multicollinear predictor pairs (r > 0.9) "
            "explicitly."
        ),
        "sample_size_note": (
            "Reliable partial correlation requires at least 15 paired "
            "observations; 30+ is preferred for the confidence interval "
            "to narrow usefully. Below 15, URSA-OSCAR refuses to compute "
            "and reports INSUFFICIENT_DATA."
        ),
    },
    "cross_correlation_with_bootstrap_ci": {
        "name": "Time-shifted Cross-Correlation with Bootstrap Intervals",
        "description": (
            "For two daily-aggregated metrics A and B, the method computes "
            "Pearson correlation between A(t) and B(t + k) at each "
            "integer lag k in a window. A bootstrap 95% confidence "
            "interval is computed at each lag by resampling aligned "
            "pairs with replacement (1000 iterations). The lag with the "
            "largest |r| whose confidence interval excludes zero is "
            "highlighted as the peak. Negative lags are included as a "
            "sanity check — a strong correlation at lag −2 (effect "
            "before cause) is biologically implausible and indicates "
            "noise rather than a real effect."
        ),
        "limitations": (
            "Cross-correlation does not establish causation. Time-shifted "
            "correlations can arise from shared periodic patterns (e.g., "
            "both metrics responding to a weekly cycle) without a direct "
            "causal link. Lags below the n=15 floor per lag are dropped "
            "from the analysis."
        ),
        "sample_size_note": (
            "Each lag requires at least 15 aligned pairs. The overall "
            "analysis requires at least 15 paired observations in the "
            "date range. Confidence intervals widen at edge lags where "
            "the aligned-pair count drops."
        ),
    },
    "ridge_regression_cv_with_quantile_intervals": {
        "name": "Ridge Regression with Cross-Validated Prediction Intervals",
        "description": (
            "Ridge regression is a regularized linear regression that "
            "adds an L2 penalty to the loss function, reducing model "
            "variance at the cost of small bias. The regularization "
            "parameter alpha is selected by 5-fold cross-validation from "
            "a logarithmic grid. Prediction intervals are computed by "
            "fitting four separate quantile regression models at the "
            "2.5th, 25th, 75th, and 97.5th percentiles, yielding both a "
            "50% interval (where the actual value falls roughly half the "
            "time) and a 95% interval. The cross-validation R² is "
            "reported alongside the prediction as a model-quality scalar."
        ),
        "limitations": (
            "Linear models assume additive relationships between "
            "predictors and target. Ridge handles correlated predictors "
            "but does not capture non-linear or interactive effects. "
            "Predictions extrapolate poorly beyond the range of training "
            "data. Counterfactual predictions ('what if X changed?') are "
            "evaluated at the model's input points, not retrained — the "
            "counterfactual reflects the trained model's response, not "
            "necessarily what would happen in reality if the input "
            "actually changed."
        ),
        "sample_size_note": (
            "URSA-OSCAR requires at least 30 nights of training data to "
            "fit predictive models, reflecting the higher data needs of "
            "regression over correlation. Confidence in predictions "
            "increases substantially above 50 nights and is considered "
            "high above 100 nights."
        ),
    },
    "linear_regression_least_squares": {
        "name": "Linear Trend (Least-Squares Regression)",
        "description": (
            "A simple linear trend is fit to the metric over the date "
            "range using ordinary least-squares regression on day-index "
            "vs. metric value. The slope (per day), intercept, R² "
            "(fraction of variance explained), and p-value (probability "
            "the slope is zero) are reported. Optional forward projection "
            "extrapolates the fitted slope a specified number of days."
        ),
        "limitations": (
            "A linear trend captures only monotonic change at a constant "
            "rate. Non-linear patterns (e.g., a recent improvement after "
            "a long plateau) may show a misleading linear fit. R² near "
            "zero means the trend explains very little of the day-to-day "
            "variation."
        ),
        "sample_size_note": (
            "Trend analysis requires at least 5 nights to fit; 30+ nights "
            "give meaningfully tight slope estimates. Forward projections "
            "should not be extrapolated more than ~25% of the training "
            "window length without explicit caveat."
        ),
    },
    "compare_periods_mean_difference": {
        "name": "Period Comparison (Mean Difference)",
        "description": (
            "Two date ranges (Period A and Period B) are summarized by "
            "the mean and median of each requested metric. The absolute "
            "delta and relative percentage change between Period A's and "
            "Period B's means is reported per metric. No statistical test "
            "for significance is applied — this is descriptive comparison "
            "intended for routine 'how did this month compare to last' "
            "questions."
        ),
        "limitations": (
            "Mean comparison is sensitive to outliers; the median delta "
            "is reported alongside for that reason. Periods of unequal "
            "length are not normalized — the comparison is between the "
            "actual mean of each period as recorded. Significance testing "
            "of period differences is deferred to dedicated tools "
            "(correlation, regression)."
        ),
        "sample_size_note": (
            "Each period needs at least 3 nights for the comparison to "
            "be reportable. With fewer than 7 nights per period the "
            "comparison should be treated as qualitative."
        ),
    },
}


def lookup_methodology(method_key: str) -> MethodologyEntry | None:
    """Return the registry entry for a method, or None if unknown.

    The PDF generator uses None to detect unregistered methods — see
    ``collect_methodology_descriptions`` for the strict-mode behavior."""
    return METHODOLOGY_REGISTRY.get(method_key)


def collect_methodology_descriptions(
    method_keys: list[str],
    *,
    strict: bool = True,
) -> list[MethodologyEntry]:
    """Return registry entries for the methods that appear in a report.

    Duplicates in the input are deduplicated while preserving order
    (first appearance wins). Unknown methods raise ``MissingMethodologyError``
    in strict mode — the audit-trail discipline from Decision 6.3-D.
    Lenient mode (strict=False) skips unknown methods silently; reserved
    for backfill / migration scenarios, not production rendering.
    """
    seen: set[str] = set()
    out: list[MethodologyEntry] = []
    for key in method_keys:
        if key in seen or key is None:
            continue
        seen.add(key)
        entry = METHODOLOGY_REGISTRY.get(key)
        if entry is None:
            if strict:
                raise MissingMethodologyError(
                    f"Method '{key}' appeared in a report's analytical "
                    f"results but has no registry entry. Every analytical "
                    f"method MUST register a methodology description in "
                    f"methodology_registry.py before its output can be "
                    f"included in a PDF. This guard exists so the "
                    f"Methodology section never has a 'stealth method' "
                    f"without explanation."
                )
            continue
        out.append(entry)
    return out


class MissingMethodologyError(RuntimeError):
    """Raised when a report's data contains a `method` value that hasn't
    been registered. Decision 6.3-D: this is a hard failure, not a
    soft warning — the PDF cannot ship without a complete Methodology
    section."""
