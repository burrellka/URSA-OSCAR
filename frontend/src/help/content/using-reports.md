# Reports

PDF clinical reports for sharing with your sleep medicine provider, your PCP, or your own records.

## Three templates

### Full clinical report

The comprehensive one. 8-12 pages depending on what's in your data. Sections:

- Executive summary (3-sentence headline)
- Nightly summary statistics over the report's date range
- AHI breakdown over time with rolling averages
- Pressure profile (median / p95 / p99.5 distributions and trends)
- Leak profile (with the redline-violation breakdown)
- Event distribution by type and time-of-night
- Trend analysis for AHI and the major sub-indices
- Correlation matrix for the metrics most commonly relevant clinically
- Manual log summary (medications, symptoms, alertness scores aligned with nightly metrics)
- Equipment / settings context
- Methodology section explaining every analytical method used
- Limitations + sample-size notes

This is what you bring to a provider visit when you want a real conversation about your therapy data. ~10-15 minutes of provider reading time, structured for clinical review.

### Summary report

The concise one. 2-3 pages. Headline numbers + the most recent trend + a flagged-items section if URSA-OSCAR detected anything worth attention. Good for routine check-ins or to email your provider before a visit.

### Analytical report

Focused on the Phase 6 statistical analyses. Skips the routine summary tables, deep-dives into:

- Linear trends with R² + sample-size discussion
- Multivariate correlations (which predictors actually matter, controlling for others)
- Lag analyses (cause-effect timing)
- Predictive models (point + interval estimates, counterfactual scenarios)

Most useful when you're investigating a specific hypothesis: "is my AHI getting worse?" "does my morning alertness track my AHI?" "would changing my pressure window help?"

## Generating a report

Pick template + date range. Click "Preview" to see what's going to be included before you commit — URSA-OSCAR returns a metadata-only preview showing:

- Estimated page count
- Sections that will be included
- Sections that don't have enough data (skipped with a note)
- The methodology entries that will be cited
- Sample-size warnings

Click "Generate PDF" once you're happy with the preview. The actual PDF generation runs server-side (WeasyPrint backend) and the file downloads when ready.

## What's intentionally NOT in reports

- **Diagnostic statements.** URSA-OSCAR does not say "you have moderate OSA" or "your CPAP is failing." It reports what your data shows. A provider does the diagnostic interpretation.
- **Therapy recommendations.** URSA-OSCAR does not say "raise your pressure" or "switch to BiPAP." Those are clinical decisions.
- **Comparisons to "normal".** URSA-OSCAR doesn't compare you to a population mean or a textbook reference. It compares you to yourself across time. Population-normal language belongs to your provider.

## Methodology section

Every report includes a Methodology section automatically — every analytical method that produced a number in the report has its description, limitations, and sample-size note printed in plain text. This is the Decision 6.3-D audit-trail discipline: no "stealth methods" that produce numbers without explanation. The Help pages in the Methodology section here are the same text that lands in the PDF.

If a method appears in your data but isn't registered, URSA-OSCAR fails report generation loudly rather than producing a PDF with unexplained analytical output.

## Sharing a report

The PDF is just a file. Email it, print it, upload to your provider portal, share with a family member. URSA-OSCAR doesn't host reports anywhere — they're generated on demand from the data in your DuckDB.

If you want the same report a week from now, regenerate it. Reports are derived data; the source data lives in DuckDB and is the canonical store.

## Cache + recompute

Reports use the analytical cache for repeated runs. The first time you generate a Full Clinical Report for "last 90 days," the underlying correlations / trends / etc. compute fresh. Subsequent runs within the cache TTL reuse the cached results.

If you want to force a fresh compute (e.g., after a re-import that changed some night's aggregates), use the "Recompute" toggle on the preview page.

## Common report pitfalls

- **Window too short**: a 7-night report has less than the minimum for most analytical methods. Trends, predictions, and partial correlations will show "insufficient samples" sections. Use Summary report for short windows.
- **No manual logs**: if you haven't logged any medications or symptoms, the Manual Log Summary section is short. Not a problem, just a note.
- **No equipment context**: if your settings haven't changed across the report window, the equipment section is essentially "you've been on this configuration the whole time." That's fine — it documents stability.
