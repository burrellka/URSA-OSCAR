# What's in a nightly summary

Every night URSA-OSCAR imports becomes one row in the `nightly_summary` table. That row is the source of truth for every aggregate, comparison, and trend the system computes. This page is the field-by-field reference.

## The night identifier

`date` (YYYY-MM-DD) — the DATALOG directory's date, NOT the timestamp of any individual session. ResMed devices group sessions that started after about noon into the *previous* day's DATALOG directory. A session starting at 2:55 AM on 2026-05-08 lives under `DATALOG/20260507/`. URSA-OSCAR follows ResMed's convention: the date is the "evening you started" date.

## Therapy timing

- `session_count` — how many distinct mask-on/mask-off blocks the device recorded
- `start_time` — earliest session start across the night (device-local time, naive)
- `end_time` — latest session end across the night
- `total_time_minutes` — sum of session durations (mask-on time)

## AHI family

The clinical event indices. All are events-per-hour over the night's mask-on time.

- `total_ahi` — overall Apnea-Hypopnea Index. AHI < 5 is "well controlled," 5-15 is "moderate," > 15 is "poorly controlled." Per AASM convention, RERAs are NOT included in AHI.
- `obstructive_ahi` — obstructive apneas only. The airway physically collapsed.
- `central_ahi` — central apneas only (ClearAirway in ResMed terminology). The respiratory drive paused. High central AHI on a CPAP user can indicate complex / mixed sleep apnea — worth raising with your provider.
- `hypopnea_index` — hypopneas per hour. Hypopneas are partial airway obstructions that don't fully close.
- `rera_index` — Respiratory Effort Related Arousals per hour. Sub-apnea events that still disturb sleep. Reported separately because they're conventionally counted in RDI, not AHI.

## Pressure family

In cmH2O. Computed from the `Press.2s` channel in PLD.edf (one sample every 2 seconds).

- `median_pressure` — 50th percentile across all pressure samples for the night
- `p95_pressure` — 95th percentile (what your machine reaches most of the night)
- `p995_pressure` — 99.5th percentile (the peak pressure the machine ran)

For an AutoSet user, median and p95 will differ — the device titrates up when it detects airway resistance. The gap between median and p95 tells you how often your machine is fighting your airway. For a fixed-CPAP user, all three should be nearly identical.

## EPAP family

If you're using a BiPAP or AutoSet with EPR (Expiratory Pressure Relief), URSA-OSCAR tracks the lower pressure separately.

- `median_epap`, `p95_epap`, `p995_epap` — same percentiles, from the EprPress.2s channel.

On a non-EPR/non-BiPAP machine these fields are null.

## Leak family

In L/min. Computed from the `Leak.2s` channel.

- `median_leak` — typical leak rate
- `p95_leak` — the leak rate during the worst 5% of the night
- `p995_leak` — peak leak (often a mask-shift event)
- `minutes_over_leak_redline` — how many minutes of the night the leak rate exceeded ResMed's "large leak" threshold (~24 L/min)
- `large_leak_pct` — the same quantity expressed as a percentage of total mask-on time

A `large_leak_pct` above 5% suggests mask fit problems worth investigating. URSA-OSCAR doesn't tell you what to do about it — that's a mask-shop conversation — but it tells you when to start the conversation.

## Sleep-disturbance metric

- `minutes_in_apnea` — sum of every obstructive + central + unclassified apnea duration. Different from AHI: AHI is events-per-hour, this is total minutes spent in apnea.

## Equipment context

The device-reported settings active that night. These come from the SETTINGS/CurrentSettings.json file and the per-night Identification.json:

- `machine_model` — e.g., "AirSense 11 AutoSet"
- `mode` — e.g., "AutoSet", "CPAP", "BiPAP"
- `min_pressure_setting`, `max_pressure_setting` — your pressure window in cmH2O
- `epr_level` — EPR (0-3) if enabled
- `ramp_time_minutes` — pressure ramp on session start
- `humidity_level` — humidifier setting (0-8)
- `mask_type` — Nasal / Pillows / Full Face
- `antibacterial_filter` — whether the device thinks an antibacterial filter is installed
- `temperature_enable`, `tube_temp` — heated tube settings

These let you correlate AHI changes against settings changes. If your AHI jumped on the night you went from EPR 3 to EPR 0, the trend page will show it.

## What's NOT in a nightly summary

A few things URSA-OSCAR computes but doesn't store at the night level:

- **Per-event detail** — events live in the `events` table (queryable via the Events page or `get_event_distribution_by_hour` MCP tool)
- **Per-session detail** — sessions live in the `sessions` table (queryable via the Daily View's session list or `get_session_breakdown` MCP tool)
- **Waveform data** — pressure, flow, leak samples live in time-series tables, fetched per-night via `/api/v1/timeseries/{date}`

This separation keeps the nightly_summary table small and fast to scan for aggregates while still letting you drill down to per-event or per-sample resolution when you need it.
