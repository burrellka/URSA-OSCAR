# Sessions vs nights

URSA-OSCAR distinguishes between **sessions** (continuous mask-on blocks) and **nights** (the device's DATALOG date). This page explains the distinction and why it matters.

## What a session is

A session is one continuous period where your CPAP detected the mask was on and pressurized. From the device's perspective, a session starts when you put on the mask (or press the start button) and ends when:

- You take off the mask (the device detects a sustained leak),
- You press the stop button,
- The device times out due to no breathing detected, or
- You unplug the device.

A typical night might be one session: you put on the mask at bedtime, you take it off when you wake up. Or it might be three sessions: you went to the bathroom at 2 AM, you woke up to check on a kid at 5 AM, and there's a brief "did the mask come off" at 6 AM before your full wake-up.

URSA-OSCAR shows the sessions list per night on the Daily View — one row per session with its start time, end time, duration, and pre-aggregated stats.

## What a night is

A night is one row in `nightly_summary`. Identified by `date`. Aggregated from all the non-excluded sessions that share that date.

The "night" date is the DATALOG directory's YYYYMMDD — ResMed's convention puts sessions starting after noon into the previous day's directory. So a session starting at 3 AM on May 8 lives under `DATALOG/20260507/` and contributes to URSA-OSCAR's "2026-05-07" night.

This is the "noon-split" convention. It matches OSCAR Desktop, myAir, and AirView. URSA-OSCAR doesn't try to be clever about it.

## Why the distinction matters

### For event timing

When the AI assistant or PDF report tells you "your AHI on 2026-05-13 was 4.2," that number is the total events from every session whose DATALOG date is 2026-05-13, divided by the total mask-on hours across those sessions. A single session with a high AHI can dominate the night-level number; a long quiet session and a short noisy one average together in ways that obscure what happened.

The Daily View's session table is where you go to see the per-session detail.

### For session exclusion

The session-exclusion toggle (Phase 4 Ticket 1) lets you mark individual sessions as "ignore for analytics." Why you'd do this:

- A session where you accidentally hit start without actually wearing the mask (high leak, no real therapy)
- A session that the device cut short due to a power blip
- A test session where you were adjusting the mask, not actually trying to sleep

Excluding a session removes its events and waveforms from the night's aggregate. The DuckDB row gets re-computed via `recompute_nightly_summary`. Excluded sessions are preserved in the raw data — you can toggle them back on later.

### For the analytics floor

Some analytical methods need a minimum number of *nights*, not sessions. The trend regression wants 5+ nights. The predictive model wants 30+. A night with 8 sessions still counts as one night for these purposes.

### For "no therapy session" nights

If a date's DATALOG directory exists but has no usable session files (operator put the mask on for 30 seconds and gave up, or the device created a directory but never recorded), URSA-OSCAR doesn't create a `nightly_summary` row for that date. The date is treated as "skipped" — invisible on the Overview heatmap, counted in the Statistics page's "Y skipped" tally, no detail page on the Daily View.

A date the operator didn't put the mask on at all also produces no row. Same outcome.

## Practical consequences

- **Comparing your AHI across nights**: the comparison is at the night level. If you have a night with one good 7-hour session and another night with three 30-minute sessions, those two nights have very different reliability — the multi-session night's AHI is more variable just from short-duration noise.
- **Mid-night events**: the EventRug timeline on the Daily View shows all events from all sessions for the night, stacked on a single 24-hour timeline. The session boundaries are visible as gaps.
- **Manual logs**: manual log entries are keyed by date (the night), not by session. "Took 5 mg melatonin at 9 PM" is recorded against the night that started after that time.

## The MCP tool surface

The MCP tool surface mirrors the distinction:

- `get_nightly_summary` returns one row per night
- `get_session_breakdown` returns per-session detail for a single night
- `get_event_distribution_by_hour` returns events with their session_id attached so the AI can reason about which session produced which events

If you're asking the AI "what happened around 3 AM last night?" — it'll usually call `get_session_breakdown` to find the right session, then drill into events from there.
