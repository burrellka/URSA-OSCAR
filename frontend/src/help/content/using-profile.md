# Profile

Your clinical context. Static (or slowly changing) information about you that informs how the rest of URSA-OSCAR — especially the AI assistant — interprets your data.

## What goes in the profile

The Profile page collects:

### Demographics

Optional: age, weight (with units), height, BMI (auto-computed from weight + height when both are present). URSA-OSCAR doesn't use these for clinical determinations; they're context for the AI when you ask things like "is my pressure within typical range for someone my size?"

### Diagnoses

Free-form list. Examples:

- Obstructive sleep apnea (severity if known: mild/moderate/severe, AHI at diagnosis)
- Central sleep apnea / mixed apnea
- Comorbidities (atrial fibrillation, type 2 diabetes, hypertension, etc.)
- Non-apnea sleep disorders if relevant (RLS, parasomnia)

These get passed to the AI assistant as part of the system-prompt context so the AI doesn't have to ask "do you have a diagnosis?" every conversation.

### Active medications

What you're taking that might affect sleep, breathing, or CPAP therapy. Examples:

- Sleep meds (melatonin, trazodone, zolpidem, mirtazapine)
- Pain meds with respiratory effect (opioids, gabapentin)
- Antidepressants
- Beta blockers, anti-hypertensives
- Anything new in the last 6 months

The medication list in Profile is the "background" set — what you take regularly. Manual logs are for "what I took on this specific night" — different surface, different purpose.

### Providers

Your sleep medicine provider's name (or practice), your PCP, your DME supplier. The AI uses this to say things like "this is worth bringing up with Dr. Smith at your next visit" instead of "you should talk to a doctor."

### Treatment goals

What "doing well" means to you. Specific numbers help:

- Target AHI (e.g., < 5)
- Target mask-on hours (e.g., 7+)
- Target subjective alertness (1-10 scale)
- Specific symptoms to track resolution of (morning headaches, daytime sleepiness)

The AI references these goals when interpreting trends: "your AHI is at 6.2, target is < 5" is more useful than "your AHI is at 6.2."

### Equipment

Your specific CPAP setup:

- Machine model (auto-populated from imported data)
- Mask type (nasal, pillows, full-face)
- Mask brand/model (P30i, F30i, AirFit N20, etc.)
- Humidifier setting preference
- Heated tube on/off
- EPR/pressure support preference

Some of this is in your nightly_summary already (machine model, mode, EPR level, mask type from the device's perspective). The Profile fields are for the operator's-eye-view distinctions the device doesn't track — "I switched from F30 to F30i but the device just says 'full face mask'."

## When to update the profile

- After a provider visit where something changed (diagnosis updated, new medication, new treatment goal)
- After equipment changes (new mask, new machine)
- After a meaningful weight change (10+ lbs)

You don't need to update it every day. The profile is "what's true now," not "what was true on each night."

## How the AI uses it

On the first message of each conversation, the AI calls `get_user_profile` and gets a structured summary. This becomes part of the system prompt context. So when you say "compare this to my baseline," the AI knows your target AHI is < 5; when you say "I had a headache this morning," the AI knows if "morning headache" is on your symptoms-to-track list.

You can ask the AI to use information not in your profile ("imagine I'm a side sleeper for this analysis") in any conversation. The profile is a default; you can override in the moment.

## Privacy

The profile is in DuckDB on your hardware. Same posture as your CPAP data: no cloud sync, no third-party access, available to the AI provider via outbound API calls only when you actively start a chat conversation (and only the relevant subset gets sent in the system prompt, not the whole file).

If you're uncomfortable with the AI provider seeing specific information (a particular diagnosis, a medication), leave it out of the profile and use chat carefully. URSA-OSCAR doesn't enforce a privacy policy on what you share with a third party — the choice is yours.

## What the profile is NOT

- **A medical record.** It's a personal context document, not an EMR substitute.
- **Authoritative.** If you list "AHI at diagnosis: 28," nobody is going to use that for billing purposes.
- **Audited.** You can put anything you want. URSA-OSCAR doesn't validate.
- **Required.** Every other URSA-OSCAR feature works with an empty profile. Filling it in just makes the AI conversations richer.
