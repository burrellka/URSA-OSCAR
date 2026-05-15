# Tool Reference

The eleven analytical tools URSA-OSCAR exposes to LLMs. Same tools are available via the MCP server (for Claude.ai connector) AND the in-app AI chat (any of 7 providers).

For each tool: what it does, when an LLM should call it, parameters, response envelope shape, and a worked example.

For the descriptor source (the strings the LLM actually reads) see `backend/src/ursa_oscar/ai_proxy/tools.py:TOOL_DESCRIPTORS`. The descriptions are the "use when the user asks…" patterns; LLM tool routing is driven by matching user intent against those strings.

---

## Envelope contract

Every tool returns either:

```json
{ "ok": true, "data": { ... tool-specific payload ... } }
```

or:

```json
{
  "ok": false,
  "code": "NOT_FOUND | INVALID_INPUT | NETWORK_ERROR | UPSTREAM_ERROR | INTERNAL_ERROR | UNKNOWN_TOOL",
  "error": "<short human-readable message>"
}
```

The LLM sees this envelope verbatim. Tools never raise out to the LLM; failures are envelope-shaped so the LLM can decide what to tell the user.

---

## Index

| Tool | What it does |
|---|---|
| [`get_nightly_summary`](#get_nightly_summary) | Single-night or range summary (AHI, pressure, leak, equipment) |
| [`get_ahi_breakdown`](#get_ahi_breakdown) | Per-event-type AHI decomposition + TECSA heuristic |
| [`list_available_nights`](#list_available_nights) | Dates the user has imported data for, with optional filter |
| [`compare_periods`](#compare_periods) | A/B comparison of metrics between two date ranges |
| [`analyze_correlation`](#analyze_correlation) | Pearson correlation between two metrics |
| [`get_trend`](#get_trend) | Linear-regression trend + projection |
| [`get_manual_log_summary`](#get_manual_log_summary) | Manual logs (medications, symptoms, alertness, etc.) |
| [`get_user_profile`](#get_user_profile) | Clinical profile (diagnoses, meds, goals) |
| [`get_event_distribution_by_hour`](#get_event_distribution_by_hour) | Hour-of-night histogram of respiratory events |
| [`get_pressure_profile`](#get_pressure_profile) | Pressure statistics (median, percentiles, EPAP) |
| [`get_leak_profile`](#get_leak_profile) | Leak statistics + seal-quality interpretation |

---

## get_nightly_summary

**Description (verbatim to LLM):** Return the nightly summary (AHI, pressure, leak, equipment settings) for one date or a date range. Use when the user asks 'how was last night', 'show me my CPAP data for X', 'compare last night to the night before'.

**Parameters:**
- `date` *(required)* — Start date in `YYYY-MM-DD`
- `end_date` *(optional)* — Inclusive end date. When set, returns a list of summaries

**Returns:**

Single date (no `end_date`): an object with the full `NightlySummary` shape:

```json
{
  "date": "2026-05-10",
  "session_count": 2,
  "start_time": "2026-05-10T22:30:00",
  "end_time": "2026-05-11T05:55:00",
  "total_time_minutes": 445,
  "total_ahi": 3.13,
  "obstructive_ahi": 0.45,
  "central_ahi": 2.34,
  "hypopnea_index": 0.27,
  "rera_index": 0.07,
  "median_pressure": 7.52,
  "p95_pressure": 11.20,
  "p995_pressure": 12.18,
  "median_epap": 6.40,
  "p95_epap": 9.72,
  "median_leak": 4.2,
  "p95_leak": 15.6,
  "minutes_in_apnea": 6,
  "minutes_over_leak_redline": 1.2,
  "large_leak_pct": 0.27,
  "machine_model": "AirSense 11 AutoSet",
  "mode": "AutoSet",
  "min_pressure_setting": 5.0,
  "max_pressure_setting": 15.0,
  "epr_level": 3,
  "ramp_time_minutes": 0,
  "humidity_level": "Auto",
  "mask_type": "Full Face"
}
```

Range (with `end_date`): an array of objects above.

**Routing:** `_route_nightly_summary` — calls `/api/v1/night/{date}` for single-date, `/api/v1/nights?start={date}&end={end_date}` for ranges.

**LLM uses it for:** "How was last night?", "Show me May 10", "Compare these three nights."

---

## get_ahi_breakdown

**Description:** Per-event-type AHI decomposition for a single night: central vs obstructive vs hypopnea vs RERA counts + per-hour indices + TECSA-likely heuristic. Use when the user asks 'what kind of apneas did I have', 'are these central or obstructive'.

**Parameters:**
- `date` *(required)* — `YYYY-MM-DD`

**Returns:**

```json
{
  "date": "2026-05-08",
  "total_ahi": 23.47,
  "central": { "count": 47, "index": 6.21 },
  "obstructive": { "count": 28, "index": 3.70 },
  "hypopnea": { "count": 1, "index": 0.13 },
  "apnea_unclassified": { "count": 2, "index": 0.26 },
  "rera": { "count": 7, "index": 0.92 },
  "interpretation": {
    "tecsa_likely": true,
    "tecsa_reason": "Central index 6.2/hr; 47/77 apneas are central"
  }
}
```

The `interpretation.tecsa_likely` flag fires when central index ≥ 5/hr AND > 50% of all apneas are central — a heuristic for treatment-emergent central sleep apnea. **Not a clinical diagnosis**; the LLM should frame this as "your data is consistent with TECSA" rather than "you have TECSA".

**Routing:** `_route_ahi_breakdown` — composes `/api/v1/night/{date}` + `/api/v1/events?date={date}`.

**LLM uses it for:** "What kind of apneas did I have?", "Is this TECSA?", "Are my events mostly central or obstructive?"

---

## list_available_nights

**Description:** List the dates the user has imported CPAP data for. Optional filter expression (e.g., 'AHI < 5'). Use when the user asks 'what nights do I have data for', 'find me good nights', 'show me my worst nights this month'.

**Parameters:**
- `filter_expression` *(optional)* — SQL-style filter on summary fields, e.g., `"AHI < 5"`, `"central_ahi > 3"`
- `start_date` *(optional)* — Lower bound
- `end_date` *(optional)* — Upper bound

**Returns:**

```json
{
  "nights": [
    { "date": "2026-05-07", "total_ahi": 2.84, "session_count": 3 },
    { "date": "2026-05-08", "total_ahi": 23.47, "session_count": 2 },
    ...
  ],
  "total": 10
}
```

**Routing:** Direct GET to `/api/v1/nights` with query params. Filter is parsed server-side; supports a subset of safe SQL expressions over the summary columns.

**LLM uses it for:** "What dates do I have?", "Find nights where my AHI was under 5", "Show me my best nights"

---

## compare_periods

**Description:** Compare metric means / medians / std between two date ranges (period A vs period B) with absolute + relative deltas and a direction-aware interpretation. Use for 'compare this week vs last week', 'how am I trending vs last month'.

**Parameters:**
- `period_a_start` *(required)* — `YYYY-MM-DD`
- `period_a_end` *(required)* — `YYYY-MM-DD`
- `period_b_start` *(required)*
- `period_b_end` *(required)*
- `metrics` *(optional)* — list of metric names. Defaults to a sensible set: `["total_ahi", "obstructive_ahi", "central_ahi", "median_pressure", "p95_leak", "total_time_minutes"]`

**Returns:**

```json
{
  "period_a": {"start": "2026-05-01", "end": "2026-05-07", "n_nights": 7},
  "period_b": {"start": "2026-04-24", "end": "2026-04-30", "n_nights": 7},
  "metrics": {
    "total_ahi": {
      "period_a": {"n": 7, "mean": 3.45, "median": 3.21, "std": 0.85, "min": 2.40, "max": 4.80},
      "period_b": {"n": 7, "mean": 5.12, "median": 5.30, "std": 1.20, "min": 3.10, "max": 7.00},
      "absolute_delta": -1.67,
      "relative_delta_pct": -32.6,
      "interpretation": "substantial_improvement"
    },
    ...
  },
  "summary": "..."
}
```

The `interpretation` field is one of: `substantial_improvement`, `moderate_improvement`, `no_meaningful_change`, `moderate_worsening`, `substantial_worsening`. Direction-aware — for AHI lower is better; for `total_time_minutes` higher is better.

**LLM uses it for:** "Compare this week vs last", "Am I doing better this month?"

---

## analyze_correlation

**Description:** Pearson correlation between two metrics over a date range, with optional time lag. Use when the user asks 'is X correlated with Y', 'does my AHI change when I take melatonin'. Returns r, p-value, n, and a plain-language interpretation. Surfaces a sample-size warning when n<30.

**Parameters:**
- `metric_a` *(required)* — Bare nightly_summary column (e.g., `"total_ahi"`) OR `"log_type:filter:field"` for manual logs (e.g., `"medication:melatonin:taken"`)
- `metric_b` *(required)* — Same shape
- `start_date` *(required)* — `YYYY-MM-DD`
- `end_date` *(required)*
- `lag_days` *(optional)* — Shift metric_b by N days before correlating. Default 0.

**Returns:**

```json
{
  "metric_a": "total_ahi",
  "metric_b": "medication:melatonin:taken",
  "date_range": {"start": "2026-04-01", "end": "2026-05-15"},
  "lag_days": 0,
  "n_pairs": 42,
  "pearson_r": -0.34,
  "p_value": 0.027,
  "interpretation": "weak_negative",
  "interpretation_text": "Weak negative correlation: when melatonin was taken, AHI tended to be lower. Effect is small but statistically significant at p<0.05.",
  "sample_size_warning": null
}
```

`interpretation` ∈ `{strong_positive, moderate_positive, weak_positive, no_correlation, weak_negative, moderate_negative, strong_negative, insufficient_data, zero_variance}`.

The `sample_size_warning` is non-null when n<30 — flags small-sample noise.

**Metric naming convention:**
- Bare column from `nightly_summary` → just the column name (`"total_ahi"`, `"median_pressure"`)
- Manual logs → `"<log_type>:<filter>:<field>"`
  - `"medication:melatonin:taken"` → 1 if user logged melatonin that night, 0 otherwise
  - `"alertness:morning:rating"` → numeric alertness rating bucketed to mornings
  - `"sleep_environment:any:noise_level"` → JSON-blob field extraction

**LLM uses it for:** "Does X correlate with Y?", "Does melatonin help my AHI?", "Is my AHI higher on stressful days?"

---

## get_trend

**Description:** Linear-regression trend of one metric over a date range with R², slope-per-day, projection, and improving/worsening label. Use for 'what's the trend', 'is my AHI getting better', 'project where I'll be in 30 days'.

**Parameters:**
- `metric` *(required)* — Same metric naming convention as `analyze_correlation`
- `start_date` *(required)*
- `end_date` *(required)*
- `projection_days` *(optional)* — Forward-projection horizon. Default 7.

**Returns:**

```json
{
  "metric": "total_ahi",
  "date_range": {"start": "2026-04-15", "end": "2026-05-15"},
  "n_nights": 31,
  "slope_per_day": -0.045,
  "intercept": 4.82,
  "r_squared": 0.18,
  "p_value": 0.022,
  "current_value_estimate": 3.42,
  "projection": {
    "projection_days": 7,
    "projection_date": "2026-05-22",
    "projected_value": 3.10
  },
  "interpretation": "improving",
  "interpretation_text": "Slight improvement: AHI down ~0.05/day. R² is low (0.18) so day-to-day variance dominates the trend — meaningful but not dramatic."
}
```

`interpretation` ∈ `{improving, slight_improvement, no_clear_trend, slight_worsening, worsening, insufficient_data}`. Direction-aware per the metric.

The R² + p_value provide statistical-confidence context. Low R² = noisy data; the trend exists but day-to-day variance is bigger than the trend itself.

**LLM uses it for:** "What's the trend?", "Am I getting better?", "Project my AHI for next week."

---

## get_manual_log_summary

**Description:** Aggregate the user's manual logs (medications, symptoms, alertness, sleep environment, notes) over a date range. Use for 'what did I take last week', 'show me my mood logs', 'how often have I logged anxiety'.

**Parameters:**
- `date` *(optional)* — Single-date shortcut
- `start_date` *(optional)*
- `end_date` *(optional)*
- `log_type` *(optional)* — One of `medication | symptom | alertness | sleep_environment | note`

**Returns:**

```json
{
  "date_range": {"start": "2026-05-01", "end": "2026-05-15"},
  "total_entries": 87,
  "by_type": {
    "medication": {
      "n": 42,
      "by_name": {
        "Doxepin": 14,
        "Melatonin": 14,
        "Vitamin D": 14
      }
    },
    "symptom": {
      "n": 12,
      "by_name": {
        "Anxiety": 5,
        "Headache": 4,
        "Fatigue": 3
      }
    },
    "alertness": {
      "n": 33,
      "mean_rating": 3.8,
      "by_time_of_day": {
        "morning": {"n": 11, "mean": 3.2},
        "midday": {"n": 11, "mean": 4.1},
        "evening": {"n": 11, "mean": 4.0}
      }
    },
    ...
  }
}
```

**LLM uses it for:** "What did I take last week?", "How often did I log fatigue?", "Show me my alertness trend."

---

## get_user_profile

**Description:** Return the user's clinical profile (diagnoses, active medications, treatment goals, allergies). Use ONCE at the start of a conversation if you need clinical context, OR when the user asks 'what's in my profile', 'what meds am I on'.

**Parameters:** none

**Returns:**

```json
{
  "version": 2,
  "last_updated": "2026-05-10T12:34:56",
  "display": { ... },
  "clinical": {
    "diagnoses": ["Obstructive Sleep Apnea", "Treatment-Emergent Central Apnea"],
    "medications": [
      {"name": "Doxepin", "dose_mg": 10, "active": true},
      {"name": "Melatonin", "dose_mg": 3, "active": true}
    ],
    "allergies": [],
    "treatment_goals": ["AHI < 5", "Improve sleep quality"],
    "notes": "Started CPAP April 2026..."
  },
  "personalization": { ... }
}
```

**Note:** the AI proxy's *system prompt* already includes a summary of this (diagnoses + active meds + goals). The LLM should rarely need to call this tool — it has the context already. Use case is when the user explicitly asks "what's in my profile" or needs the full details (allergies, doses).

**LLM uses it for:** "What's in my profile?", "What's my dose of X?", "What's my treatment goal?"

---

## get_event_distribution_by_hour

**Description:** Hour-of-night histogram of respiratory events for a single night. Use when the user asks 'when did my events happen', 'are they clustered early or late'.

**Parameters:**
- `date` *(required)*
- `event_types` *(optional)* — Filter list: `["ClearAirway", "Obstructive", "Hypopnea", "Apnea", "RERA", "LargeLeak"]`

**Returns:**

```json
{
  "date": "2026-05-08",
  "total_events": 78,
  "hours": [
    {"hour": 22, "counts": {"ClearAirway": 3, "Obstructive": 5}},
    {"hour": 23, "counts": {"ClearAirway": 8, "Obstructive": 2, "Hypopnea": 1}},
    {"hour": 0, "counts": {"ClearAirway": 12, "Obstructive": 4}},
    ...
  ]
}
```

The hour-of-day uses local clock time as recorded by the device (not the operator's wall-clock — see [DeviceClock](33-operator-setup-guide.md#q-my-sd-card-has-yyyy-mm-dd-dates-that-dont-look-like-real-times)).

**LLM uses it for:** "When did my events happen?", "Are they early or late?", "Was I worse in REM?"

---

## get_pressure_profile

**Description:** Pressure statistics + delivered-pressure distribution for a single night. Median / p95 / p99.5 pressure, EPAP companions, and a settings comparison. Use for 'what pressure did I actually run at', 'was my pressure too high'.

**Parameters:**
- `date` *(required)*

**Returns:**

```json
{
  "date": "2026-05-07",
  "median_pressure": 6.96,
  "p95_pressure": 11.20,
  "p995_pressure": 12.50,
  "median_epap": 5.20,
  "p95_epap": 9.30,
  "p995_epap": 10.60,
  "min_pressure_setting": 5.0,
  "max_pressure_setting": 15.0,
  "epr_level": 3
}
```

**LLM uses it for:** "What pressure did I run at?", "Did my pressure hit the max?", "How much EPR was applied?"

---

## get_leak_profile

**Description:** Leak statistics for a single night: median + p95 + p99.5 leak, minutes-over-redline, large-leak %, seal-quality label. Use for 'did my mask leak last night', 'how's my seal'.

**Parameters:**
- `date` *(required)*

**Returns:**

```json
{
  "date": "2026-05-10",
  "median_leak": 4.20,
  "p95_leak": 15.60,
  "p995_leak": 22.40,
  "minutes_over_redline": 1.2,
  "large_leak_pct": 0.27,
  "mask_type": "Full Face",
  "interpretation": {
    "seal_quality": "good",
    "summary": "Seal good; 0.27% of recording over the leak redline"
  }
}
```

`seal_quality` ∈ `{good, marginal, poor}` based on `large_leak_pct`:
- `< 1%` → good
- `< 5%` → marginal
- `>= 5%` → poor

**LLM uses it for:** "How was my seal?", "Did the mask leak?", "Did I need to readjust last night?"
