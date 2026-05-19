# The Statistics page

Aggregate view of your CPAP data over a configurable window. Where the "how am I doing this month?" question lives.

## Window selector

Four windows: **7d**, **30d**, **90d**, **All**.

- **7d**: useful for catching a recent shift before it shows up in longer windows. Noisy enough that single-night values dominate.
- **30d**: the most common "right now" window. Stable enough that you can act on the numbers, recent enough to reflect current therapy.
- **90d**: medium-term trend baseline. Smooths out single-night and single-week noise.
- **All**: lifetime aggregates over everything URSA-OSCAR has imported. Useful for "since I started CPAP" or "since the device was new."

The selected window determines both the date range AND the usage-rate denominator.

## Usage breakdown card

The top of the page (above the Aggregates table) shows your usage rate:

> **18 used / 12 skipped · 60% usage** over the last 30 days

For fixed windows (7d / 30d / 90d):

- `used` = nights in the window with therapy data in URSA-OSCAR
- `skipped` = window days without data (operator didn't use the CPAP, traveled, ill, etc.)
- `usage %` = used / total

For the **All** window, the denominator is ambiguous (earliest imported night → today, but earliest may pre-date when you started using URSA-OSCAR), so the page shows just the night count without a percentage.

## Aggregates table

Mean, median, min, max, and standard deviation for every nightly metric in your window. One row per metric:

- AHI total + sub-indices (obstructive, central, hypopnea)
- Pressure metrics (median, p95)
- Leak metrics (p95)
- Mask-on minutes

The aggregates are computed over the nights with data only — no-session nights don't enter the numerator OR denominator. This is the clinical-convention choice (architect decision 1a in the 0.13.4 patch): rolling averages compute over therapy nights, not over calendar days.

## Histograms

Visual distributions for the most-watched metrics. One histogram each for:

- Nightly AHI
- 95% pressure
- Mask-on minutes
- Central AHI
- Obstructive AHI
- Large-leak %

The histogram shape tells you something the mean and median don't:

- **Bell-shaped** centered on a low value → consistent therapy, normal day-to-day variation
- **Bimodal** (two peaks) → two distinct regimes. Often "nights I slept well" + "nights I didn't" — worth checking what differentiates them in the manual logs
- **Long right tail** → most nights are fine but some nights are bad. The mean gets pulled up by the tail; the median tells you the typical night
- **Spike at zero** in central AHI → most nights are clean centrals, occasional nights have a burst. Look at those specific nights

## Reading the standard deviation

The std-dev column tells you how variable your nights are.

A mean AHI of 5 with std-dev 1 is "consistently around 5, plus or minus a little." A mean AHI of 5 with std-dev 4 is "could be 1, could be 9 on any given night." The first is well-controlled; the second is poorly-controlled even though the mean is the same.

Std-dev > mean × 0.5 = high variability. Worth provider conversation.

## When the Statistics page is misleading

The aggregates assume the nights in the window are samples from one consistent therapy regime. If you changed your pressure settings, your mask, or your medication in the middle of the window, the aggregate is averaging across the change.

For "did my recent change help?" questions:

- Use the Trends page (linear fit identifies the change point if R² is high enough)
- Or use compare_periods on the two halves of the window

For "did this medication affect my AHI?":

- Use the lag-correlation tool with the medication's manual-log entries as the predictor

The Statistics page is for "describe my CPAP data over this window," not "did X cause Y."

## Why no per-metric trend on this page

The Statistics page deliberately doesn't show "this metric is trending up/down" annotations. Trend determination needs the Trends page's full machinery (R², p-value, sample-size guards). Surfacing a half-baked trend on Statistics would be misleading. If you want trend, click into Trends.

## What's not on Statistics

- **Single-night detail.** That's the Daily View.
- **Event lists.** That's the Events page.
- **Predictions.** That's the Trends page (Predictive Modeling section).
- **PDF export.** Use Reports.
