# Leak metrics

Leak — the amount of air escaping past your mask seal — is the single most important variable for "is your CPAP actually treating you?" beyond pressure itself. A leaking mask means the prescribed pressure isn't reaching your airway, and the machine's event detection becomes less reliable because the flow signal is contaminated.

## How leak is measured

Your CPAP estimates leak rate as the difference between the air it pushed out and the air it expected to return. The estimate is updated continuously and stored in the `Leak.2s` channel — one sample every 2 seconds. URSA-OSCAR computes its leak statistics from this channel.

Units: liters per minute (L/min).

## The metrics URSA-OSCAR reports

### `median_leak`

The 50th percentile across the night. What your leak typically was. A healthy mask seal gives a median leak of 5-15 L/min — your machine is venting normally through the intentional exhalation port (yes, all masks vent on purpose; the vent prevents CO2 rebreathing).

### `p95_leak`

The 95th percentile. Your worst 5% of the night. This is where you'd see the effects of mask shifts during sleep, mouth breathing on a nasal mask, beard hair compromising the cushion, etc.

### `p995_leak`

The 99.5th percentile — basically the peak leak. Usually corresponds to a specific event (mask coming partially off, dramatic mouth opening, partner accidentally pulling the hose). A few minutes per night of high leak is normal; sustained high leak is a problem.

### `minutes_over_leak_redline`

Total minutes during which the leak rate exceeded ResMed's "large leak" threshold (~24 L/min). The machine's event detector becomes unreliable above this threshold — apneas detected during high-leak periods may not be real apneas.

### `large_leak_pct`

The same quantity expressed as a percentage of total mask-on time. The interpretation thresholds:

| `large_leak_pct` | Status |
|---|---|
| < 1% | Excellent mask seal |
| 1 – 5% | Normal; minor occasional shifts |
| 5 – 15% | Meaningful leak; mask fit problem worth investigating |
| > 15% | Significant leak; your therapy is partially compromised |

## What "large leak" actually means

ResMed's definition of large leak is a specific number (~24 L/min above the expected vent flow). The threshold is calibrated to be the point at which the device's flow-based apnea detection becomes unreliable. Below the threshold the device trusts its event detection; above it, the device flags apneas it detected but signals lower confidence.

This is why URSA-OSCAR surfaces `minutes_over_leak_redline` specifically: it's the duration during which your AHI for that night should be interpreted with caution.

## Common leak patterns

- **Sudden mid-night spike** — mask shifted. Look at the Daily View pressure chart; you'll often see the leak spike correlate with a posture change visible in the pressure response.
- **Gradual rise toward morning** — your mask straps loosened as the night progressed, or your face's geometry changed (some people's faces "swell" slightly during sleep).
- **Spike at sleep onset, then stable** — the mask wasn't quite seated when you put it on. Reseat technique.
- **Consistently elevated median (20-30 L/min)** — mouth breathing on a nasal mask. Either switch to full-face or try a chin strap, depending on what your provider recommends.
- **Brief spike at session start/end** — your CPAP turning on/off, not really a mask issue. Already filtered out of `minutes_over_leak_redline`.

## When leak invalidates a night

If `large_leak_pct` is > 15% for a night, your AHI for that night is suspect — the device's detector ran in degraded confidence for too much of the night. Don't draw therapy conclusions from a high-leak night.

URSA-OSCAR doesn't automatically exclude high-leak nights from analytics. That's an operator decision: if you have a night where your mask was actively coming off and you'd rather not include it in your trend, use the Daily View's session-exclusion toggle to drop the affected session(s).

## Where leak surfaces in URSA-OSCAR

- **Overview heatmap**: AHI color, not leak directly. Severe leak doesn't change the color, but you can hover for the tooltip.
- **Daily View**: dedicated leak time-series chart + the large-leak events marked on the EventRug.
- **Statistics**: large-leak % histogram + the leak metrics in the Aggregates table.
- **Trends**: leak metrics available as both correlation targets and predictors.
- **Reports**: full clinical report has a leak section with the patterns and threshold breakdown.

## What URSA-OSCAR doesn't do about leak

URSA-OSCAR doesn't recommend mask changes, mask sizes, or accessories. That's a mask-shop conversation: the DME (durable medical equipment) supplier or sleep technician who provided your mask is who fits and adjusts. URSA-OSCAR tells you when to start that conversation.
