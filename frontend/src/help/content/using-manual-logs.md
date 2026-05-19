# Manual logs

Subjective data — things URSA-OSCAR can't measure but you can report. This is how you make the data more than just "what the CPAP saw."

## Five categories

URSA-OSCAR partitions manual logs into five types. Each type has its own form fields and vocabulary.

### Medication

What you took, when, dose, and an optional note. Vocabulary autocomplete for medication names; new entries get added to the vocabulary the first time you use them so you don't have to retype "amitriptyline 25mg" every night.

Use cases:
- Track whether melatonin nights vs no-melatonin nights show different AHI
- Note when you started a new medication (e.g., an SSRI) so future analysis can find pre/post comparisons
- Record dose changes so you can correlate dose-response

### Symptom

Free-text or vocabulary-driven. What you noticed: morning headache, dry mouth, daytime sleepiness, sore throat, congestion. Some symptoms are nightly observations; some are next-morning ratings.

Use cases:
- Correlate "morning headache" against AHI to see if the headaches track airway events
- Find whether congested nights have worse leak or AHI
- Document side effects from a new medication

### Alertness

A 1-10 subjective scale. URSA-OSCAR shows it next to the previous night's AHI and mask-on time so you can see whether the subjective tracks the objective.

Use cases:
- Confirm therapy is working: as AHI improved, did alertness improve?
- Spot the mismatch: AHI looks great but alertness is low — RERA index check, or maybe sleep quality issues beyond apnea
- Track day-of-week patterns (lots of people have "Monday-morning alertness 4" vs "Saturday-morning alertness 8" patterns)

### Sleep environment

Bedroom factors: temperature (cool / room / warm), partner snoring (yes / no), light level, noise level, alcohol consumption, caffeine cutoff time, exercise that day.

Use cases:
- Test the hypothesis "I sleep worse when the room is warm" against the data
- Document confounders so when you see a bad night, you can check the environment
- Track lifestyle correlations (does evening alcohol predict elevated AHI?)

### Freeform

Anything else. Date-tagged free-text notes that don't fit a category. Reasonable uses: travel ("3-hour time zone change"), illness ("sinus infection started today"), equipment changes ("switched to F30 mask"), life events ("late dinner with wine").

The freeform notes aren't analyzed statistically (no vocabulary, no fields to correlate against), but they're visible in the Daily View timeline and in the Reports' Manual Log Summary section.

## How logs connect to nights

Manual logs are keyed by date. The "log a medication taken at 9 PM" entry on 2026-05-13 attaches to the night that started after that time — same convention as CPAP sessions (noon-split).

This means:
- A log made between bedtime and the next noon attaches to "tonight's night"
- A log made in the morning attaches to the night that just ended

The autocomplete vocabulary remembers what you've entered before. The first time you log "amitriptyline" it goes into the vocabulary; from then on it autocompletes as you type. Same for symptom names, sleep environment values, etc.

## Adding a log

Two paths:

1. **From the Manual Logs page**: form-based entry with full field validation, vocabulary autocomplete, and immediate display in the recent-logs table.
2. **From the AI chat panel**: ask the AI to log something. "Log that I took 5 mg melatonin at 9 PM" triggers the `create_manual_log` tool (if that capability is enabled in your AI provider config). The AI confirms the entry before committing it.

## Editing or deleting a log

The Manual Logs page lets you edit any entry (correcting a typo, fixing a dose) or delete one outright. Editing creates a `last_updated` timestamp; the original `created_at` is preserved for audit.

## Analyzing logs against CPAP data

The Trends page's correlation, multivariate, and lag-correlation sections all accept manual-log fields as either predictors or targets. So you can ask:

- "Does alertness correlate with AHI from the previous night?" → pairwise correlation (alertness, AHI)
- "Does alertness correlate with AHI even after controlling for mask-on hours?" → multivariate correlation
- "Does evening alcohol predict elevated AHI two nights later?" → lag correlation with a 2-day lag

URSA-OSCAR converts categorical manual log values (medication name, symptom name) to presence/absence indicators on the date — "did you take this thing today: yes or no?" — so they can enter the regression. Numeric values (dose, alertness score) enter directly.

## What manual logs are not for

- **Detailed sleep journaling.** URSA-OSCAR isn't trying to be Mahler-Sleep-Diary. Keep it brief and structured.
- **Therapy decisions.** Logs document what happened. Decisions about what to change come from clinical conversations.
- **Replacement for a sleep technician's questionnaire.** If your provider wants you to use a specific instrument (Epworth Sleepiness Scale, etc.), keep using that — URSA-OSCAR alongside, not instead of.

## Privacy posture

Manual logs sit in DuckDB on your hardware, alongside everything else. No cloud sync, no third-party access. If you log "took 50 mg of THC at 10 PM" or "had a fight with my partner before bed," that information stays on your server. URSA-OSCAR's threat model is "anyone with host file access to /data can read this" — same as for your CPAP data.
