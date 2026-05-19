# The Daily View

Per-night detail. The page you go to when something interesting happened and you want to see what.

URL: `/daily/YYYY-MM-DD`. Bookmark a specific night, share a URL with your provider (assuming your provider has access — see Architecture and Deployment for the access posture).

## The page layout

Top to bottom:

1. **Date navigator** — previous / next night buttons, plus a date picker. Skips over no-therapy nights so "next" always lands on the next night with data.
2. **Summary band** — the headline numbers for this night: AHI, mask-on time, pressure p95, large-leak %, central count, obstructive count, hypopnea count.
3. **EventRug** — a 24-hour timeline showing every event when it happened. Color-coded by type.
4. **Time-series charts** — pressure (IPAP/EPAP/median lines), flow rate, leak. All synchronized: hovering one chart shows a crosshair on all three.
5. **Sessions table** — one row per session with start/end/duration + per-session AHI + the toggle for excluding the session.
6. **AI chat panel** (if configured) — right-side panel you can open to ask about this night specifically.

## EventRug

The visual that makes single-night patterns obvious. Each event is a colored dot at its timestamp. The y-axis is just event type (apnea / hypopnea / leak), the x-axis is wall-clock time across the night.

What you'll see:

- **A cluster of events at sleep onset, then quiet** — common pattern; airway re-collapses as you fall back asleep, then your CPAP titrates up and stabilizes.
- **Periodic clusters every 60-90 minutes** — events at REM sleep transitions. Normal for OSA patients.
- **A burst of leak events around 4 AM** — your mask shifted during REM.
- **Continuous events across the night** — your CPAP isn't controlling well; provider conversation.

Click any dot to see the event detail (timestamp, duration, type, at-event pressure and leak readings).

## Time-series charts

Three stacked charts share an x-axis (time of night):

- **Pressure**: IPAP in blue, EPAP in purple (if EPR/BiPAP), median pressure as a horizontal reference. The min/max pressure settings are dashed lines so you can see when the machine bumped against the boundary.
- **Flow rate**: the breath-by-breath flow signal. Apneas show as flat stretches; hypopneas show as reduced-amplitude stretches.
- **Leak**: leak rate over the night. The redline (~24 L/min) is a horizontal dashed line. Anything above is in `minutes_over_leak_redline`.

You can zoom in on any time range by dragging on a chart. The zoom synchronizes across all three.

## Sessions table

One row per session for the night. Columns:

- Session start / end (device-local time)
- Duration in minutes
- Per-session AHI
- Per-session leak %
- Exclude toggle

The exclude toggle is what you use when a session is clearly bad (mask off most of the time, brief mis-fire) and you don't want it polluting the night's aggregate. Toggling triggers a `recompute_nightly_summary` on the backend; the page refreshes with the new aggregate.

Excluded sessions are preserved in raw data — you can toggle them back on later. The exclusion state is stored in DuckDB and persists across re-imports (the `excluded_sessions` table).

## AI chat panel

The right-side panel. If you've configured an AI provider (Settings → AI Assistant), the panel is available; otherwise it's hidden.

The chat is **scoped to this date** by default. The system prompt receives the current date as context, so when you ask "what happened?" or "explain this night," the AI knows you mean THIS night. It still has access to your full data via the MCP tools — you can ask "how does this compare to the last 7 nights?" and it'll go pull that.

Conversation history is stored in browser localStorage, keyed per-date. Each night's chat is its own conversation; switching dates doesn't carry context across. (Architectural choice — see Phase 5 Decision 5 in the build handovers.)

## Common Daily View workflows

- **A night was unusually bad.** Open the Daily View, look at the EventRug for clustering, check whether leak was elevated, examine the pressure chart for whether the machine was titrating high.
- **A night was unusually good after a stretch of bad ones.** Same investigation in reverse: what changed? Look at sessions, at pressure, at leak.
- **Provider visit prep.** Open the Daily View for the night(s) you want to discuss, click "Generate Report" from the page (when that feature lands) or open Reports manually to generate a PDF including this date.
- **Hypothesis testing.** "I think my AHI is worse when I sleep on my back" — the URSA-OSCAR data alone can't tell you that (no posture sensor), but the AI chat can help you formalize the question and identify what additional logging would test it.

## What's not on the Daily View

- **Trend lines** — Daily is single-night detail; trends are on the Trends page.
- **Multi-night comparison** — Statistics page or the AI chat ("compare last night to the night before").
- **Settings changes** — the equipment fields on the nightly_summary row are visible in the summary band, but the change history isn't displayed as a timeline. That's a future enhancement.
