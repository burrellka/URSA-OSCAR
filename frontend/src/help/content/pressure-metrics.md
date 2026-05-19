# Pressure metrics

URSA-OSCAR reports three pressure percentiles per night: median, p95, and p99.5. This page explains what each one tells you and what to do with them.

## How pressure is recorded

Every 2 seconds your CPAP records the current mask pressure into the `Press.2s` channel of PLD.edf. A typical 8-hour night is 14,400 samples. URSA-OSCAR computes the percentiles over all those samples.

## Median pressure

The midpoint of the distribution — half your night was at or below this pressure, half above.

For a **fixed-CPAP user**: median should equal your prescribed pressure setting almost exactly. If it doesn't, something is wrong (mask leak large enough that the machine compensates, or a settings issue).

For an **AutoSet user**: median is somewhere between your min and max pressure. It's where your machine "rests" — the pressure that holds the airway open with minimal intervention. A median equal to your min suggests your apneas are well-controlled and the machine rarely needs to titrate up. A median near your max means your machine is fighting hard most of the night.

## p95 pressure

The 95th percentile. Your machine ran at or below this pressure for 95% of the night. The other 5% (about 24 minutes of an 8-hour night) it ran higher.

p95 is the metric most providers focus on for AutoSet titration: it represents the pressure the machine reaches under real airway resistance, not just the baseline. If your p95 is consistently bumping against your max setting, your max is probably too low — your airway is asking for more pressure than your machine is permitted to deliver.

## p99.5 pressure

The 99.5th percentile. The machine ran at or below this for 99.5% of the night. Only ~2-3 minutes of an 8-hour night exceeded this. This is essentially the peak pressure the machine ran in response to a transient event.

A wide gap between p95 and p99.5 means there were sharp, brief pressure spikes — usually responses to a specific apnea event. A narrow gap means the high-pressure periods were sustained.

## The pressure window settings

Your machine has two prescribed bounds:

- `min_pressure_setting` — the floor. Machine won't go below this.
- `max_pressure_setting` — the ceiling. Machine won't go above this.

URSA-OSCAR's Daily View shows these as horizontal lines on the pressure chart so you can see at a glance how often the machine was running at the boundary.

## EPR — Expiratory Pressure Relief

ResMed AirSense devices can optionally reduce pressure on exhale to make CPAP more comfortable. The reduction is in increments of 1 cmH2O (EPR 1, 2, or 3).

When EPR is on, the inhalation pressure is what `Press.2s` records and the exhalation pressure is what `EprPress.2s` records. URSA-OSCAR tracks both:

- `median_pressure` / `p95_pressure` / `p995_pressure` — IPAP (inhale)
- `median_epap` / `p95_epap` / `p995_epap` — EPAP (exhale)

On a fixed-CPAP machine without EPR, the EPAP columns are null.

## BiPAP

If you're on a BiPAP machine (true bilevel), IPAP and EPAP are independently set. URSA-OSCAR treats this the same way as EPR — it tracks both pressures separately. The gap between p95 IPAP and p95 EPAP is your "pressure support" — how much extra push the device gives you on inhale.

## What patterns to look for

- **Median creeping up over weeks** — your airway is getting harder to keep open. Worth a provider conversation: are you gaining weight, is your nasal congestion worse, are there other changes?
- **p95 frequently bumping max** — your max is too low. Provider conversation to raise it.
- **Median = min and never moves** — you may be over-titrated. The machine isn't reaching for higher pressures because your apneas are gone, but you're tolerating more pressure than you need. Mention to provider.
- **Wide p95-vs-median gap** — your machine is dynamic, fighting your airway often. Consider whether positional therapy or weight change might reduce demand.

## Pressure in the analytical tools

The Trends page lets you plot pressure metrics over time, correlate them against other metrics (e.g., does p95 pressure correlate with leak?), and run predictive models. The Statistics page shows you the distribution as a histogram so you can see the shape of your pressure usage at a glance.
