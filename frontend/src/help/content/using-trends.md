# The Trends page

Where the multi-night and statistical analysis lives. Five sections, each backed by a Phase 6 analytical endpoint.

## 1. Single-metric trend

Pick a metric (any nightly_summary column) and a date range. URSA-OSCAR fits a linear regression to the metric's daily values and shows:

- A line chart of the metric over time with the regression line overlaid
- The slope (units per day)
- R² (fraction of variance the line explains)
- p-value (probability the slope is zero)
- A forward projection (default 30 days) — guarded by the safe_projection sample-size + bounds checks

**Default range:** last 90 days, because Kevin's experience showed 30 days misses the longer-arc trajectory most operators care about.

**How to read it:**

- R² > 0.4: the trend is meaningful; the line explains real movement
- R² 0.1 - 0.4: the trend exists but day-to-day noise is significant
- R² < 0.1: the line is essentially flat against noise; don't act on it
- p < 0.05: the slope is statistically distinguishable from zero

Methodology details: see the **Linear Trend** page in the Methodology section.

## 2. Pairwise correlation

Pick two metrics + a date range. URSA-OSCAR computes the Pearson correlation between the two over the range and shows:

- A scatter plot of metric A vs metric B (one point per night)
- A regression line through the points
- Pearson r, p-value, n
- A sample-size warning if n < 30

**Default range:** all data, because correlations benefit from as many samples as possible.

**How to read it:** see the **Pearson Correlation** page in the Methodology section.

## 3. Multivariate (partial) correlation

Pick a target metric + a list of predictor metrics + a range. URSA-OSCAR computes the partial correlation between the target and each predictor while controlling for the others.

Shown as a table: predictor name, partial r, 95% CI (bootstrap), p-value, interpretation, plus a multicollinear-pairs callout if any two predictors are highly correlated with each other.

**Use case:** disentangling "AHI is correlated with everything, what's actually driving it?" If pressure and central index are both correlated with AHI in pairwise tests, partial correlation tells you which (or both) survive controlling for the other.

**Floor:** 15 paired observations. Below that, URSA-OSCAR refuses to compute and returns `INSUFFICIENT_DATA`.

Methodology details: see the **Partial Correlation** page.

## 4. Lag analysis

Pick metric A and metric B + a range. URSA-OSCAR computes cross-correlation at lags −7 to +7 days and shows:

- A lag profile chart: correlation strength on the y-axis, lag in days on the x-axis
- The peak lag (largest |r| whose CI excludes zero)
- A clinical interpretation note

**Use case:** "does today's poor sleep environment correlate with tomorrow night's AHI?" or "does a high-AHI night today predict elevated morning headache the day after?"

The negative lags are a sanity check — a strong correlation at lag −2 (effect before cause) is implausible and indicates noise. URSA-OSCAR surfaces this honestly.

Methodology details: see the **Time-shifted Cross-Correlation** page.

## 5. Predictive modeling

Pick a target metric, a list of predictors, a training date range, optional counterfactual inputs. URSA-OSCAR fits a cross-validated ridge regression and returns:

- A point prediction
- 50% and 95% prediction intervals
- Cross-validation R² (model quality scalar)
- Per-predictor coefficients (which predictors matter, by how much)
- Counterfactual analysis if you supplied overrides ("what if mask-on time were 60 minutes higher?")

**Floor:** 30 nights of training data. Below that, URSA-OSCAR refuses and returns `INSUFFICIENT_DATA`.

Methodology details: see the **Ridge Regression** page.

## When trends don't move

A common operator experience: you make a change (different mask, new EPR setting, weight loss) and the trends don't budge.

Things to check:

- **Window size**: a 7-day window might be too short to detect a change. Try 30 or 90 days.
- **R² check**: maybe the change had an effect but daily noise dominates. The regression line itself can be flat-looking even if there's signal.
- **Confounders**: if your weight changed AND your mask changed AND your EPR changed all in the same week, no single-metric trend will isolate which one mattered. Use multivariate correlation to see which factor survives controlling for the others.

## When trends *do* move dramatically

The opposite caution: a steeply trending metric over a short window is usually noise, not signal. Three nights of unusually good sleep can produce a steep "improving" linear fit that's meaningless because n=3 + R²=0.95 just means the line fit those three nights, not that the trend will continue.

The safe_projection guards (sample-size + physical bounds) protect against acting on these. If you see a suppressed projection, that's URSA-OSCAR saying "this trend isn't well-supported enough to extrapolate."

## Provider conversation prep

The Trends page is where you build the case for a provider conversation. "My AHI has been trending up at +0.05/day over the last 90 days, R²=0.42, p<0.01" is more useful than "my AHI feels worse lately." Generate a PDF report from Reports → Analytical Report to put the trend on paper.
