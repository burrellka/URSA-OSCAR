# Ridge Regression with Cross-Validated Prediction Intervals

*This page mirrors the methodology entry in URSA-OSCAR's analytical core. The same description ships in every PDF report that uses this method.*

## Description

Ridge regression is a regularized linear regression that adds an L2 penalty to the loss function, reducing model variance at the cost of small bias. The regularization parameter alpha is selected by 5-fold cross-validation from a logarithmic grid. Prediction intervals are computed by fitting four separate quantile regression models at the 2.5th, 25th, 75th, and 97.5th percentiles, yielding both a 50% interval (where the actual value falls roughly half the time) and a 95% interval. The cross-validation R² is reported alongside the prediction as a model-quality scalar.

## Limitations

Linear models assume additive relationships between predictors and target. Ridge handles correlated predictors but does not capture non-linear or interactive effects. Predictions extrapolate poorly beyond the range of training data. Counterfactual predictions ("what if X changed?") are evaluated at the model's input points, not retrained — the counterfactual reflects the trained model's response, not necessarily what would happen in reality if the input actually changed.

## Sample size

URSA-OSCAR requires at least 30 nights of training data to fit predictive models, reflecting the higher data needs of regression over correlation. Confidence in predictions increases substantially above 50 nights and is considered high above 100 nights.

## Where this method is used in URSA-OSCAR

- **`/api/v1/analytics/predict`** — the prediction endpoint
- **`analyze_prediction` MCP tool** — when an AI assistant asks "if X changes, what happens to Y?" or "predict Y given current conditions"
- **Trends page → Predictive Modeling section** — point estimate + intervals + counterfactual analysis

## How to read prediction intervals

The point estimate is the model's best single guess. The 50% interval is where the truth lands about half the time — narrow but uncertain. The 95% interval is where the truth lands 19 times out of 20 — wider but rarely wrong.

If your 95% prediction interval is `[2.1, 7.4]` for AHI, the model is saying "based on the predictors you gave me and 47 nights of training, your next-night AHI will probably fall in this range." That's a different statement from "your AHI WILL be 4.2" — point estimates without intervals are misleading.

## Counterfactuals — what they are and aren't

A counterfactual prediction asks "if X had been different by Δ, what would Y have been?" The model evaluates that question against its existing fit — it doesn't refit. So the answer is the model's response to a hypothetical input, not a real-world prediction of what would happen if you actually changed X.

If your model says "increasing mask-on time by 60 minutes reduces predicted AHI by 1.2," that's the model's internal sensitivity to mask-on time given the data it was trained on. Whether actually wearing the mask 60 more minutes would actually reduce your AHI by 1.2 depends on whether the model's assumed relationship holds in reality — which is a separate question that requires clinical reasoning, not just regression.
