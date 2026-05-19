# Time-shifted Cross-Correlation with Bootstrap Intervals

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method.*

## Description

For two daily-aggregated metrics A and B, the method computes Pearson correlation between A(t) and B(t + k) at each integer lag k in a window. A bootstrap 95% confidence interval is computed at each lag by resampling aligned pairs with replacement (1000 iterations). The lag with the largest |r| whose confidence interval excludes zero is highlighted as the peak. Negative lags are included as a sanity check — a strong correlation at lag −2 (effect before cause) is biologically implausible and indicates noise rather than a real effect.

## Limitations

Cross-correlation does not establish causation. Time-shifted correlations can arise from shared periodic patterns (e.g., both metrics responding to a weekly cycle) without a direct causal link. Lags below the n=15 floor per lag are dropped from the analysis.

## Sample size

Each lag requires at least 15 aligned pairs. The overall analysis requires at least 15 paired observations in the date range. Confidence intervals widen at edge lags where the aligned-pair count drops.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/lag-correlation`** — the lag-correlation endpoint
- **`analyze_lag_correlation` MCP tool** — when an AI assistant asks "does X today predict Y tomorrow?" or similar
- **Trends page → Lag Analysis** — the lag profile chart

## How to read a lag profile

The chart plots correlation strength (r) on the y-axis against lag in days on the x-axis. Lag 0 is the same-day correlation. Positive lags ask "does A *today* relate to B *N days later*?" — that's the causally plausible direction when A precedes B.

The peak lag is the value of k where |r| is largest AND the bootstrap confidence interval excludes zero. If no lag's CI excludes zero, the analysis returns "no significant lagged effect" rather than picking the largest r — that's a guard against fishing.

A strong correlation at a *negative* lag (cause before effect, reversed) is the diagnostic that the apparent relationship is spurious. URSA-OSCAR surfaces this honestly in the interpretation text.
