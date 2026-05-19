# Period Comparison (Mean Difference)

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method.*

## Description

Two date ranges (Period A and Period B) are summarized by the mean and median of each requested metric. The absolute delta and relative percentage change between Period A's and Period B's means is reported per metric. No statistical test for significance is applied — this is descriptive comparison intended for routine "how did this month compare to last" questions.

## Limitations

Mean comparison is sensitive to outliers; the median delta is reported alongside for that reason. Periods of unequal length are not normalized — the comparison is between the actual mean of each period as recorded. Significance testing of period differences is deferred to dedicated tools (correlation, regression).

## Sample size

Each period needs at least 3 nights for the comparison to be reportable. With fewer than 7 nights per period the comparison should be treated as qualitative.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/compare-periods`** — the period-comparison endpoint
- **`compare_periods` MCP tool** — when an AI assistant asks "how does this week compare to last week?"
- **Statistics page** — when you switch the 7d/30d/90d window
- **Reports** — period-over-period comparisons in the Full Clinical Report template

## Usage rate breakdown (0.13.4+)

Period-comparison responses include a usage breakdown alongside each metric: `n_nights_in_range`, `n_nights_with_therapy`, `n_nights_skipped`, `usage_rate_pct`. This is how the operator sees that, say, "Period A: 18 of 30 nights used (60%)" alongside the clinical metrics computed only from the 18.

Why surface usage rate? Because a comparison that says "Period A mean AHI: 4.2, Period B mean AHI: 3.8" looks like progress but might just be "you wore the mask 18 nights in Period A and only 5 in Period B" — the comparison is over a different denominator. Usage rate makes that explicit.

## How to use period comparison sensibly

- **Equal-length periods** make the comparison cleaner. Two 30-day windows are more interpretable than a 30-day vs. a 7-day window.
- **Adjacent or recent periods** are easier to interpret than periods separated by a long gap — your therapy changes, machine settings, and life circumstances all shift over months.
- **Low n in either period** → treat the result as qualitative. The 3-night minimum is the floor for the comparison to compute; 7+ in each period is when the means start being meaningfully stable.
- **Median delta > mean delta in magnitude** suggests outlier influence. A single bad night can pull a 7-night mean noticeably; the median is more robust.
