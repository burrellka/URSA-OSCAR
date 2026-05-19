# Partial Correlation (multivariate)

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method.*

## Description

Partial correlation measures the relationship between two variables while statistically controlling for the influence of other measured variables. The method uses residual regression: each variable is regressed on all other variables, then Pearson correlation is computed on the residuals. This isolates the unique association between two variables that is NOT explained by the other measured factors. 95% confidence intervals are computed via bootstrap resampling (1000 iterations).

## Limitations

Partial correlation can only control for variables that are measured and included. Unmeasured confounders are not addressed. The method assumes linear relationships and can be sensitive to multicollinearity among predictors. The report flags multicollinear predictor pairs (r > 0.9) explicitly.

## Sample size

Reliable partial correlation requires at least 15 paired observations; 30+ is preferred for the confidence interval to narrow usefully. Below 15, URSA-OSCAR refuses to compute and reports `INSUFFICIENT_DATA`.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/multivariate-correlation`** — the partial correlation endpoint
- **`analyze_multivariate_correlation` MCP tool** — when an AI assistant asks "is X correlated with Y after controlling for Z?"
- **Trends page → Multivariate section** — controlled-correlation table

## When to prefer partial over pairwise

Use pairwise Pearson when you want the raw relationship between two metrics. Use partial when you suspect a third variable is driving the apparent relationship and want to see if the link survives controlling for it.

Example: if pressure and AHI are both correlated with mask-on time, a simple pairwise correlation between pressure and AHI may reflect the shared dependence on mask-on time rather than a direct pressure → AHI relationship. Partial correlation, controlling for mask-on time, shows what's left.
