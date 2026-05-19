# URSA-OSCAR — Developer Guide

This is the maintainer's-eye-view of the URSA-OSCAR codebase. If you're forking the project, picking up where the original team left off, or trying to make a change that touches multiple containers, start here.

Audience: Python + TypeScript developers comfortable with Docker, REST, SSE, and basic CPAP-data semantics. No prior URSA-OSCAR knowledge assumed.

---

## Table of contents

1. [What URSA-OSCAR is](#1-what-ursa-oscar-is)
2. [Five-minute mental model](#2-five-minute-mental-model)
3. [Repository layout](#3-repository-layout)
4. [The four containers](#4-the-four-containers)
5. [End-to-end request walkthroughs](#5-end-to-end-request-walkthroughs)
6. [Data model](#6-data-model)
7. [Local development setup](#7-local-development-setup)
8. [Testing](#8-testing)
9. [Building and publishing images](#9-building-and-publishing-images)
10. [Deployment (TrueNAS / Dockge / generic Compose)](#10-deployment)
11. [Configuration reference](#11-configuration-reference)
12. [Adding a new feature](#12-adding-a-new-feature)
13. [Architectural decision records](#13-architectural-decision-records)
14. [Known limitations and Phase-N housekeeping](#14-known-limitations-and-phase-n-housekeeping)

---

## 1. What URSA-OSCAR is

URSA-OSCAR is a self-hosted analytics platform for CPAP-machine output. Specifically: it ingests the DATALOG tree a ResMed AirSense 11 (and similar) writes to its SD card, computes per-night summaries that match OSCAR's well-known clinical math, surfaces the data through a React web UI, and exposes the same analytics as MCP tools so AI assistants (Claude, GPT-4, etc.) can query the data conversationally.

It is **not** a medical device. It is **not** OSCAR. It is a homelab-deployed service that complements OSCAR's desktop application for users who want network-accessible review and AI-assistant integration.

**The five things URSA-OSCAR does:**

1. **Ingests** raw EDF + JSON files from a ResMed SD card export into a single embedded DuckDB database
2. **Computes** nightly summaries (AHI broken into central / obstructive / hypopnea / RERA components, pressure percentiles, leak statistics, mask-on time) using the same math OSCAR's Daily View shows
3. **Serves** a React web UI for daily / overview / events / trends / data-management review
4. **Exposes** an MCP server (FastMCP + SSE + OAuth 2.1) so AI clients can call `get_nightly_summary`, `analyze_correlation`, `compare_periods`, etc.
5. **Brings** the AI experience in-app via a chat panel with bring-your-own-key for Claude, OpenAI, Gemini, OpenRouter, Groq, or any OpenAI-compatible local LLM

---

## 2. Five-minute mental model

### One database, two ways to read it

There is exactly ONE DuckDB file (`/data/ursa-oscar.duckdb` inside the API container; bind-mounted from the host's data directory). The API container is the **sole writer**. Other containers (MCP, watcher, web) **never open DuckDB directly** — they go through the API's HTTP surface.

This isn't aesthetic preference; DuckDB v1.x acquires a file lock that prevents *any* other process from opening the file, even read-only, while the writer is open. See [`Docs/architect-decisions/adr-003-mcp-is-thin-proxy-over-api.md`](architect-decisions/adr-003-mcp-is-thin-proxy-over-api.md) for the full rationale. Once you internalize "API is the only thing that touches the DB," everything else makes sense.

```
                         ┌────────────────────┐
                         │  ursa-oscar-api    │
                         │  (sole DB writer)  │
                         │  ┌──────────────┐  │
                         │  │ DuckDB file  │  │
                         │  └──────────────┘  │
                         └─▲────────▲──────▲──┘
                           │HTTP    │HTTP  │HTTP
                           │        │      │
       ┌───────────────────┘        │      └────────────────┐
       │                            │                       │
┌──────┴────────┐         ┌─────────┴──────┐       ┌────────┴───────┐
│ursa-oscar-mcp │         │ursa-oscar-web  │       │ursa-oscar-     │
│(FastMCP+SSE)  │         │(nginx + React) │       │  watcher       │
│Claude.ai      │         │Browser         │       │(daemon)        │
└───────────────┘         └────────────────┘       └────────────────┘
```

### Two presentations of the same analytical core

The eleven analytical tools (`get_nightly_summary`, `get_ahi_breakdown`, `compare_periods`, `analyze_correlation`, etc.) live as **API endpoints** under `/api/v1/...`. Both presentation layers wrap those endpoints:

- **MCP server** wraps each as a `@mcp.tool()` decorated function, exposed over SSE to Claude.ai
- **AI proxy** (inside the API container itself) describes the same tools to whatever LLM the operator configured, executes them in-process when the LLM calls them, and streams the conversation to the chat panel over SSE

When you add a new tool, **add it to the API once**. The MCP wrapper and the AI proxy descriptor both reference the same underlying endpoint. See §12.

### Ingestion: pure batch, idempotent per-night

The importer reads an SD card layout (either `<root>/DATALOG/YYYYMMDD/...edf` or a directly-supplied YYYYMMDD-flat directory), parses each night's EDFs into events + waveforms + summary, and writes to DuckDB. Per-night writes are atomic: the row's existence in `nightly_summary` is the source of truth for "this night is imported."

Re-imports default to **skip-existing** mode (don't re-parse nights already in the DB). Force re-import (`?force=true`) deletes+rewrites every night. This makes re-uploading the same SD card cheap.

### Single timezone fiction

The CPAP device records timestamps in its own local wall-clock with **no timezone metadata**. Server-side, we treat those timestamps as naive datetime and never apply any conversion. The display layer (frontend) optionally applies an operator-configured offset (Phase 4 Ticket 4 — the DeviceClock feature) to render times in the operator's actual local time when the device's clock differs (DST not auto-adjusted, etc.). All wire-format timestamps stay device-naive.

### Conversation state lives in the browser

The AI chat panel's conversation history is stored in `localStorage` keyed per Daily View date. Server-side has zero knowledge of conversations. This is intentional — see Phase 5 Decision 5.

---

## 3. Repository layout

```
URSA-OSCAR/
├── backend/                        Python 3.11 / FastAPI — the API container source
│   ├── pyproject.toml              dependencies + version
│   ├── Dockerfile                  image build (ARM64 + AMD64 multi-arch)
│   └── src/ursa_oscar/
│       ├── main.py                 FastAPI app factory + lifespan
│       ├── config.py               pydantic-settings env reader
│       ├── api/                    HTTP endpoint modules
│       │   ├── ai.py               Phase 5: /ai/{providers,config,chat,test}
│       │   ├── analytics.py        Phase 3: /analytics/{compare,correlation,trend,...}
│       │   ├── events.py           per-night event listings
│       │   ├── exports.py          CSV exports
│       │   ├── health.py           /healthz
│       │   ├── imports.py          Phase 3 folder upload + Phase 4 async queue
│       │   ├── manual_logs.py      Phase 3: subjective logging
│       │   ├── nights.py           nightly summaries + per-night purge
│       │   ├── profile.py          UserProfile read/patch
│       │   ├── system.py           Settings page surface + MCP verify
│       │   ├── timeseries.py       waveform endpoints
│       │   └── vocab.py            autocomplete vocabulary
│       ├── ai_proxy/               Phase 5: AI provider abstraction
│       │   ├── providers/
│       │   │   ├── base.py         ProviderAdapter ABC + AiMessage shapes
│       │   │   ├── claude.py       Anthropic Messages API adapter
│       │   │   ├── openai_compat.py OpenAI-format adapter (covers 6+ providers)
│       │   │   └── presets.py      7-preset registry
│       │   ├── tools.py            11 LLM tool descriptors + executor
│       │   ├── prompt.py           system prompt template
│       │   ├── secrets.py          Fernet secret store
│       │   └── config_store.py     non-secret AI config
│       ├── analytics/              Pure math, no DB writes
│       │   ├── edf_parser.py       EVE/BRP/PLD/SA2 EDF readers
│       │   ├── session_analyzer.py per-session aggregation
│       │   ├── summary_builder.py  NightlySummary construction
│       │   ├── leak_detector.py    leak redline math
│       │   ├── settings_parser.py  CurrentSettings.json reader
│       │   ├── period_compare.py   compare_periods math
│       │   ├── correlation.py      pearsonr wrapper
│       │   ├── trend.py            linregress wrapper
│       │   ├── manual_log_summary.py manual-log rollups
│       │   ├── metric_resolver.py  unified metric naming (nightly cols + manual logs)
│       │   └── recompute_summary.py session-exclusion recompute
│       ├── ingestion/              EDF → DuckDB pipeline
│       │   ├── importer.py         import_path() — main entry
│       │   └── airsense11_layout.py SD card layout detection
│       ├── models/                 Pydantic domain models
│       │   ├── domain.py           NightlySummary, ImportLogEntry, Session, etc.
│       │   └── manual_logs.py      discriminated-union manual log types
│       ├── services/               Background services (asyncio)
│       │   ├── import_worker.py    Phase 4: async import job processor
│       │   └── profile_vocab_sync.py bidirectional profile↔vocab sync
│       └── storage/                DuckDB layer
│           ├── db.py               DuckDBManager (RLock-serialized writes)
│           ├── migrations.py       SCHEMA_VERSION + apply_migrations()
│           ├── schema.sql          DDL — authoritative schema
│           ├── profile_store.py    profile.json file backing
│           ├── vocab_store.py      vocab.json file backing
│           └── repositories/
│               ├── events.py
│               ├── nights.py
│               ├── manual_logs.py
│               ├── sessions.py
│               ├── timeseries.py
│               └── import_jobs.py
│
├── frontend/                       React 18 / TypeScript / Vite — the web container source
│   ├── package.json
│   ├── nginx.conf                  serves the SPA + proxies /api → ursa-oscar-api:8000
│   ├── Dockerfile                  multi-stage: vite build → nginx:alpine
│   └── src/
│       ├── App.tsx                 BrowserRouter + Routes
│       ├── components/
│       │   ├── AiChatPanel.tsx     Phase 5 slide-in chat panel
│       │   ├── TimeSeriesChart.tsx uPlot wrapper
│       │   ├── EventRug.tsx        event-tick rug above charts
│       │   ├── CalendarHeatmap.tsx Overview AHI heatmap
│       │   ├── Layout.tsx          sidebar + main outlet
│       │   └── Histogram.tsx
│       ├── pages/
│       │   ├── Overview.tsx        landing page
│       │   ├── Daily.tsx           night-by-night drilldown
│       │   ├── Statistics.tsx
│       │   ├── Events.tsx
│       │   ├── Import.tsx
│       │   ├── Trends.tsx          Phase 3
│       │   ├── ManualLogs.tsx      Phase 3
│       │   ├── Profile.tsx         Phase 3 + Phase 4 DeviceClock
│       │   ├── Settings.tsx
│       │   ├── SettingsAi.tsx      Phase 5
│       │   └── DataManagement.tsx
│       ├── api/
│       │   ├── client.ts           Typed REST client; api.* namespace
│       │   └── types.ts            all wire-format types mirror backend
│       └── lib/
│           ├── format.ts           AHI / time / leak formatters
│           └── timeOffset.ts       Phase 4 Ticket 4 device-clock shift
│
├── mcp-server/                     FastMCP server (Claude.ai connector)
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/ursa_oscar_mcp/
│   │   ├── server.py               FastMCP app + auth wiring
│   │   ├── client.py               httpx client → ursa-oscar-api
│   │   ├── envelope.py             {ok, data, ...} response shape
│   │   └── tools/                  one @mcp.tool()-decorated module per tool
│   └── tests/
│       ├── conftest.py             Phase 5 Ticket 0: in-thread API fixture
│       ├── test_tools.py           12 tool round-trip tests
│       └── verification/
│           └── test_auth_boundary.py
│
├── watcher/                        Phase 4: file watcher daemon
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/ursa_oscar_watcher/
│   │   ├── __main__.py             python -m ursa_oscar_watcher
│   │   ├── watcher.py              poll loop with fingerprint + quiescence
│   │   ├── fingerprint.py          os.scandir-based tree fingerprint
│   │   ├── api_client.py           httpx wrapper for /imports + /imports/jobs
│   │   └── config.py               WatcherConfig env reader
│   └── tests/
│       └── test_watcher.py         13 cases using FakeClock + FakeApi
│
├── infra/
│   ├── docker-compose.yml          production stack (TrueNAS / Dockge)
│   ├── docker-compose.dev.yml      local development with bind mounts
│   ├── docker-compose.production.yml legacy; see top-level docker-compose.yml
│   ├── build_and_push.ps1          Windows-side build+push helper
│   └── verify-mcp-live.sh          MCP smoke-check script
│
├── Docs/                           Public documentation
│   ├── URSA-OSCAR_Design.md        v1.2 — authoritative spec
│   ├── URSA-OSCAR_Framework.md     vision + product framing
│   ├── 03-mcp-tool-contract.md     MCP tool envelope spec
│   ├── 14-current-architecture-and-filelist.md
│   ├── 17-oauth-setup.md           Claude.ai MCP connector setup
│   ├── 30-developer-guide.md       (this file)
│   ├── architect-decisions/        ADRs
│   │   ├── adr-001-no-tailwind-no-shadcn.md
│   │   ├── adr-002-mcp-server-template-adoption.md
│   │   ├── adr-003-mcp-is-thin-proxy-over-api.md
│   │   └── adr-004-duckdb-cross-container-concurrency.md
│   └── WIP/                        Build agent's per-phase handovers (gitignored)
│
├── README.md                       Public-facing entry point
├── COPYRIGHT
├── LICENSE                         GPL-3.0
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
└── Makefile                        common dev tasks
```

---

## 4. The four containers

### 4.1 `ursa-oscar-api` — analytics + ingestion + AI proxy

**Image:** `brain40/ursa-oscar-api:<version>`
**Build:** `backend/Dockerfile`
**Listens:** 8000 inside the container (mapped via the web container's reverse-proxy)
**Owns:** the DuckDB file, the profile/vocab JSON files, the secrets blob
**Started by:** FastAPI lifespan in `backend/src/ursa_oscar/main.py`

Lifespan responsibilities, in order:
1. Open DuckDB (`DuckDBManager(settings.db_path, read_only=False)`)
2. Run `apply_migrations(db)` — idempotent; brings the schema to the current `SCHEMA_VERSION`
3. Initialize `/data/profile.json` and `/data/vocab.json` from packaged defaults if absent
4. Resolve the Fernet master key (env or first-start generation → `/data/secret_key.gen`)
5. Construct `SecretStore` + `ConfigStore` for the AI proxy and attach to `app.state`
6. Start `ImportWorker` (asyncio task draining `import_jobs`)
7. Yield (serve traffic)
8. On shutdown: stop the worker, close the DB

### 4.2 `ursa-oscar-mcp` — Claude.ai MCP connector

**Image:** `brain40/ursa-oscar-mcp:<version>`
**Build:** `mcp-server/Dockerfile`
**Listens:** 8000 inside the container (mapped to host 8085 by default)
**Connects to:** `ursa-oscar-api:8000` over the `kairos-net` Docker network
**Reads:** never directly. All data flows through the API container's HTTP surface.

The MCP server is intentionally a thin wrapper:
1. FastMCP starts up, loads bearer-token + OAuth client config from env
2. Each `@mcp.tool()`-decorated function calls `api_get(...)` / `api_post(...)` against the API container
3. Wraps responses in `{ok: true, data: ...}` envelope (or `{ok: false, code: ..., error: ...}` on failure)

ADR-003 captures the rationale: DuckDB's process-level lock makes cross-container DB access impossible; the API becomes the sole point of truth.

### 4.3 `ursa-oscar-web` — React SPA + nginx reverse proxy

**Image:** `brain40/ursa-oscar-web:<version>`
**Build:** `frontend/Dockerfile` (multi-stage: Vite build → nginx:alpine static serve)
**Listens:** 80 inside the container (typically mapped to host 5063)
**Serves:** static SPA bundle + reverse-proxies `/api/v1/*` → `ursa-oscar-api:8000`
**Holds:** zero state. Browser session + localStorage are the only client memory.

The nginx config (`frontend/nginx.conf`) has two specific tweaks worth knowing about:

1. **`client_max_body_size 5000m`** — Phase 3 folder upload sends large multipart bodies (200-800 MB on a real SD card). Without this bump nginx 413s before the API sees the request.
2. **`X-Accel-Buffering: no`** propagation — Phase 5 chat endpoint streams SSE; if nginx buffers it, the chat panel doesn't update until the LLM finishes. The API sets this header; nginx honors it.

### 4.4 `ursa-oscar-watcher` — file-watcher daemon

**Image:** `brain40/ursa-oscar-watcher:<version>`
**Build:** `watcher/Dockerfile`
**Listens:** nothing (no HTTP surface)
**Reads:** `/cpap-import` bind-mount (the operator's NAS-mounted SD card location)
**Calls:** `ursa-oscar-api:8000/api/v1/imports` to enqueue jobs

Phase 4 Ticket 3 introduced this loop. See §5.4 for the flow.

---

## 5. End-to-end request walkthroughs

The five most-traveled paths through the system.

### 5.1 Operator imports a folder via the browser

```
Browser file picker (webkitdirectory)
       │
       │ multipart POST /api/v1/imports/upload  (5000 MB cap)
       │
       v
ursa-oscar-web (nginx)
       │
       │ proxy_pass http://ursa-oscar-api:8000
       │
       v
ursa-oscar-api: api/imports.py:upload_folder_and_import
       │ 1. Sanitize each multipart filename via _sanitize_relpath():
       │    normalize backslashes, strip drive letters, reject traversal
       │    + absolute paths + OS-junk segments, require allowlisted suffix
       │ 2. Write accepted files to tempdir
       │ 3. Enqueue ImportJob(upload_dir=tempdir)
       │
       │ HTTP 200 with {id, status: "queued", ...}
       v
Browser polls /api/v1/imports/jobs/<id> every 2s
       │
       v
ImportWorker (async task in API process)
       │ 1. claim_next_queued() — atomic UPDATE status='running'
       │ 2. asyncio.to_thread(self._run_import_sync, job)
       │    └─ locate_import_root(tempdir) — find SD root or DATALOG/
       │    └─ import_path(target, db, skip_existing=not force)
       │       └─ for each YYYYMMDD/:
       │          └─ discover_sessions() → SessionAggregate list
       │          └─ build_summary() + write to nightly_summary + events
       │          └─ write per-channel timeseries (pressure, leak, ...)
       │          └─ if has_exclusions: recompute_for_date()
       │ 3. mark_completed(job, result.model_dump())
       │ 4. shutil.rmtree(tempdir)  — finally block
       │
       v
Next browser poll: status="completed", result_json has the ImportLogEntry
```

### 5.2 Operator opens Daily View for one night

```
Browser GET /daily/2026-05-13
       │
       │ React Router → pages/Daily.tsx
       │
       v
Daily.tsx useEffect:
       │ Parallel:
       │   GET /api/v1/nights              (date list for prev/next nav)
       │   GET /api/v1/night/2026-05-13    (NightlySummary)
       │   GET /api/v1/events?date=2026-05-13
       │   GET /api/v1/nights/2026-05-13/sessions
       │
       v
After first paint, second useEffect:
       │   GET /api/v1/timeseries/2026-05-13?series=pressure&series=leak&...
       │
       v
Charts component:
       │ For each track, render <TimeSeriesChart>
       │   - timestamps array (epoch seconds, naive-parsed from ISO)
       │   - per-series stroke (CSS var → resolveCssColor → concrete RGB)
       │   - synced cursor across all charts (cursor.sync.key)
       │   - pinned xMin/xMax from summary.start_time/end_time
       │
       v
SessionInformationCard:
       │ Maps each Session row → table row
       │ Checkbox bound to session.excluded
       │ onChange → POST /api/v1/nights/<date>/sessions/<id>/toggle
       │            → server-side recompute_for_date → response carries
       │              the new NightlySummary
       │ Splice the new summary into local state; tiles re-render
```

### 5.3 Operator chats with AI on Daily View

```
Daily.tsx renders <AiChatPanel open={showChat} currentDate=...>
       │ User types: "How was last night?"
       │
       │ POST /api/v1/ai/chat (SSE response)
       │   body: { messages: [...], context: { current_date: "2026-05-13" } }
       │
       v
api/ai.py:chat
       │ 1. Load config (provider_id, model, endpoint)
       │ 2. Load API key from SecretStore
       │ 3. Build ProviderAdapter via build_adapter()
       │ 4. Load user profile + device_clock + render system prompt
       │ 5. api_base_url = request.scope["server"]  ← loopback to self
       │
       v
adapter.chat(messages, tools=TOOL_DESCRIPTORS, system_prompt=...)
       │  (Claude adapter or OpenAI-compat adapter)
       │
       v
Live LLM streams events:
       │  ← text "Looking at "
       │  ← tool_call_start id=tu_xyz name=get_nightly_summary
       │  ← tool_call_input partial_input='{"date":'
       │  ← tool_call_input partial_input=' "2026-05-13"}'
       │  ← tool_call_complete arguments={"date":"2026-05-13"}
       │  ← complete stop_reason=tool_use
       │
       │ Server appends to conversation:
       │   assistant.tool_calls = [{id, name, arguments}]
       │
       │ For each pending tool call:
       │   execute_tool("get_nightly_summary", {...}, api_base_url)
       │     → httpx.get(http://<scope>:<port>/api/v1/night/2026-05-13)
       │     → returns {ok: true, data: {NightlySummary}}
       │   Append AiMessage(role="tool", tool_call_id=tu_xyz, content=...)
       │   Emit SSE: tool_result
       │
       │ Loop: adapter.chat() again with the updated conversation
       │
       v
LLM streams the answer:
       │  ← text "On 5/13 you had..."
       │  ← text " AHI of 3.94..."
       │  ← complete stop_reason=end_turn
       │
       v
Browser AiChatPanel:
       │ Each SSE frame parsed → applyStreamEvent reducer
       │ text → append to last assistant message content
       │ tool_call_complete → add to message.tool_calls
       │ tool_result → set tool_calls[i].status=complete + summary
       │ complete → setStreaming(false)
       │
       v
localStorage persists conversation as ursa_oscar_chat_2026-05-13
```

### 5.4 Operator drops an SD card; watcher auto-imports

```
Operator copies new DATALOG/YYYYMMDD/* into /cpap-import (bind mount)
       │
       v
ursa-oscar-watcher (poll every 30s):
       │ 1. compute_fingerprint(/cpap-import)
       │    └─ For each DATALOG/* child: (name, max(dir_mtime, newest_file_mtime))
       │ 2. If fingerprint changed: reset quiescence timer
       │ 3. If fingerprint stable for 30s: trigger
       │
       v
POST /api/v1/imports
       │ body: { source_path: "/cpap-import" }
       │
       │ ← 200 { id: 42, status: "queued" }
       v
Watcher polls /api/v1/imports/jobs/42 every 30s
       │ status: "running"
       │ status: "completed"
       v
If URSA_OSCAR_IMPORT_WEBHOOK_URL set:
       │ POST <webhook url>
       │ body: {
       │   event: "import_completed",
       │   job_id: 42,
       │   nights_imported: 1,
       │   nights_skipped_existing: 65,
       │   latest_date: "2026-05-14"
       │ }
```

### 5.5 Claude.ai connector calls a tool over MCP

```
Claude.ai (with the connector OAuth'd in) calls get_nightly_summary
       │
       │ SSE POST to MCP server (OAuth token in header or static bearer)
       │
       v
ursa-oscar-mcp: FastMCP server
       │ 1. Validate bearer / OAuth token
       │ 2. Route to @mcp.tool()-decorated get_nightly_summary
       │
       v
tools/nightly_summary.py:get_nightly_summary
       │ if end_date: api_get("/api/v1/nights", params={start, end})
       │ else:        api_get(f"/api/v1/night/{date}")
       │
       v
ursa-oscar-api: nights router serves the JSON response
       │
       v
MCP wraps in {ok: true, data: ...} envelope
       │
       v
Claude.ai receives the envelope, generates a response for the user
```

---

## 6. Data model

The schema is in `backend/src/ursa_oscar/storage/schema.sql`. Migrations in `migrations.py`. Current SCHEMA_VERSION: **5** (Phase 5 close).

### Core tables

**`nightly_summary`** — one row per night. Primary key: `date`. Holds AHI components, pressure/leak percentiles, mask-on time, equipment settings.

**`nightly_events`** — one row per respiratory event. `id` from a sequence; `(date, timestamp, session_id, event_type)` are the meaningful axes.

**`sessions`** (v4) — one row per non-empty CPAP session within a night. `(date, session_id)` PK. Carries `start_ts`, `end_ts`, `mask_on_minutes`.

**`excluded_sessions`** (v4) — Phase 4 Ticket 1: operator's "don't count this session" list. Inserts and deletes both used (insert = exclude; delete = re-include).

**`import_jobs`** (v5) — Phase 4 Ticket 2 async queue. `status` field is the state machine: `queued → running → {completed | failed | orphaned}`. `result_json` holds the serialized `ImportLogEntry` on success.

**`manual_logs`** (v1) — Phase 3 subjective logging. Discriminated by `log_type` (medication / symptom / alertness / sleep_environment / freeform). See `models/manual_logs.py` for the Pydantic discriminated union.

**`import_log`** — historical record of every import call. Append-only.

**`config`** — key/value config — currently lightly used; the Settings page reads env-derived state via `/api/v1/system/config` rather than this table.

**`schema_version`** — migration tracking.

### Time-series tables (one per channel)

`pressure_timeseries`, `flow_timeseries`, `leak_timeseries`, `flow_limit_timeseries`, `tidal_volume_timeseries`, `minute_vent_timeseries`, `resp_rate_timeseries`, `snore_timeseries`. PK is `(date, timestamp)`. Pressure additionally carries `epap` (column) since they're recorded together.

Why eight separate tables instead of `(date, timestamp, channel, value)` rows? DuckDB's columnar layout + the per-night `delete_for_date()` pattern make per-channel tables a better fit. We also occasionally read all channels for a night in parallel.

### File-backed state

Lives in `/data/` alongside the DuckDB file:

- `profile.json` — `UserProfile` (clinical context + display preferences + DeviceClock config)
- `vocab.json` — autocomplete vocabularies for medication names, symptoms, etc.
- `ai_config.json` — non-secret AI proxy config (Phase 5)
- `secrets.enc` — Fernet-encrypted API keys (Phase 5)
- `secret_key.gen` — first-start Fernet master key (operator picks this up + deletes after copying to compose env)

---

## 7. Local development setup

### Prerequisites

- Python 3.11+ (3.12 tested)
- Node 20+
- Docker + Docker Compose (for full-stack runs)
- Make (optional)
- A Windows / macOS / Linux box; Windows is the most-tested operator platform but Linux works fine for dev

### One-time setup

```bash
git clone https://github.com/burrellka/URSA-OSCAR.git
cd URSA-OSCAR

# Backend (editable install with dev deps)
cd backend
pip install -e ".[dev]"
cd ..

# Frontend
cd frontend
npm install
cd ..

# Watcher (optional, only if you're working on the watcher)
cd watcher
pip install -e .
cd ..
```

### Run tests

```bash
# Backend
cd backend && pytest --ignore=tests/regression
# 135 tests at Phase 5; the regression suite is operator-only

# MCP
cd mcp-server && pytest    # 12 tool tests + 6 auth boundary

# Watcher
cd watcher && pytest       # 13 cases
```

### Run the stack locally

The simplest option is the full Docker Compose stack (mirrors production):

```bash
docker compose -f infra/docker-compose.dev.yml up
```

For frontend hot-reload while iterating on UI:

```bash
# Terminal 1 — API only
cd backend
URSA_OSCAR_DB_PATH=./local.duckdb \
URSA_OSCAR_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
uvicorn ursa_oscar.main:app --reload --port 8000

# Terminal 2 — Vite dev server (proxies /api/* → :8000 via vite.config.ts)
cd frontend
npm run dev    # http://localhost:5173
```

The Vite dev server's `proxy` block already targets `http://localhost:8000`, so the React side hot-reloads while the Python side is also reload-aware.

### Seed test data

The 4-night regression fixture lives at `backend/tests/regression/fixtures/nights/oscar-reference/`. To seed your local DB:

```bash
cd backend
python -m ursa_oscar.ingestion.importer \
    tests/regression/fixtures/nights/oscar-reference \
    --db-path ./local.duckdb --include-timeseries
```

---

## 8. Testing

### Test layout

```
backend/tests/
├── conftest.py            shared fixtures (FIXTURE_ROOT, ...)
├── unit/                  pure unit tests
│   ├── test_importer.py
│   ├── test_settings_parser.py
│   ├── test_storage_roundtrip.py
│   └── test_session_analyzer.py
├── integration/           tests that need a DuckDB or FastAPI TestClient
│   ├── test_api_endpoints.py
│   ├── test_analytics_and_delete.py
│   ├── test_upload_endpoint.py
│   ├── test_session_exclusion.py
│   ├── test_async_import.py
│   ├── test_device_clock.py
│   └── test_ai_proxy.py
├── regression/            operator-only — Kevin's real SD card data
│   ├── canonical_targets.py    expected event counts per night (gitignored)
│   └── test_oscar_parity.py
└── smoke/                 live-API tests (require credits)
    └── test_claude_live_smoke.py

mcp-server/tests/
├── conftest.py            in-thread API + seeded DB
├── test_tools.py
└── verification/test_auth_boundary.py

watcher/tests/
└── test_watcher.py        FakeClock + FakeApi
```

### Running specific suites

```bash
# Backend, no regression (the default)
cd backend && pytest

# Backend, with regression (requires Kevin's fixture data — operator only)
cd backend && pytest

# A specific file
cd backend && pytest tests/integration/test_ai_proxy.py -v

# A specific test
cd backend && pytest -k "test_secret_store_roundtrip"

# Live Claude smoke test (requires real API key)
cd backend
set CLAUDE_API_KEY_LIVE=sk-ant-...
pytest tests/smoke/ -v -s
```

### Coverage targets

The project doesn't enforce a coverage percentage — pragmatic test-when-it-matters policy. New analytical math should have at least one canonical-target test; new endpoints should have at least one TestClient round-trip; new repositories should have at least one set+get sanity test. Locking down bugs the operator hits in production is the #1 priority — every bug fix lands with a regression test.

### The scripted-adapter pattern (for SSE-streaming endpoints with multi-turn loops)

The AI proxy's `/api/v1/ai/chat` endpoint runs a server-side tool-execution loop: stream events from a provider adapter → execute requested tools → feed results back → continue. This control flow has a wire-protocol contract that a unit test against the adapter alone can't verify and a live-LLM smoke test can only verify at the cost of API credits + cross-internet latency.

The scripted-adapter pattern, introduced in Phase 5.5, gives full-loop coverage in <5s with no credentials. It lives in `backend/tests/integration/test_ai_proxy.py` and consists of three pieces:

**1. `_ScriptedAdapter` test double.** A drop-in replacement for `ClaudeAdapter` / `OpenAiCompatAdapter` whose `chat()` async-iterates a pre-scripted list of `AiStreamEvent` objects. The script is a list-of-lists: outer dimension is "which chat() call within the multi-turn loop"; inner dimension is "events emitted during that call". The adapter advances through the outer list automatically, so a multi-tool comparison query just builds a 3-element outer list (turn 1 = call tool A, turn 2 = call tool B, turn 3 = render prose).

**2. `_setup_chat(monkeypatch, script, tool_results=...)` helper.** Wires the scripted adapter and a fake `execute_tool` into the `ursa_oscar.api.ai` module via monkeypatch. `tool_results` can be a list (FIFO across all tool calls), a dict keyed by tool name, or `None` for the trivial `{ok: True, data: {}}` default.

**3. `_parse_sse_events(body)` helper.** Pulls the JSON payloads out of every `data:` frame in the `Content: text/event-stream` response body, skipping comments and blank frames. Returns a list of dicts the test asserts on.

Together they make a typical test ~30 lines:

```python
def test_chat_correlation_tool_call(chat_ready_client, monkeypatch):
    script = [
        [
            _ai_event("tool_call_start", id="tu_c", name="analyze_correlation"),
            _ai_event("tool_call_complete", id="tu_c",
                      name="analyze_correlation",
                      arguments={"metric_a": "total_ahi", "metric_b": "p95_leak",
                                 "start_date": "2026-04-15",
                                 "end_date": "2026-05-15", "lag_days": 0}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="Weak negative correlation (r=-0.21, n=28)."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {"pearson_r": -0.21, "n_pairs": 28}},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "Does my AHI correlate with leak?"}],
    })
    events = _parse_sse_events(r.content)
    tcc = next(e for e in events if e["event_type"] == "tool_call_complete")
    assert tcc["payload"]["arguments"]["metric_a"] == "total_ahi"
```

**When to use it.** Any change to:
- `event_generator()` in `backend/src/ursa_oscar/api/ai.py`
- The multi-turn tool-execution loop's wire-protocol contract
- Adapter event-shape definitions in `ai_proxy/providers/base.py`

…should be covered by a scripted-adapter test before merge. The 0.9.6 bug (server forwarded the adapter's per-turn `complete` event, breaking client-side for-await early) went out in 0.9.0 specifically because no scripted-adapter coverage of the multi-turn loop existed at that point. The eight tests now in place cover all five acceptance-matrix query classes (Q1 single-tool, Q2 multi-tool comparison, Q3 correlation, Q4 trend, Q5 manual logs) plus the three error paths (tool returns ok=False, adapter errors mid-stream, runaway-tool-loop safety cap).

Run them with:

```bash
cd backend && pytest tests/integration/test_ai_proxy.py -v -k "scripted or chat_"
```

Total wall time: <5s. Run them on every PR that touches the chat code path.

---

## 9. Building and publishing images

The build script lives at `infra/build_and_push.ps1`. PowerShell-only currently (operator's TrueNAS-host workflow); a bash equivalent would be a small port if you need it.

```powershell
# Build all four images at version 0.9.0, push to Docker Hub
.\infra\build_and_push.ps1 -Version 0.9.0 -DockerHubUser brain40

# Build only, no push
.\infra\build_and_push.ps1 -Version 0.9.0 -SkipPush
```

The script builds each image's `Dockerfile` with the repo root as the build context (so `COPY backend/src/...` etc. resolve correctly), tags both `<version>` and `latest`, and pushes both tags.

Manual single-image build:

```bash
docker build -t brain40/ursa-oscar-api:0.9.1 -t brain40/ursa-oscar-api:latest \
    -f backend/Dockerfile .
docker push brain40/ursa-oscar-api:0.9.1
docker push brain40/ursa-oscar-api:latest
```

### Multi-arch (ARM64 + AMD64)

The Dockerfiles use `python:3.11-slim` / `node:20-alpine` / `nginx:alpine` base images, all of which ship multi-arch. The default `docker build` on Docker Desktop produces single-arch for the host architecture. For multi-arch publishes, use `docker buildx`:

```bash
docker buildx create --use --name ursa-multi
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t brain40/ursa-oscar-api:0.9.1 \
    -t brain40/ursa-oscar-api:latest \
    -f backend/Dockerfile \
    --push .
```

### Bumping image versions

URSA-OSCAR runs under **strict version pinning** as of Phase 5.5. The `:latest` tag still exists on Docker Hub (and `build_and_push.ps1` still pushes it for convenience), but no compose file in this repo references `:latest`. Every `image:` line in `infra/docker-compose.yml` and `infra/docker-compose.production.yml` pins to an explicit `X.Y.Z` tag, and operator-deployed composes are expected to do the same.

The full version-bump workflow when shipping a new image:

1. **Pick a version.** Patch bumps for bugfixes (0.9.7 → 0.9.8). Minor bumps for features (0.9.x → 0.10.0). Pre-1.0 is current; major bump comes when Phase 6 closes URSA-OSCAR's feature set.

2. **Build + push the image with both tags.** The build script tags `:VERSION` and `:latest`:
   ```powershell
   .\infra\build_and_push.ps1 -Version 0.9.8 -DockerHubUser brain40
   ```

3. **Update `infra/docker-compose.production.yml`** — change the `image:` line for the bumped container AND the matching `URSA_OSCAR_*_IMAGE_VERSION` env-chip default. Both must move together; the chip is what the Settings page displays and operator confusion erupts whenever they drift.

4. **Update `infra/docker-compose.yml`** — same change, here the version often lives in a `${URSA_OSCAR_*_IMAGE_VERSION:-X.Y.Z}` substitution. Keep the default in sync.

5. **Commit + push to public main.** Production compose is the canonical "what's the current shipped version?" reference; if it shows 0.9.8, that's the current version.

6. **Update the operator's local compose.** Either the operator does this manually (the typical case — the operator owns their Dockge file) or you provide a chat-side snippet they paste. The same two-line bump (image tag + env-chip default) applies.

7. **`docker compose pull && docker compose up -d --force-recreate`** on the operator's host.

8. **Verify version chips on `/settings`** match the new version. If they don't match the deployed image tags, something is wrong and won't be obvious from logs alone.

The "Settings chip drift" anti-pattern caught the operator twice during the 0.9.x patch cycle — they'd bumped the `image:` tag but not the env var, so the Settings page kept reporting the old version. Strict pinning of both halves keeps that from happening.

For experimental local builds where you don't care about the chip accuracy, build with `-SkipPush` and `docker compose up` against a one-off pinned tag (e.g. `:0.9.8-dev1`). Just don't push that tag to production until you've bumped through the full workflow above.

---

## 10. Deployment

See [`infra/docker-compose.yml`](../infra/docker-compose.yml) for the canonical production-stack definition. Designed for TrueNAS + Dockge but works on any Docker host.

### Minimum host requirements

- Docker 24+
- 2 GB RAM (the API container is the heaviest; budget ~1 GB for it under import load)
- 5 GB disk for images
- Whatever disk the operator's CPAP data needs (~50 MB per 100 nights with full waveforms)

### One-time deployment

1. Create a Docker network `kairos-net` (or rename references throughout the compose to your network of choice)
2. Bind-mount paths:
   - `<host-data-path>` → `/data` (DuckDB + JSON state)
   - `<host-cpap-path>` → `/cpap-import` (SD card mount, read-only)
3. Pull images: `docker pull brain40/ursa-oscar-{api,mcp,web,watcher}:<version>`
4. Bring up the stack with the env block (§11) filled in
5. **First-start operator action**: watch `docker logs ursa-oscar-api` for the line `URSA_OSCAR_SECRET_KEY is unset. Generated a fresh Fernet key…` — copy the key from `/data/secret_key.gen` into your compose env, delete the file, redeploy

### Public-facing URLs

The web container's nginx listens on 80 internally. Map it to a host port (default 5063 in the operator's setup) and put a TLS-terminating reverse proxy in front (Cloudflare Tunnel, nginx-proxy, Traefik, etc.).

The MCP server is intended to be public-facing (so Claude.ai can reach it). Map :8085 (or your choice), put TLS in front, and configure the URL in your Claude.ai MCP connector setup. See `Docs/17-oauth-setup.md` for OAuth client + URL configuration.

---

## 11. Configuration reference

All configuration is environment-variable-driven. Settings are read once at startup (or for each request that needs them) — there's no live-reload mechanism.

### API container (`ursa-oscar-api`)

| Env var | Default | Purpose |
|---|---|---|
| `URSA_OSCAR_DB_PATH` | `/data/ursa-oscar.duckdb` | DuckDB file path |
| `URSA_OSCAR_IMPORT_WATCH_PATH` | `/cpap-import` | (Legacy; the watcher reads this, but the API doesn't use it directly anymore) |
| `URSA_OSCAR_EXPORTS_PATH` | `/data/exports` | Where CSV exports land |
| `URSA_OSCAR_MCP_INTERNAL_URL` | `http://ursa-oscar-mcp:8000` | For the Settings page's MCP verify |
| `URSA_OSCAR_MCP_BASE_URL` | (set by operator) | Public-facing MCP URL — surfaced on Settings |
| `URSA_OSCAR_MCP_BEARER_TOKEN` | (set by operator) | Mirrored from MCP env for masking on Settings page |
| `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` | (set by operator) | Mirrored from MCP env |
| `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` | (set by operator) | Mirrored from MCP env |
| `URSA_OSCAR_SECRET_KEY` | (auto-generated on first start) | **Phase 5** — Fernet master key for `/data/secrets.enc`. If unset, generated to `/data/secret_key.gen`; operator copies into compose env and deletes file. |
| `URSA_OSCAR_JWT_SECRET` | (auto-generated on first start at `/data/jwt_secret`) | **Phase 6.4** — HS256 signing secret for operator JWTs. If unset, auto-generated to `/data/jwt_secret` (mode 0600) on first boot. Persisted across restarts. Same secret must be visible to the MCP container. |
| `URSA_OSCAR_*_IMAGE_VERSION` | `latest` | Per-container image version, surfaced on Settings page |

### MCP container (`ursa-oscar-mcp`)

| Env var | Default | Purpose |
|---|---|---|
| `URSA_OSCAR_API_URL` | `http://ursa-oscar-api:8000` | Where the MCP tools reach the API |
| `URSA_OSCAR_MCP_BEARER_TOKEN` | (set by operator) | Static bearer; matched against Authorization header |
| `URSA_OSCAR_MCP_BASE_URL` | (set by operator) | Public URL — used in OAuth metadata response |
| `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` | (set by operator) | OAuth client ID (set in Claude.ai connector config too) |
| `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` | (set by operator) | OAuth client secret |
| `URSA_OSCAR_JWT_SECRET` | (falls back to `/data/jwt_secret` from shared volume) | **Phase 6.4** — same secret as the API container, used to verify operator JWTs as a third bearer kind on `/sse` (in addition to OAuth + static bearer). When unset, MCP reads `/data/jwt_secret` from its read-only `/data` mount. |
| `URSA_OSCAR_MCP_API_TOKEN` | (set by operator via web UI) | **Phase 6.4** — 90-day JWT the MCP container attaches to its OUTGOING backend calls. Required when paired with API ≥ 0.13.0; generated via the URSA-OSCAR web UI Settings → Account → Generate API Token. |

### Watcher container (`ursa-oscar-watcher`)

| Env var | Default | Purpose |
|---|---|---|
| `URSA_OSCAR_API_URL` | `http://ursa-oscar-api:8000` | Where to enqueue jobs |
| `URSA_OSCAR_WATCH_PATH` | `/cpap-import` | Tree to monitor |
| `URSA_OSCAR_POLL_INTERVAL` | `30` (seconds) | How often to scan |
| `URSA_OSCAR_QUIESCENCE_SECONDS` | `30` | Stability window before triggering |
| `URSA_OSCAR_IMPORT_WEBHOOK_URL` | (unset → no webhook) | POSTed on each successful import |
| `URSA_OSCAR_FORCE_REIMPORT` | `false` | When true, every auto-import passes `?force=true` |
| `URSA_OSCAR_JOB_WAIT_TIMEOUT` | `600` (seconds) | Release tracker if a job hangs |
| `URSA_OSCAR_LOG_LEVEL` | `INFO` | Python logging level |
| `URSA_OSCAR_WATCHER_TOKEN` | (set by operator via web UI) | **Phase 6.4** — 90-day JWT attached as `Authorization: Bearer <token>` on every API call. Required when paired with API ≥ 0.13.0; generated via the URSA-OSCAR web UI Settings → Account → Generate API Token. NOT forwarded to webhook URLs (external endpoints don't see the internal credential). |

### Web container (`ursa-oscar-web`)

No env vars — pure static-content + reverse-proxy container. All wire URLs hardcoded in `nginx.conf` to use Docker DNS service names.

---

## 12. Adding a new feature

### Adding a new AI tool

Suppose you want to add `get_session_breakdown` (already exists as an MCP tool; pretend it doesn't for the example).

1. **Add the API endpoint** in `backend/src/ursa_oscar/api/<module>.py`. Return `{ok: true, data: ...}` envelope semantics. Add tests in `backend/tests/integration/test_api_endpoints.py`.

2. **Add the AI tool descriptor** in `backend/src/ursa_oscar/ai_proxy/tools.py`:
   - Append a dict to `TOOL_DESCRIPTORS` with the OpenAI function-calling shape (name + description + parameters JSON schema)
   - **The description is the single most important thing for tool routing.** Include "Use when the user asks…" examples.
   - Append a routing entry to `_TOOL_ROUTING` pointing at the API endpoint. Use a `path` + `builder` pair for simple GETs; use a custom router function for shapes that need composing.

3. **Add the MCP tool wrapper** in `mcp-server/src/ursa_oscar_mcp/tools/<tool_name>.py`. Decorate with `@mcp.tool()`. Call `api_get(...)` or `api_post(...)`. Return `_ok(...)` / `_err(...)` envelopes. Add to `server.py`'s tool registration.

4. **Tests**:
   - Backend: round-trip test in `test_api_endpoints.py`
   - AI proxy: descriptor + dispatcher test in `test_ai_proxy.py`
   - MCP: tool function test in `mcp-server/tests/test_tools.py`

### Adding a new AI provider

1. **Add the preset** to `backend/src/ursa_oscar/ai_proxy/providers/presets.py`. If the new provider speaks OpenAI's `/v1/chat/completions` format, the `openai_compat` adapter handles it — just supply the endpoint + default models + auth header config.
2. If it needs a fundamentally different protocol, write a new adapter in `providers/<name>.py` that subclasses `ProviderAdapter`. Update `build_adapter()` in `ai_proxy/__init__.py` to route to it.
3. **Tests**: add to `test_ai_proxy.py::test_seven_presets_registered` (update count) + `test_only_claude_uses_claude_adapter` if needed + `test_build_adapter_routes_to_right_class` for a new adapter class.

### Adding a new chart panel to Daily View

1. If the data needs a new endpoint, add it under `backend/src/ursa_oscar/api/timeseries.py` (or wherever appropriate).
2. Add the new track config to the `tracks` array in `frontend/src/pages/Daily.tsx`.
3. Add the chart height to `CHART_HEIGHTS` and decide whether it belongs in the compact view's `COMPACT_HIDDEN` set.
4. Wire the data into the existing `getTimeseries` call's `series` parameter, or add a new fetch if the data isn't a same-axis time-series.

### Schema migration

1. **Update the DDL** in `backend/src/ursa_oscar/storage/schema.sql`. Use `CREATE TABLE IF NOT EXISTS` so re-runs are idempotent. For column additions, use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
2. **Bump** `SCHEMA_VERSION` in `migrations.py` and add an entry to `_VERSION_DESCRIPTIONS`.
3. **Add backfill** code in `apply_migrations()` if the new schema requires populating from existing data. Use `WHERE NOT EXISTS` patterns so the backfill is idempotent on existing databases.
4. **Add a test** that exercises the backfill against a v(N-1) seed.

---

## 13. Architectural decision records

The full ADRs are in [`Docs/architect-decisions/`](architect-decisions/). One-line summary of each:

- **ADR-001 — No Tailwind / no shadcn.** Hand-rolled CSS with a small set of utility classes. Rationale: dependency minimalism + the CSS surface is small enough that handcrafted reads better than a framework.
- **ADR-002 — MCP server template adoption.** The MCP server lifted its OAuth + bearer + FastMCP scaffold from APEX's template. Tracks where the duplication lives so improvements flow both ways.
- **ADR-003 — MCP is a thin proxy over API.** DuckDB's process-level file lock means only one container can open the DB. The API container owns it; the MCP server (and AI proxy, and watcher) reach the data via the API's HTTP surface.
- **ADR-004 — DuckDB cross-container concurrency.** Concrete rules around `db.serialized()` usage, RLock semantics, and which operations get wrapped vs. left alone.

---

## 14. Known limitations and Phase-N housekeeping

These are noted but not yet fixed:

- **`fastmcp` starlette pin** — `fastmcp==3.2.4` pins `starlette==1.0.0` which conflicts with FastAPI's expected starlette range. Production is fine (containers isolated); test setup uses sys.path manipulation. Refresh fastmcp when upstream relaxes.
- **Orphan excluded_sessions** — when a session_id no longer exists after a re-import (rare), the `excluded_sessions` row points at nothing. Low-frequency edge accepted in Phase 4.
- **Webhook idempotency on watcher restart** — `_tracked_job_id` state is per-process; restart could theoretically re-fire a webhook for a job already notified pre-restart.
- **Streamed multipart upload** — current code calls `UploadFile.read()` per file, buffering each file into memory. The 10 MB per-file cap bounds peak memory. Lift the cap by switching to `UploadFile.seek()` / `.read(chunk_size)`.
- **Conversation export** — Phase 5 chat history is localStorage-only. No "save this conversation" affordance yet.
- **Mobile chat layout** — 480px panel works on desktop; phones are cramped.
- **Token usage tracking** — both adapters surface `usage` blocks (input/output tokens) but the UI doesn't render a cost estimate.
- **Native Gemini SDK** — current Gemini preset uses Google's OpenAI-compat layer. If tool-calling reliability proves poor, add `google-generativeai` as a third adapter.
- **`datetime.utcnow()` deprecations** — one fixed in Phase 5; sweep for any remaining when adjacent code is touched.

---

If you found something here unclear or wrong, file an issue or open a PR. The maintainers welcome it — that's the point of GPL.

— URSA-OSCAR maintainers
