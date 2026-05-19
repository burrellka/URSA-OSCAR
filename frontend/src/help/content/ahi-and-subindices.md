# AHI and its sub-indices

The Apnea-Hypopnea Index is the headline number on most CPAP review screens. URSA-OSCAR breaks it down into its components so you can see what's actually happening.

## What AHI is

AHI = (apneas + hypopneas) / mask-on hours.

The classic AASM thresholds:

| AHI | Clinical label |
|---|---|
| < 5 | Well controlled / normal |
| 5 – 15 | Mild residual sleep apnea |
| 15 – 30 | Moderate |
| > 30 | Severe |

These thresholds were developed for *untreated* AHI as a diagnostic. Once you're on CPAP therapy, what they mean is different: your machine is supposed to keep your AHI low. A treated AHI of 8 isn't "mild OSA" — it's "your CPAP isn't fully controlling your apneas, ask your provider about it."

## The components URSA-OSCAR tracks

The total AHI is the sum of three sub-indices:

- **Obstructive AHI** (`obstructive_ahi`) — your airway physically collapsed. The chest is still trying to breathe, the airway is closed.
- **Central AHI** (`central_ahi`) — your respiratory drive paused. No breathing effort at all.
- **Hypopnea Index** (`hypopnea_index`) — your airway partially closed. Air still flowing but reduced by 30%+ for at least 10 seconds.

These add up to total AHI. The fourth event class URSA-OSCAR tracks — **RERAs** (`rera_index`) — is reported separately because the AASM convention is to count RERAs in the **R**espiratory **D**isturbance **I**ndex (RDI) but not in AHI. RDI = AHI + RERA index.

## Why the breakdown matters

The same AHI can mean very different things:

- **All obstructive, no central**: probably the most common pattern on a CPAP user. Your machine's pressure is partially controlling the apneas but isn't quite high enough on some nights. Conversation with your provider: "should we raise the lower bound or change to a different mode?"
- **Mostly central**: complex / mixed sleep apnea. Central apneas often appear when an obstructive patient gets their airway opened — the body's CO2 control gets confused. May respond to ASV (adaptive servo-ventilation) rather than plain CPAP. Conversation: "my centrals are elevated; should we consider ASV?"
- **Mostly hypopnea, low frank apneas**: airway is partially obstructed but not fully closing. Often associated with sub-optimal pressure. Conversation: "should we titrate higher?"
- **Mostly RERA**: your AHI looks great but you wake up tired. RERAs disturb sleep without showing up in AHI. Conversation: "my AHI is 2 but I still feel unrefreshed; my RDI is 18."

URSA-OSCAR doesn't tell you which of these patterns applies to you — that's a clinical conversation. It surfaces the breakdown so you can recognize the pattern and bring data to your provider rather than vague impressions.

## How URSA-OSCAR detects events

The events come from your CPAP machine, not URSA-OSCAR. ResMed AirSense 10/11 devices write event annotations to the `EVE.edf` file. URSA-OSCAR parses those annotations and counts them; it doesn't run its own event-detection algorithm on the raw flow signal.

This matters because:

1. URSA-OSCAR's AHI matches what your device's clinical/care app reports (myAir, AirView). They use the same source data.
2. ResMed's event detector is a specific implementation. Different CPAP brands (Philips, Fisher & Paykel, etc.) use different detectors. AHI comparisons across brands aren't apples-to-apples.
3. URSA-OSCAR doesn't currently support non-ResMed devices. The EDF format is standard but each manufacturer's event annotation conventions differ.

## Comparing single-night vs rolling AHI

A single night's AHI is noisy. Your AHI can vary 2-5 points night-to-night just from natural variation (sleep stage distribution, body position, congestion, etc.). Decisions about your therapy should look at rolling averages over weeks, not single nights.

URSA-OSCAR surfaces this in two ways:

- **Trends page** — fits a linear regression with R² so you can see whether a "downward trend" is real or just noise. Low R² → the day-to-day variation dominates and the line is misleading.
- **Statistics page** — shows mean / median / std-dev so you can see how variable your nights are. A standard deviation of 3 means your "average AHI of 5" actually ranges roughly 2 to 8 night to night.

## When to escalate to your provider

URSA-OSCAR shows you the data. Your provider decides what to do. Patterns worth flagging in conversation:

- Treated AHI consistently above 5 for 2+ weeks
- Central AHI rising over time (especially on a stable pressure setting)
- New RERA elevation without AHI change
- Sudden shift in any sub-index that coincides with a settings change

A printout from Reports → Full Clinical Report is good prep for that conversation.
