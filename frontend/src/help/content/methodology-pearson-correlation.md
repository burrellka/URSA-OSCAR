# Pearson Correlation

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method, so the report and the Help page never drift.*

## Description

Pearson correlation measures the linear relationship between two variables, ranging from −1 (perfect negative) to +1 (perfect positive). A value of 0 indicates no linear relationship. Significance is reported via p-value, the probability of observing the correlation by chance if the variables were unrelated. P < 0.05 is conventionally considered statistically significant.

## Limitations

Pearson correlation captures only LINEAR relationships. Two variables can be strongly related (e.g., U-shaped) and show a near-zero Pearson correlation. Correlation does not establish causation.

## Sample size

Confidence in the correlation strength increases with sample size. With fewer than 30 paired data points the correlation should be treated as exploratory.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/correlation`** — the pairwise correlation endpoint
- **`analyze_correlation` MCP tool** — when an AI assistant asks "is X correlated with Y over this range?"
- **Trends page → Correlation section** — the scatter plot with regression overlay
