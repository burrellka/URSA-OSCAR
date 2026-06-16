# Handover from URSA-OSCAR

For the developer modernizing Kevin's Fitbit / Google Health codebase. This document is the bridge: it explains what URSA-OSCAR is, what's worth lifting from it, what isn't, and how to think about the port.

**The mandate, in one sentence:** keep the existing Fitbit codebase's underlying logic (database, services, cache, MCP server, calculation logic) intact, but replace its UI shell with URSA-OSCAR's look-and-feel — including the four-container Docker architecture, the React/uPlot charting style, the in-app help system, the AI assistant, the profile system, and the segmented navigation pattern that turns "one long report" into a multi-page application with a left-rail nav.

> **Architect note (2026-06-08) — read this before you scope the frontend.** "Replace the UI shell" undersells the work, and you'll mis-plan if you read it as a port. The old fitbitkb has *no reusable shell*: it is a single container running three supervisord processes, and its UI is a **328 KB monolithic Dash app** (`src/app.py`). There is nothing to lift from it visually. The React/uPlot shell comes entirely from URSA; the old app contributes **only the engine** — data fetch, cache, SQLite, the Reality/Proxy sleep scoring, and the MCP tool surface. Scope the frontend as a **net-new build against URSA's patterns**, not a migration of Dash. This is the single largest chunk of work in the project. Also note the API client itself is a real rewrite (Fitbit Web API → Google Health API v4 — consolidated endpoints, new OAuth, new response shapes), not a base-URL swap.

---

## Before you write any code

Read these in order. Total ~90 minutes of reading; it will save you weeks.

| Order | File | Why |
|---|---|---|
| 1 | `README.md` | Outside-in framing of what URSA-OSCAR is and isn't |
| 2 | `Docs/architect-decisions/` (entire folder) | The ADRs. Highest signal per word. DuckDB rlock, MCP-as-thin-proxy, no-Tailwind, MCP template adoption. The architectural priors apply to Fitbit too. |
| 3 | `Docs/30-developer-guide.md` | Container roles, request walkthroughs, build / test / deploy |
| 4 | `Docs/14-current-architecture-and-filelist.md` | The canonical "what lives where" map |
| 5 | `Docs/17-oauth-setup.md` | claude.ai Custom Connector OAuth flow |
| 6 | `frontend/src/help/content/arch-*.md` (all 5 files) | Single-tenant trust boundary, multi-instance, deployment topologies, network security, MCP tool surface |
| 7 | `frontend/src/help/content/about-version.md` | Release history showing how features land incrementally |
| 8 | `Docs/install/` (the whole install path) | The Apnea Board install overhaul. Your project should ship something equivalent on day one. |

After those, read enough of the React frontend to internalize the visual language — start at `frontend/src/pages/DailyView.tsx`, `frontend/src/pages/Trends.tsx`, `frontend/src/App.tsx` (nav structure), and `frontend/src/theme.css` (the Jobscan-light theme tokens).

---

## URSA-OSCAR at a glance

**Domain:** self-hosted analytics for ResMed CPAP machine data. One operator per instance, single-tenant by design.

**Four containers:**

| Container | Job |
|---|---|
| `api` | FastAPI + DuckDB. The brain. Owns the database, runs analytics, generates PDF reports, proxies AI provider calls. |
| `web` | nginx + React + Vite. Static asset delivery + API proxy. |
| `mcp` | FastMCP server. OAuth 2.1 + PKCE + DCR. Exposes 17 analytical tools to claude.ai / Claude Code via Custom Connector. |
| `watcher` | Polls a bind-mounted source folder once a minute for new CPAP data files, triggers imports via the api. |

**Stack:**
- Python 3.12 + FastAPI + DuckDB + pandas/numpy/scikit-learn (analytics)
- React 18 + TypeScript + Vite + uPlot (UI/charts)
- FastMCP + MCP 2025 spec (external AI surface)
- Fernet master key for at-rest secret encryption, JWT for service tokens, scheme-aware Secure cookies for browser auth
- Docker Compose for deployment, TrueNAS SCALE + Dockge as the reference homelab pattern

**What it does:**
- Daily View — per-night detail with EventRug timeline, pressure traces, event lists
- Trends — single-metric regression, multivariate partial correlation, lag analysis, predictions
- Reports — provider-ready PDFs with explicit statistical methodology disclosure
- AI Assistant — in-app chat (bring-your-own-API-key for Claude / OpenAI / Gemini / OpenRouter / Groq / local LLMs) AND external (claude.ai connector via MCP)
- Settings — provider config, password management, version chips, MCP setup
- Help — 37 in-app topics across 7 sections, queryable by AI via `get_help_topic` MCP tool

**Single-tenant trust boundary:** the operator is implicitly trusted for everything on their data directory. No multi-user, no recovery flow, no cloud backend, no telemetry. Same posture applies to your project.

---

## What to LIFT from URSA (visual + architectural patterns)

### UI shell (highest priority — the headline ask)

Port these wholesale. They define URSA's look-and-feel:

1. **Left-rail navigation** — `frontend/src/App.tsx`. Sections: Overview / Daily View / Trends / Reports / Manual Logs / Profile / Settings / Help. Lucide-react icons. Sticky, collapsible on mobile.
2. **Light Jobscan-inspired theme** — `frontend/src/theme.css`. White-card-on-light-gray background, subtle shadows, indigo accent. No dark glass. Tokens are CSS variables (`--bg`, `--surface`, `--ink`, `--ink-soft`, `--accent`, etc.) — easy to retune for Fitbit's brand if desired but DON'T introduce Tailwind (ADR-001).
3. **Card-based layout** — sections live in `.card` containers with rounded corners and consistent vertical rhythm. Headers use a specific weight + size hierarchy that's defined in the theme tokens.
4. **uPlot for time-series charts** — `frontend/src/components/charts/`. We chose uPlot over Chart.js / Recharts for performance on multi-thousand-point series (CPAP waveforms are dense; HR / steps will be denser). Read the existing chart components for the cursor / tooltip / legend conventions.
5. **Sortable / filterable data tables** — `frontend/src/pages/Events.tsx` has the canonical pattern. Sort by clicking column headers, filter chips above the table, sticky header. Lift this directly for the Fitbit project's tables (Sleep Data Overview, Workout Log, etc.).
6. **Section headers with optional context line** — every page has a consistent H1 + descriptive subhead pattern. Look at the existing pages for the shape.
7. **EventBadge / pill style** — small colored pills for categorical data (event types in URSA; could be workout types, sleep stages, or HR zones in Fitbit). Same component family.

### Application capabilities

1. **AI Assistant chat panel** — `frontend/src/components/ChatPanel.tsx` + `backend/src/ursa_oscar/ai_proxy/`. Multi-provider (Anthropic / OpenAI / Gemini / OpenRouter / Groq / local) with operator-chosen system prompt. Bring-your-own-API-key encrypted at rest with the Fernet master key. Lift the whole flow — it's well-tested and provider-agnostic.
2. **Profile system** — `frontend/src/pages/Profile.tsx` + the manual-logs / vocabulary patterns. The Fitbit project's "profile" would carry age, sex, fitness goals, baseline HR zones, etc. Same architectural pattern.
3. **In-app help system** — `frontend/src/help/`. 37 topics in markdown, indexed by section, queryable by the AI via the `get_help_topic` MCP tool so external assistants can read the project's own docs. This is a force multiplier — operators self-serve, AI assistants can answer documentation questions, and you ship help next to the code that implements the thing.
4. **PDF report generation with methodology disclosure** — `backend/src/ursa_oscar/reports/`. Every statistical method gets a verbatim methodology block in the generated PDF. The current Fitbit report is one long static PDF; URSA's PDF reports are operator-selectable date ranges and metric sets, with the methodology explicit. Lift this pattern; the existing Fitbit report content becomes the source material for the new Reports page.
5. **Settings page with provider chips, version display, maintenance actions** — `frontend/src/pages/Settings.tsx`. Includes the "verify MCP" button and the AI provider key form. Take the structure; swap the specific actions.

### Architectural patterns

1. **Four-container Docker stack** — same shape (api / web / mcp / watcher). The watcher slot becomes a Google Health sync scheduler in your project (different mechanism, same role).
2. **Auto-managed secrets on first boot** — Fernet master key, JWT signing secret, service tokens between containers. Zero-ceremony first run. Read `backend/src/ursa_oscar/storage/secrets.py` and `backend/src/ursa_oscar/auth/`. Operator never copies a key around.
3. **Single bind-mounted `/data` volume** — DuckDB, secrets, processed analytics. The bind mount is the persistence boundary; everything else is ephemeral.
4. **Version-introspection** — each container reports its own version via `/version` endpoints; UI version chips read those. No env-var coordination drift. Shipped in URSA 1.1.3; see `backend/src/ursa_oscar/api/system.py` and `frontend/src/pages/Settings.tsx` chip-loading code.
5. **MCP-as-thin-proxy** (ADR) — every MCP tool calls a REST endpoint on the api container; never reaches into the DB directly. Means auth lives in one place, the analytics surface is browser-testable, and the MCP container is genuinely stateless.
6. **Public-friendly docker-compose** — strip homelab-specific networks, comment out MCP service by default, prominent per-OS path examples. See `infra/docker-compose.production.yml`. Your project should ship this same shape on day one.
7. **`Docs/install/` per-OS walkthrough** — Windows, macOS, Linux, TrueNAS, Synology, plus a `concepts.md` for users who don't know Docker. Don't repeat URSA's release-cycle mistake of letting docs lag the code — the Apnea Board adoption stall was 80% installer friction.

### MCP server template

Kevin has extracted the reusable MCP boilerplate to `burrellka/mcp-server-template` (per Kevin's project memory). Reference the URSA `mcp-server/` directory and that template. The Fitbit / Google Health version of this is the same OAuth + DCR + SSE skeleton with a different tool surface (e.g. `get_sleep_stages`, `analyze_hr_zones`, `correlate_workouts_with_sleep`).

---

## What NOT to LIFT (or lift only after critical review)

1. **DuckDB and the EDF parser.** Your project has its own database — keep it. URSA's CPAP-specific binary parsing (`backend/src/ursa_oscar/analytics/edf_parser.py`, `airsense11_layout.py`) doesn't apply. If your existing storage is MongoDB / Postgres / SQLite / whatever, leave it alone. The headline ask is UI, not DB swap.
2. **Per-night session model.** CPAP data is nightly-bounded by mask-on/mask-off. Fitbit data is continuous (HR every 5-60 seconds, steps all day, sleep stages overnight, weight episodic). The schema and query patterns are fundamentally different — don't try to port URSA's `nightly_summary` / `sessions` shape onto wearable data.
3. **The watcher daemon's file-polling pattern.** ResMed dumps EDF files to an SD card; the watcher polls a bind-mounted directory. Google Health Connect is an API-pull or webhook-push model. The "watcher container" slot in the four-container architecture still makes sense, but its internals are a sync scheduler, not a filesystem poll.
4. **Anything ResMed / CPAP-specific in the analytics layer.** Lag correlation, multivariate partial regression, bootstrap CIs — those are general-purpose statistical patterns and could apply, but URSA's specific implementations are tuned for AHI / pressure / leak metrics. Take the methodology disclosure pattern; rewrite the specific calculations.
5. **The OSCAR data-folder convention.** URSA reads OSCAR-shaped backups for compatibility with existing CPAP users. The Fitbit project should NOT try to look like OSCAR's UI conventions; it should look like Fitbit / Google Fit / Apple Health — whatever mental model the operator already has.

---

## Proposed navigation structure for the Fitbit project

Translating the current 16-page report into URSA-style segmented navigation. Use this as a starting point; refine with Kevin / the architect.

```
┌─ Overview               ← Headline cards: today's HR, sleep score, steps, calories. "Quick glance."
├─ Daily View             ← Detail page for a single day: hourly HR, sleep timeline, activity blocks.
├─ Sleep                  ← Sleep stages, Reality Score, regularity heatmap, exercise↔sleep correlations.
│   └─ Sleep Night Details  ← Drill-down per night (the existing "select a sleep date" widget).
├─ Activity               ← Steps, distance, floors, active zone minutes, weekly heatmap.
│   └─ Workout Detail       ← Per-workout HR zones (the existing "select a workout date" widget).
├─ Cardiovascular         ← Resting HR, HRV, VO2 Max, breathing rate, SpO2, EOV.
├─ Body                   ← Weight, body fat %, temperature variation.
├─ Trends                 ← Multi-metric correlation, regression projections, lag analysis. Methodology disclosure.
├─ Reports                ← Generate PDF reports with date ranges + metric selection. Carries the existing static report's content but operator-driven.
├─ Manual Logs            ← Like URSA's manual-logs page: subjective notes, supplements, events that don't come from the device.
├─ AI Assistant           ← In-app chat panel. Provider config in Settings.
├─ Profile                ← Age, sex, baseline HR zones, fitness goals, weight history.
├─ Settings               ← AI provider keys, password, version chips, MCP setup, data export.
└─ Help                   ← Topic-indexed in-app docs, AI-queryable.
```

Each top-level item is a route. Some have nested detail pages (Sleep → Sleep Night Details, Activity → Workout Detail). URSA uses React Router for this; lift the same pattern.

### Page-by-page mapping from the current report

| Current report section | Goes to | Notes |
|---|---|---|
| Resting Heart Rate | Cardiovascular | Primary chart + period-comparison table (30d/3mo/6mo/1yr) |
| Steps Count | Activity | Daily count + heatmap-by-day-of-week |
| Activity (HR zones) | Activity | Fat burn / cardio / peak stacked area |
| Weight Log | Body | Time series + trend line |
| Body Fat % | Body | (Currently no data — show empty-state pattern from URSA) |
| SpO2 | Cardiovascular | Time series with average overlay |
| Oxygen Variation (EOV) | Cardiovascular | (Currently no data — empty state) |
| HRV | Cardiovascular | Time series. **Key metric for sleep medicine correlation with CPAP** — flag this prominently. |
| Breathing Rate | Cardiovascular | Time series |
| Cardio Fitness Score | Cardiovascular | (Currently no data — empty state) |
| Temperature | Body | Time series, baseline-relative |
| Active Zone Minutes | Activity | Time series |
| Calories & Distance | Activity | Two side-by-side time series |
| Floors Climbed | Activity | Time series |
| Workout Details | Activity → Workout Detail | The interactive "select a workout date" pattern stays |
| Sleep Stages | Sleep | Stacked area chart of stage durations |
| Sleep Data Overview table | Sleep | This table is GOLD — the "Reality Score" + "Proxy Score" comparison is unique to your project. Make it a sortable table per URSA's Events table pattern. |
| Sleep Quality Analysis | Sleep | Score time series + stage distribution donut |
| Sleep Regularity | Sleep | The chronotype trend chart (sleep start / end time over time) |
| Sleep Night Details | Sleep → Sleep Night Details | Drill-down detail view per night |
| Exercise ↔ Sleep Correlations | Sleep OR Trends | Could live under Sleep as a related-analysis section, or move to Trends as a multivariate analysis |

### What the current report is missing that URSA gives you

1. **Operator-driven date range selection.** Currently a fixed 8-day report. URSA's Trends / Reports let operators pick any range.
2. **Methodology disclosure.** Currently charts are presented without saying how "Reality Score" is computed. URSA's posture is: every method gets verbatim documentation in the PDF, browse-able in-app at `/help`.
3. **Interactive AI synthesis.** Currently the operator looks at charts and forms their own opinions. URSA's AI Assistant + MCP connector lets them ask "what's the trend in my HRV been since I changed pressure on May 15?" and the AI calls tools to answer specifically.
4. **Multi-period comparison.** Currently 8-day window only. URSA's Reports do 30d/3mo/6mo/1yr structured comparisons.
5. **In-app help.** Currently the operator has to read the report's small intro paragraphs to know what each metric means. URSA has 37 topics on tap.

---

## Architectural questions to answer before writing code

These are for Kevin / the architect to decide. The new dev shouldn't pick unilaterally.

1. **Standalone or feeder?** Should this be its own product (parallel to URSA, separate claude.ai connector), or should it feed URSA's MCP so external Claude correlates "last night's sleep quality" with "last night's HRV"? The natural domain integration is sleep medicine — both data streams matter for treatment thinking.
2. **One MCP or two?** Standalone means a second claude.ai Custom Connector. Feeder means URSA's MCP gets extended with Fitbit / Health Connect tools. Two-connectors is operationally simpler; one-connector is a better Claude-context experience.
3. **Google Health Connect vs. Google Health Web API.** RESOLVED — not a real choice. Health Connect is an on-device Android store and is NOT the target. The Fitbit Web API successor is the **Google Health API** at `https://health.googleapis.com/v4/`. One path. The genuine open risk is **metric parity**: some Fitbit metrics aren't on Google v4 yet (HR webhooks confirmed missing; sleep score and HRV must be verified). This is settled by the parity harness, not by a decision — see `c:\dev\fitbit-web-ui-app\tools\parity-harness\`.
4. **What's the migration boundary with the existing fitbitkb codebase?** Full rewrite (start clean, harvest concepts only), or surgical migration (keep existing code, swap the API client + add URSA's UI shell)? The brief above assumes the latter ("keep underlying code, port look-and-feel"). Audit the existing fitbitkb for a 30-minute "what would survive a port" assessment before deciding.
5. **Time-series storage at Fitbit scale.** HR at 5-second cadence over 5 years is ~32M points. Steps at 1-minute over 5 years is ~2.6M. Sleep stages at 30-second over 5 years is ~5M. Your existing DB shape determines whether this is comfortable or you need to compact / pre-aggregate.
6. **Two operators in one household?** URSA's single-tenant model assumes one CPAP user per instance. Some households want two Fitbits — his and hers — under one analytics pane. Pick: (a) two instances per URSA's pattern, or (b) actual multi-profile support. URSA chose (a) for trust-boundary reasons; the Fitbit project could legitimately go either way.

---

## Suggested first-week milestones for the new dev

1. **Day 1**: Read the docs above. Audit the existing fitbitkb. Form a "what survives, what changes, what's new" inventory.
2. **Day 2-3**: Stand up the four-container scaffolding (`api`, `web`, `mcp`, `watcher`/sync). Get a single chart rendering URSA-styled with one real data series from your existing DB.
3. **Day 4-5**: Build the left-rail nav skeleton with all sections from the proposal above, even if most are placeholders. Get URSA's theme + chart conventions locked in.
4. **End of week 1**: One full vertical slice working — Sleep section, end-to-end, with a real metric and a real chart and a real drill-down. Tagged as `0.1.0`. Demo-able.
5. **Week 2+**: Add sections one at a time, following the same pattern. Profile + AI Assistant + Help land in parallel (they're cross-cutting). PDF Reports and Trends are later (they depend on more sections being built).

---

## On the relationship to URSA

URSA is at 1.1.7 as of June 2026, in active public release on Apnea Board. The codebase is stable and the patterns are battle-tested at this point. Read URSA's code with confidence — if something looks weird, there's usually a documented reason (check the ADRs and the in-app help). If it still looks weird, ping Kevin.

URSA's principle that's worth repeating: **single-tenant, operator-owned data, no cloud backend, no recovery flow, no telemetry, password protection that the operator owns, secrets auto-managed at first boot.** The Fitbit project should inherit this posture by default. Don't sneak in cloud sync, email recovery, or registration flows — those break the trust boundary that makes the homelab self-hosted pattern work.

---

## License

URSA-OSCAR is GPL-3.0. Your project may or may not be — that's Kevin's call. If it's GPL too, copying code from URSA is straightforward. If it's not, treat URSA's code as reference material (read it, understand it, write your own) rather than direct lift.

---

## Contact / escalation

- Architectural questions Kevin can't answer: raise them in writing, get sign-off before building
- Questions about URSA-specific patterns: read the in-app help first (it's queryable via `get_help_topic`), then ask
- Bug suspicion in URSA code you're studying: file a GitHub Issue on burrellka/URSA-OSCAR with the file:line — Kevin will fix and you can resume reading
