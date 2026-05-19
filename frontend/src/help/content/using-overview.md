# The Overview page

The landing page when you sign in. Designed for a 5-second answer to "how am I doing?"

## Calendar heatmap

The main visual element. One cell per night that has data. Color follows the night's total AHI:

- Green: AHI < 5 (well controlled)
- Yellow: 5 ≤ AHI ≤ 15 (moderate)
- Red: AHI > 15 (poorly controlled)
- Empty (no cell): no therapy session that night

Hover a cell to see the night's exact AHI, mask-on time, and date. Click to jump straight to the Daily View for that night.

## Why no-therapy nights are absent

Architect decision: the calendar shows only nights with actual data. A night you didn't use the CPAP doesn't appear at all.

The motivation: long stretches of operator non-use (travel, illness, time off therapy) shouldn't display as "missing data" gaps that look like an import bug. They're operator choices and the UI should reflect that. The skip count is still visible on the Statistics page as part of the usage breakdown.

If you want to see all dates including skips, the **Statistics page** is where the explicit usage breakdown lives.

## Headline stat tiles

Below the heatmap, a row of stat tiles surfaces rolling values:

- Last 7-night AHI mean — what's happening right now
- Last 30-night AHI mean — your medium-term trend baseline
- p95 pressure over the same windows
- Usage rate — how many nights in the window had therapy

These are the same numbers the Statistics page computes. The tiles are an "at a glance" preview; click any tile to dive into the full distribution on Statistics.

## What the Overview is for

- Quick check after a clinical visit: are my numbers stable?
- Spotting a "recent bad week" before it shows up as a 30-day trend
- Confirming the last import landed (the most recent cell is today's date or yesterday's, depending on when you used the machine)

## What the Overview is NOT for

- Detailed event analysis — that's the Daily View
- Trends over time — that's the Trends page
- Provider conversations — generate a Reports PDF instead
- Configuration — that's Settings

## Keyboard shortcut

Press `g` then `o` to jump to Overview from anywhere. (Coming in a future patch — listed here so the Help search picks it up when it ships.)

## Mobile note

The Overview is the page that works best on a narrow viewport. Stat tiles stack vertically; the heatmap scrolls horizontally if needed. If you're checking your CPAP data from a phone before bed, this is the page to bookmark.
