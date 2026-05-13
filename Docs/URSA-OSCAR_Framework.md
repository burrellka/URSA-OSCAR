# URSA-OSCAR — Self-Hosted Sleep Therapy Analytics Platform
## Framework Document for Architect Chat

**Version:** 1.0
**Date:** May 11, 2026
**Prepared for:** Architect chat in URSA project
**Status:** Initial framework, requires architect refinement against Fitbit App and Apex codebases

---

## Purpose

Build a self-hosted, headless+UI replacement for OSCAR (Open Source CPAP Analysis Reporter) that integrates natively with the URSA project's MCP ecosystem. The system replaces OSCAR's desktop-only Windows/Mac/Linux application with a modern homelab-deployed service that exposes:

1. CPAP data ingestion from ResMed AirSense 11 SD card exports
2. Subjective sleep/medication/symptom logging (manual entry)
3. MCP tool surface for URSA agent queries
4. Web UI matching OSCAR's analytical depth with modern UX
5. Long-term longitudinal database for cross-night, cross-metric analysis

The system is referred to as **URSA-OSCAR** to distinguish from the original OSCAR project while signaling lineage and intent.

---

## Context: Why Replace OSCAR

OSCAR is the gold standard for CPAP analysis but has architectural limitations for the URSA use case:

- **Desktop-only.** Cannot be queried by an MCP server or accessed from multiple devices.
- **Manual SD card workflow.** No automation, no scheduled imports.
- **No subjective data layer.** Can't track medications, mood, daytime alertness alongside CPAP metrics.
- **No native correlation with other health data.** Fitbit, Apex, and OSCAR data live in separate silos.
- **No agent integration.** URSA can't directly query the data without manual export/copy.

URSA-OSCAR addresses all five.

---

## Reference Architecture (Pattern Match to Existing Systems)

The user has two existing systems the architect chat should analyze before finalizing design:

1. **Fitbit App / fitbitkb MCP server** — provides the pattern for: SQLite-cached health data, MCP tool surface, natural language query layer, schema design for time-series health metrics.

2. **Apex** — provides the pattern for: homelab service deployment, containerization approach, networking patterns, web UI conventions.

URSA-OSCAR should match the architectural style of these systems for operational consistency. The architect chat must read both codebases before finalizing the URSA-OSCAR design.

---

## High-Level System Architecture

Recommended stack (architect chat to validate against Apex/fitbitkb patterns):

```
┌─────────────────────────────────────────────────────────────────┐
│                    URSA-OSCAR Platform                          │
├─────────────────────────────────────────────────────────────────┤
│  Web UI (React/Vue/Svelte — match Apex pattern)                 │
│  ├── Daily View (OSCAR-equivalent)                              │
│  ├── Overview / Statistics                                      │
│  ├── Events View                                                │
│  ├── Correlation / Trends                                       │
│  ├── Manual Logging (medications, symptoms, mood)               │
│  └── Import / Export controls                                   │
├─────────────────────────────────────────────────────────────────┤
│  REST API (FastAPI / Express — match existing stack)            │
├─────────────────────────────────────────────────────────────────┤
│  MCP Server (mirrors fitbitkb tool surface pattern)             │
├─────────────────────────────────────────────────────────────────┤
│  Core Services                                                  │
│  ├── Ingestion Service (SD card / folder watch / file import)   │
│  ├── EDF Parser (pyedflib or equivalent)                        │
│  ├── Analytics Engine (event detection, metrics calculation)    │
│  ├── Manual Log Service                                         │
│  └── Export Service                                             │
├─────────────────────────────────────────────────────────────────┤
│  SQLite (primary store — match fitbitkb pattern)                │
│  ├── nightly_summary                                            │
│  ├── nightly_events                                             │
│  ├── pressure_timeseries                                        │
│  ├── flow_timeseries                                            │
│  ├── manual_logs                                                │
│  └── correlations_cache                                         │
├─────────────────────────────────────────────────────────────────┤
│  Docker Compose Deployment                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Core Tables

```sql
-- Nightly summary (one row per night)
CREATE TABLE nightly_summary (
    date TEXT PRIMARY KEY,                     -- YYYY-MM-DD
    session_count INTEGER,
    start_time TEXT,                            -- ISO 8601
    end_time TEXT,
    total_time_minutes INTEGER,
    total_ahi REAL,
    obstructive_ahi REAL,
    central_ahi REAL,
    hypopnea_index REAL,
    rera_index REAL,
    median_pressure REAL,
    p95_pressure REAL,
    p995_pressure REAL,
    median_epap REAL,
    p95_epap REAL,
    p995_epap REAL,
    median_leak REAL,
    p95_leak REAL,
    p995_leak REAL,
    minutes_in_apnea INTEGER,
    minutes_over_leak_redline REAL,
    cheyne_stokes_pct REAL,
    large_leak_pct REAL,
    machine_model TEXT,
    mode TEXT,
    min_pressure_setting REAL,
    max_pressure_setting REAL,
    epr_level INTEGER,
    ramp_time_minutes INTEGER,
    humidity_level TEXT,
    mask_type TEXT,
    last_updated TIMESTAMP
);

-- Individual respiratory events
CREATE TABLE nightly_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    timestamp TEXT,                             -- ISO 8601 with subsecond if available
    session_id INTEGER,
    event_type TEXT,                            -- ClearAirway, Obstructive, Hypopnea, RERA, LargeLeak, etc.
    duration_seconds REAL,
    pressure_at_event REAL,
    epap_at_event REAL,
    flow_at_event REAL,
    leak_at_event REAL,
    FOREIGN KEY (date) REFERENCES nightly_summary(date)
);

-- High-resolution pressure time series
CREATE TABLE pressure_timeseries (
    date TEXT,
    timestamp TEXT,
    pressure REAL,
    epap REAL,
    PRIMARY KEY (date, timestamp),
    FOREIGN KEY (date) REFERENCES nightly_summary(date)
);

-- High-resolution flow waveform (very large — consider separate storage strategy)
CREATE TABLE flow_timeseries (
    date TEXT,
    timestamp TEXT,
    flow_rate REAL,
    PRIMARY KEY (date, timestamp),
    FOREIGN KEY (date) REFERENCES nightly_summary(date)
);

-- Leak rate time series
CREATE TABLE leak_timeseries (
    date TEXT,
    timestamp TEXT,
    leak_rate REAL,
    PRIMARY KEY (date, timestamp),
    FOREIGN KEY (date) REFERENCES nightly_summary(date)
);

-- Manual subjective logs
CREATE TABLE manual_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,                                  -- the night this entry is associated with
    log_type TEXT,                              -- 'medication', 'symptom', 'mood', 'alertness', 'note'
    timestamp TEXT,                             -- when the event/observation occurred
    value_text TEXT,                            -- e.g., "doxepin 3mg", "alert 8/10"
    value_numeric REAL,                         -- normalized numeric value when applicable
    unit TEXT,                                  -- "mg", "score", etc.
    category TEXT,                              -- 'sleep_aid', 'wakefulness_agent', 'energy', etc.
    notes TEXT,
    last_updated TIMESTAMP
);

-- Configuration / settings
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT,
    last_updated TIMESTAMP
);

-- Import history (audit trail)
CREATE TABLE import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_timestamp TIMESTAMP,
    source_path TEXT,
    nights_imported INTEGER,
    earliest_date TEXT,
    latest_date TEXT,
    status TEXT,
    error_message TEXT
);
```

### Manual Log Schema Examples

The manual_logs table is intentionally flexible. Example entries:

```
{
  "date": "2026-05-10",
  "log_type": "medication",
  "timestamp": "2026-05-10T22:00:00",
  "value_text": "doxepin 3mg",
  "value_numeric": 3,
  "unit": "mg",
  "category": "sleep_aid"
}

{
  "date": "2026-05-10",
  "log_type": "alertness",
  "timestamp": "2026-05-11T10:00:00",
  "value_text": "alert 8/10",
  "value_numeric": 8,
  "unit": "score_1_to_10",
  "category": "subjective"
}

{
  "date": "2026-05-10",
  "log_type": "note",
  "timestamp": "2026-05-11T07:30:00",
  "value_text": "Woke at 4:30am, got up briefly, fell back to sleep till 6am",
  "category": "wake_pattern"
}
```

---

## Ingestion

### SD Card Import

ResMed AirSense 11 SD card structure:

```
SD Card Root/
├── DATALOG/
│   ├── 20260507/                    (YYYYMMDD directories)
│   │   ├── *.edf                    (European Data Format files)
│   │   ├── *.crc                    (integrity check files)
│   │   └── ...
│   └── ...
├── STR.edf                          (summary file across all nights)
├── IDENTIFICATION.TGT
└── SETTINGS/
```

EDF parsing requirements:
- Use `pyedflib` (Python) or `edfjs` (Node) — architect chat to confirm based on stack choice
- Parse signals: Pressure, EPAP, Flow Rate, Leak, Snore, FlowLimit, Mask events
- Event detection should mirror OSCAR's algorithm:
  - **Obstructive Apnea:** >90% flow reduction lasting ≥10 seconds with continued respiratory effort
  - **Central Apnea (ClearAirway):** >90% flow reduction lasting ≥10 seconds with NO respiratory effort detected
  - **Hypopnea:** 30-90% flow reduction lasting ≥10 seconds
  - **RERA:** Respiratory Effort-Related Arousal — flow limitation pattern preceding arousal
  - **Large Leak:** Sustained leak rate >24 L/min for >10 seconds

### Import Sources (UI Capability)

The UI must support:

1. **Folder Picker Import**
   - Browser file API or Electron-style native picker
   - User points at SD card mount path or local folder containing DATALOG structure
   - System scans, deduplicates against existing data, imports new nights only
   - Progress bar with per-night status

2. **Mapped/Watched Folder Auto-Import**
   - Configured path (e.g., `/mnt/cpap-sd-card/`) checked on schedule (every 30 min)
   - Auto-imports new data without user intervention
   - Notifies via UI banner or webhook on completion

3. **Single-File Import (CSV/EDF)**
   - For testing or recovering from corrupt SD card
   - Drag-and-drop or file picker

### Deduplication

- Primary key: `nightly_summary.date`
- On re-import of existing date: overwrite with newer data (handles partial-night situations)
- Maintain `import_log` for audit
- Never delete event records — re-import truncates and rebuilds events for that date

---

## MCP Tool Surface

Pattern: mirror fitbitkb's conventions (snake_case, descriptive verb prefixes, return rich structured data).

### Tier 1: Essential Tools

```yaml
get_nightly_summary:
  description: "Returns the full nightly summary for a given date or range — AHI breakdown, pressure stats, leak metrics, equipment settings."
  parameters:
    date: string (required, YYYY-MM-DD)
    end_date: string (optional, for range)
  returns: nightly_summary record(s) plus interpretation block

get_ahi_breakdown:
  description: "Returns AHI broken down by event type. Critical for distinguishing CPAP efficacy (obstructive) from TECSA/adaptation (central)."
  parameters:
    date: string (required)
    end_date: string (optional)
  returns: |
    {
      "total_ahi": float,
      "obstructive": {"count": int, "ahi": float, "pct_of_total": float},
      "central": {"count": int, "ahi": float, "pct_of_total": float},
      "hypopnea": {"count": int, "ahi": float, "pct_of_total": float},
      "rera": {"count": int, "rdi_contribution": float},
      "interpretation": {
        "obstructive_treatment_status": "well_controlled" | "partial_control" | "inadequate_control",
        "central_apnea_concern": "none" | "mild" | "elevated" | "significant",
        "tecsa_likely": bool,
        "notes": [string]
      }
    }

get_event_distribution_by_hour:
  description: "Returns events grouped by hour of night. Reveals time-of-night patterns (e.g., 2-4 AM central apnea clusters)."
  parameters:
    date: string (required, single date — clusters don't aggregate meaningfully across nights)
    event_types: array (optional, default all)
  returns: {hour: int, events_by_type: {type: count}}

get_pressure_profile:
  description: "Returns median, 95%, 99.5% pressures and whether the machine hit its ceiling."
  parameters:
    date: string (required)
    end_date: string (optional)
  returns: pressure stats plus ceiling_hit boolean and recommendation

get_leak_profile:
  description: "Returns leak statistics including time-over-redline. Identifies mask seal issues vs. mouth opening."
  parameters:
    date: string (required)
    end_date: string (optional)
  returns: leak stats plus interpretation (large leak %, time over redline)

get_session_breakdown:
  description: "Returns per-session details for nights with multiple mask-on periods (e.g., mask removed and put back on)."
  parameters:
    date: string (required)
  returns: array of session records with start/end/duration/AHI per session

list_available_nights:
  description: "Lists all dates with available CPAP data, optionally filtered by criteria (e.g., 'AHI < 5')."
  parameters:
    start_date: string (optional)
    end_date: string (optional)
    filter_expression: string (optional, simple expression)
  returns: array of dates with summary stats
```

### Tier 2: Analytical Tools

```yaml
compare_periods:
  description: "Compares CPAP metrics between two date ranges. Used for assessing protocol changes (e.g., 'before vs after pressure adjustment')."
  parameters:
    period_1_start: string (required)
    period_1_end: string (required)
    period_2_start: string (required)
    period_2_end: string (required)
    metrics: array (optional, default all)
  returns: side-by-side comparison with delta and significance assessment

analyze_correlation:
  description: "Pearson correlation between CPAP metrics, manual log metrics, or cross-source metrics (Fitbit, Apex)."
  parameters:
    metric_a: string (e.g., 'central_ahi', 'sleep_score', 'manual.alertness')
    metric_b: string
    start_date: string
    end_date: string
  returns: correlation coefficient, p-value, sample size, interpretation

get_trend:
  description: "Returns trend analysis (linear regression slope, R²) for a metric over a date range."
  parameters:
    metric: string
    start_date: string
    end_date: string
  returns: slope, R², direction, significance

get_manual_log_summary:
  description: "Returns summary of manual log entries within a date range, optionally filtered by type/category."
  parameters:
    start_date: string
    end_date: string
    log_type: string (optional)
    category: string (optional)
  returns: aggregated log data

correlate_with_external:
  description: "Cross-source correlation between CPAP and Fitbit/Apex data. Requires fitbitkb MCP to be reachable."
  parameters:
    cpap_metric: string
    external_metric: string
    external_source: 'fitbit' | 'apex'
    start_date: string
    end_date: string
  returns: cross-source correlation analysis
```

### Tier 3: Operational Tools

```yaml
inspect_schema:
  description: "Returns database schema (matches fitbitkb pattern)."
  returns: table definitions

run_sql_query:
  description: "Read-only SQL query against the local database. Escape hatch for novel analyses."
  parameters:
    query: string (must be SELECT-only, parser-enforced)
  returns: query results

trigger_import:
  description: "Initiates import from configured SD card path or specified folder."
  parameters:
    source_path: string (optional, uses configured default)
  returns: import job ID and status

get_import_status:
  description: "Returns status of a running or completed import."
  parameters:
    job_id: string
  returns: status, progress, errors

add_manual_log:
  description: "Adds a subjective log entry (medication, symptom, alertness, etc.)."
  parameters:
    date: string
    log_type: string
    value_text: string
    value_numeric: number (optional)
    unit: string (optional)
    category: string (optional)
    notes: string (optional)
  returns: created log entry

export_data:
  description: "Exports data in CSV, JSON, or OSCAR-compatible format."
  parameters:
    date: string (optional)
    end_date: string (optional)
    format: 'csv' | 'json' | 'oscar_compat'
    download_path: string (optional, defaults to user output)
  returns: file path or download URL
```

---

## Web UI Requirements

Pattern: Match Apex's web UI conventions (architect chat to verify). The UI must replicate or improve upon OSCAR's primary screens.

### Required Screens

#### 1. Daily View (primary screen, mirrors OSCAR's Daily tab)

Layout:
- **Header bar:** Date picker (with prev/next arrows), AHI summary card, session count
- **Left sidebar:** Event flags column (CSR, LL, CA, OA, UA, H, RE) with counts and percentages
- **Main panel:** Stacked time-series charts
  - **Event Flags** (rug plot showing each event on timeline)
  - **Flow Rate** (waveform, ±100 L/min, with event markers)
  - **Pressure** (line chart showing Pressure and EPAP separately, with min/max settings as reference lines)
  - **Leak Rate** (line chart with redline at 24 L/min threshold)
  - **Flow Limit** (line chart)
  - **Tidal Volume** (optional, often useful)
  - **Minute Ventilation** (optional)
  - **Respiratory Rate** (optional)
  - **Snore** (optional)
- **Right sidebar:** Statistics panel
  - Channel | Min | Med | 95% | 99.5% (for pressure, EPAP, minute vent, respiratory rate, flow limit, leak rate, snore, inspiration time, expiration time, tidal volume)
  - Total time in apnea
  - Time over leak redline
- **Footer:** Device settings panel (Min/Max pressure, EPR, ramp time, mask type, humidity)
- **Interaction:** Zoom by clicking and dragging on time axis. Pan with scroll. Tooltip on hover showing exact values.

#### 2. Overview Screen

Pattern: Calendar heatmap + sparklines + summary metrics.

- **30/60/90 day calendar grid** colored by AHI (green ≤5, yellow 5-15, red >15)
- **Sparkline metrics** at top showing trends:
  - AHI trend
  - Sleep duration trend (if integrated with Fitbit)
  - Central vs obstructive ratio over time
  - Pressure 95% trend
  - Leak time-over-redline trend
- **Click any day to navigate to Daily View**

#### 3. Statistics Screen

Pattern: Distribution and aggregate analysis.

- **Time range selector** (7d / 30d / 90d / custom)
- **Distribution histograms** for:
  - Nightly AHI
  - Pressure (95th percentile)
  - Leak rate
  - Total mask-on time
  - Sleep efficiency (if integrated)
- **Aggregate stats table:** mean, median, min, max, std dev for each metric
- **Box plots** by week or month to show progress over time

#### 4. Events Screen

Pattern: Filterable event log.

- **Filter controls:** Date range, event types, duration threshold
- **Sortable table:** Date | Time | Event Type | Duration | Pressure | Context
- **Detail view on click:** Shows surrounding flow waveform (±30 seconds) for individual event
- **Bulk export** to CSV

#### 5. Trends / Correlations Screen (URSA-specific addition)

Not in original OSCAR. Required for URSA project.

- **Metric pair selector:** Choose any two metrics (CPAP, manual log, or external if integrated)
- **Scatter plot** with regression line
- **Correlation coefficient and significance**
- **Time-shifted correlation** option (e.g., "does medication taken night N predict AHI on night N+1?")
- **Multi-variate correlation matrix** (heatmap)

#### 6. Manual Logging Screen (URSA-specific)

The "spreadsheet-like" interface for subjective data:

- **Daily logging form:**
  - Medications taken (dropdown autocomplete from previous entries, with dose field)
  - Bedtime
  - Wake time
  - Mid-night wake events (timestamps)
  - Subjective sleep quality (1-10)
  - Daytime alertness ratings at 10 AM, 2 PM, 4 PM (1-10)
  - Notes (free text)
- **Spreadsheet view:** Calendar columns × metric rows, editable inline
- **Quick-log shortcuts:** "Repeat yesterday's medications" button, common entries as one-click
- **Manual log timeline:** Visualizes when each medication/symptom was logged across days

#### 7. Settings / Configuration

- Device profile (machine, mask, prescribed pressures)
- Import source paths (SD card mount, watched folders)
- Import schedule
- MCP server configuration
- Database management (backup, restore, vacuum)
- User profile (medical context, age, etc. — for future contextual analysis)

### Export Capability

From any screen with data displayed:

- **Export selection:** Current view, current date, date range, all data
- **Format:** CSV (per-night summary, per-event detail, time-series), JSON, OSCAR-compatible format
- **Download:** Browser download to user's local machine
- **Programmatic:** Via MCP tool

---

## Deployment

Match Apex's Docker Compose pattern:

```yaml
version: '3.8'
services:
  ursa-oscar-api:
    build: ./backend
    ports: ["8080:8080"]
    volumes:
      - ./data:/data
      - /mnt/cpap-sd-card:/cpap-import:ro
    environment:
      - DATABASE_PATH=/data/ursa-oscar.db
      - IMPORT_WATCH_PATH=/cpap-import

  ursa-oscar-mcp:
    build: ./mcp-server
    ports: ["8081:8081"]
    volumes:
      - ./data:/data
    depends_on: [ursa-oscar-api]

  ursa-oscar-ui:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      - API_BASE_URL=http://ursa-oscar-api:8080

  ursa-oscar-watcher:
    build: ./watcher
    volumes:
      - ./data:/data
      - /mnt/cpap-sd-card:/cpap-import:ro
    depends_on: [ursa-oscar-api]
```

---

## Implementation Phases

### Phase 1: Core Ingestion + MCP (highest priority)
Goal: Replace the manual CSV-export workflow we've been using.

Deliverables:
- EDF parser working against AirSense 11 data
- SQLite database with schema above
- Folder picker import
- MCP server with Tier 1 tools (get_nightly_summary, get_ahi_breakdown, get_event_distribution_by_hour, get_pressure_profile, get_leak_profile, list_available_nights)
- Basic CLI for testing
- Docker Compose deployment

Acceptance criteria:
- Can import a folder of DATALOG data and produce nightly summaries matching OSCAR's output within 1% margin
- URSA agent can query the MCP and get back structured AHI breakdowns
- All Tier 1 tools functional

### Phase 2: Web UI — Daily View + Overview
Goal: Replace OSCAR's UI for primary analysis workflow.

Deliverables:
- Daily View screen with all time-series charts
- Overview calendar heatmap
- Statistics screen
- Basic export to CSV

### Phase 3: Manual Logging + Trends
Goal: Add the subjective data layer that's missing from OSCAR.

Deliverables:
- Manual logging UI (form + spreadsheet view)
- add_manual_log MCP tool
- Trends/Correlations screen
- analyze_correlation MCP tool
- get_manual_log_summary MCP tool

### Phase 4: Automation + External Integration
Goal: Fully automated pipeline.

Deliverables:
- Watched folder auto-import
- Scheduled import service
- Cross-source correlation with fitbitkb MCP (correlate_with_external tool)
- Webhook notifications

### Phase 5: Advanced Analytics
Goal: Beyond OSCAR feature parity.

Deliverables:
- Predictive analytics (which factors predict bad nights)
- Treatment protocol comparison (A/B test of settings changes)
- Automated insight generation
- Sleep medicine report generation for provider visits

---

## Key Architectural Decisions for Architect Chat to Resolve

1. **Backend language/framework.** Python (FastAPI) likely best for EDF parsing ecosystem (pyedflib, mne). Node possible but EDF libraries weaker. Match Apex stack if reasonable.

2. **Frontend framework.** Match Apex pattern. Charting library: Chart.js (simple), Plotly (rich), D3 (custom). OSCAR-equivalent charts likely need Plotly or custom D3.

3. **Storage strategy for high-resolution time series.** Flow data at 25 Hz × 8 hours = 720,000 points per night. Options:
   - Store in SQLite (works but bloats fast)
   - Store only summary statistics in SQLite, keep raw EDF files on disk for on-demand parsing
   - Use TimescaleDB or DuckDB extension for time-series optimization
   - Architect chat to evaluate against use case

4. **MCP server pattern.** Match fitbitkb's framework exactly so URSA agent doesn't need to learn a new pattern.

5. **Authentication.** Single-user homelab — likely none initially, or basic auth match Apex pattern.

6. **Backup strategy.** SQLite + daily snapshot to homelab backup target. Match existing pattern.

---

## Integration with URSA Agent

URSA agent (Claude) connects to URSA-OSCAR MCP server alongside fitbitkb MCP. Expected query patterns:

```
"How did last night's AHI compare to my 7-day average?"
→ get_nightly_summary(yesterday) + get_nightly_summary(last_7_days_avg)

"Show me the central apnea trend over the last 2 weeks."
→ get_trend(metric='central_ahi', start_date=-14, end_date=today)

"Was my AHI better on doxepin nights or non-doxepin nights?"
→ get_manual_log_summary(filter='doxepin') joined with get_nightly_summary on those dates

"Did my readiness score improve after I started CPAP?"
→ correlate_with_external(cpap_metric='ahi', external_metric='readiness', external_source='fitbit')

"What time of night do my centrals cluster?"
→ get_event_distribution_by_hour(date=yesterday, event_types=['ClearAirway'])
```

URSA agent must call `tool_search` with multiple keyword angles to surface the full tool catalog (same pattern as fitbitkb). Tool descriptions must be rich enough to surface via varied query patterns.

---

## Open Questions for Architect Chat

1. Should URSA-OSCAR write back to URSA project knowledge files automatically (e.g., update the project context document with new findings)?

2. Should manual logging accept voice input via mobile app, or web-only?

3. How should URSA-OSCAR handle the future addition of other CPAP machines (Philips, Fisher & Paykel)? Architecture should accommodate but not prematurely abstract.

4. Should the system include a "sleep coach" rules engine that generates daily recommendations based on prior night's data?

5. What's the right backup/recovery strategy for years of accumulated data?

6. Should there be a public-facing summary view (privacy-controlled) that could be shared with providers via URL?

---

## Quality Bar

This system needs to be production-grade because it informs real medical decisions. Specifically:

- **Data integrity:** Never lose data. Audit trail on every modification.
- **Reproducibility:** Same input EDF should produce identical analysis output every time.
- **Provider-ready output:** Reports generated from this should be acceptable to sleep medicine specialists if shared.
- **Privacy:** Local-only by default. No cloud transmission without explicit opt-in.
- **Observable:** Logging, health checks, error reporting match Apex pattern.

---

## Handoff Instructions for Architect Chat

1. **Read this entire document.**

2. **Analyze the Fitbit App / fitbitkb codebase.** Specifically:
   - MCP server implementation pattern
   - SQLite schema conventions
   - Tool description and parameter conventions
   - Caching/refresh patterns
   - Error handling patterns

3. **Analyze the Apex codebase.** Specifically:
   - Container orchestration pattern
   - Web UI framework choice and conventions
   - API patterns
   - Configuration management
   - Deployment workflow

4. **Produce a refined design document** that:
   - Inherits architectural patterns from Fitbit App and Apex where appropriate
   - Resolves the open questions above
   - Provides specific technology choices with justification
   - Includes test strategy
   - Defines acceptance criteria for each phase
   - Specifies exact file/folder structure for implementation
   - Identifies risks and mitigation strategies

5. **Pass the refined design to Claude Code** for implementation, with test/validation requirements baked in.

6. **Validate the completed system end-to-end** before passing back to URSA agent for production use.

---

## Document Maintenance

This document is the source of truth for URSA-OSCAR design. As the system evolves, update this document and version it. The URSA agent should be aware of any deviations from this design.
