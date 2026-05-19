# Linear Trend (Least-Squares Regression)

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method.*

## Description

A simple linear trend is fit to the metric over the date range using ordinary least-squares regression on day-index vs. metric value. The slope (per day), intercept, R² (fraction of variance explained), and p-value (probability the slope is zero) are reported. Optional forward projection extrapolates the fitted slope a specified number of days.

## Limitations

A linear trend captures only monotonic change at a constant rate. Non-linear patterns (e.g., a recent improvement after a long plateau) may show a misleading linear fit. R² near zero means the trend explains very little of the day-to-day variation.

## Sample size

Trend analysis requires at least 5 nights to fit; 30+ nights give meaningfully tight slope estimates. Forward projections should not be extrapolated more than ~25% of the training window length without explicit caveat.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/trend`** — the trend endpoint
- **`get_trend` MCP tool** — when an AI assistant asks "is X going up or down over the last N days?"
- **Trends page → Trend section** — line chart with regression overlay

## Safe projection (0.13.5+)

URSA-OSCAR's trend endpoint runs every forward projection through a safety filter before returning the value:

1. **Sample-size rule.** You need at least `max(5, projection_days × 0.25)` observed nights. For a 30-day projection that's 7 nights; for 60 days, 15. Below the threshold the projection is suppressed and the response includes an `insufficient_samples` suppression reason.

2. **Physical bounds clamp.** Every metric has a realistic physical range (AHI floors at 0, pressure ceiling is the device's hardware max, etc.). When the linear extrapolation projects outside the bounds, the value is clamped to the boundary and the raw unclamped value is preserved in `raw_projected_value` for transparency.

This is why you'll see "Projected in 30 days: 0.00 AHI (clamped to lower physical bound; raw extrapolation was −23.72)" instead of a misleading "−23.72 AHI" projection. The model is honestly saying "I extrapolated past zero, and AHI can't be negative."

## What to do when a trend looks linear but isn't

R² is the diagnostic. If your slope is steeply negative but R² is 0.05, the line fit through your data is essentially flat noise — the visible "trend" is just where the regression line happens to land. Don't act on a low-R² trend.

Conversely, R² > 0.4 over 30+ nights is a meaningful signal worth talking to your sleep medicine provider about, especially if the direction is unexpected given your current therapy adjustments.
