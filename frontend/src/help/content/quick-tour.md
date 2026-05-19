# Quick tour of the UI

A 5-minute orientation. The sidebar lists every page in URSA-OSCAR in the order an operator typically uses them.

## Overview

The landing page. A calendar heatmap colored by nightly AHI: green for AHI < 5 (well-controlled), yellow for 5-15 (moderate), red for > 15 (poorly controlled). Click any cell to jump to that night's Daily View. Dates the operator didn't use the CPAP don't appear on the calendar — only nights with recorded data.

Headline stat tiles below the heatmap show recent rolling values (last 7-night AHI, etc.) and a "compliance" indicator that surfaces your usage rate.

## Daily View

Per-night detail. The URL is `/daily/YYYY-MM-DD`. The page has:

- A summary band with the night's key numbers
- An EventRug timeline showing every apnea/hypopnea/leak event when it happened during the night
- Time-series charts for pressure (IPAP / EPAP / median pressure), flow rate, and leak — with the events overlaid
- A sessions list (one CPAP "session" per mask-on continuous block) with toggle controls to exclude individual sessions from aggregates
- An AI chat panel on the right (if you've configured an AI provider) — ask questions about this specific night

Most operators visit Daily View when something interesting happened (a high-AHI night, a leak event) and want to see the underlying detail.

## Statistics

Aggregate view over a configurable window: 7 days, 30 days, 90 days, all. Shows:

- Usage breakdown: "X nights used / Y skipped / Z% usage" over the window
- An Aggregates table: mean, median, min, max, std-dev for every metric URSA-OSCAR tracks
- Histograms for the most-watched metrics (nightly AHI, p95 pressure, mask-on minutes, central AHI, obstructive AHI, large-leak %)

This is the page for "how am I doing over the last month?" questions.

## Events

Events list. Filterable by date range and event type (obstructive apnea, central apnea, hypopnea, RERA, large leak, etc.). Each row is one detected event with its timestamp, type, and duration. Click any row to jump to that point in the Daily View.

## Trends

Multi-section analytical surface. Includes:

- **Single-metric trend** — pick a metric, see a linear regression with R² + projection
- **Pairwise correlation** — pick two metrics, see a scatter plot with regression overlay
- **Multivariate correlation** — partial correlation controlling for other variables
- **Lag analysis** — does X today predict Y tomorrow?
- **Predictive modeling** — ridge regression with counterfactual analysis

Each section has a date-range picker and a metric selector. Defaults are tuned for the most common use cases (long-range trends, all-data correlations, recent-30-day predictions).

## Reports

PDF clinical report generator. Three templates:

- **Full clinical report** — comprehensive 8-12 page summary for sharing with your sleep medicine provider
- **Summary report** — 2-3 page highlights
- **Analytical report** — focused on the Phase 6 statistical analyses (correlations, predictions, trends)

Pick a template + date range, hit "Generate PDF." The page shows a preview of what's included before you commit; the actual PDF generation runs server-side and downloads when ready.

## Manual Logs

Subjective data — things URSA-OSCAR can't measure but you can report. Five categories:

- **Medication** — what you took, when, dose
- **Symptom** — what you noticed (morning headache, dry mouth, daytime sleepiness)
- **Alertness** — subjective alertness 1-10 scale
- **Sleep environment** — bedroom temperature, partner snoring, light level
- **Freeform** — anything else worth noting

Manual logs are joined with nightly summaries by date for analytics. You can correlate "morning headache" against AHI, or check whether your alertness tracks your AHI over weeks.

## Profile

Your clinical context. Diagnoses, medications, providers, treatment goals, equipment. Used by the AI assistant via `get_user_profile` to provide context-aware responses. Filling this in is what differentiates "AI guessing" from "AI informed."

## Settings

Configuration + operational tools:

- **Configuration** — read-only display of masked secrets and image versions
- **MCP Health Check** — verifies the MCP container is reachable and OAuth is configured
- **Data Management** — purge nights by date range, run a DuckDB CHECKPOINT
- **Account** — change your operator password, generate 90-day API tokens for external scripts
- **AI Assistant** — configure providers and the system prompt template

## Help

The page you're on. Substring search across all topics, organized by section. The AI assistant can also query these same topics via the `get_help_topic` MCP tool.

## Import / Export

- **Import** — three paths for getting SD-card data into URSA-OSCAR (folder upload, bind-mount drop, path-based)
- **Export** — OSCAR-shape CSVs (Summary / Sessions / Daily) and a bulk URSA-OSCAR-shape CSV for any date range

## The sidebar footer

Below the nav links you'll see your operator identity and a sign-out button. Clicking your name jumps to **Settings → Account**.
