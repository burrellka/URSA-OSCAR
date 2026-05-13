# 14 — Current Architecture & File List

**Authoritative as of:** 2026-05-13 (image tag `0.3.1`)
**Phase:** Phase 2 complete (Web UI shipped, deployed, UI-driven import gate validated). Phase 3 pending architect greenlight.
**Supersedes:** the Phase 1 snapshot of this same file (image tag `0.1.3`, 2026-05-12). Spec sources remain URSA-OSCAR_Framework v1.0 + URSA-OSCAR_Design v1.2.

This is the operating description of URSA-OSCAR as it actually runs today: container topology, port map, env vars, auth surface, REST + MCP API, frontend route map, and a per-module repo tour. Mirrors APEX `docs/14-current-architecture-and-filelist.md` in structure and intent. When this disagrees with the spec docs, this document wins for "how it is right now"; the spec docs win for "how it should be" where v1 has gaps.

---

## 1. Topology

```
                          ┌──────────────────────────────────────┐
                          │  Cloudflare (TLS terminator)          │
                          │  your-public-host.example.com  │
                          └────────────────┬──────────────────────┘
                                           │  HTTPS
                                           ▼
                              ┌──────────────────────────┐
                              │  Cloudflare Tunnel       │
                              │  -> kairos-net           │
                              └─────────┬────────────────┘
                                        │
                                        ▼  ursa-oscar-mcp:8000
                ┌──────────────────────────────────────────┐
                │  ursa-oscar-mcp (FastMCP + SSE)          │
                │  :8000 OAuth 2.1 + bearer fallback       │
                │                                          │
                │  get_nightly_summary                     │
                │  get_ahi_breakdown                       │
                │  get_event_distribution_by_hour          │
                │  get_pressure_profile                    │
                │  get_leak_profile                        │
                │  get_session_breakdown                   │
                │  list_available_nights                   │
                │  trigger_import                          │
                └─────────────────────┬────────────────────┘
                                      │  HTTP (kairos-net DNS)
                                      ▼
                    ┌────────────────────────────────┐
                    │  ursa-oscar-api (FastAPI)      │
                    │  :8000 no host port            │
                    │  Pydantic v2 validation        │
                    │  Single DuckDB writer          │
                    │  RLock-serialized DB access    │
                    │   (ADR-004, 0.3.1+)            │
                    └────────────┬───────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────────────┐
                │  DuckDB (embedded, single file)      │
                │  /data/ursa-oscar.duckdb             │
                │  -> /srv/ursa-oscar/data │
                └──────────────────────────────────────┘
                                 ▲
                                 │  read+write
                                 │
                ┌────────────────┴─────────────────────┐
                │  ursa-oscar-watcher (Phase 4 scaffold)│
                │  no command yet — sleeps             │
                └──────────────────────────────────────┘

                ┌──────────────────────────────────────┐
                │  ursa-oscar-web (nginx + React SPA)  │
                │  :80 -> host 5063                    │
                │  React 18 + Vite 5 + uPlot + custom  │
                │  CSS (ADR-001). Proxies /api/ to     │
                │  ursa-oscar-api over kairos-net via  │
                │  Docker DNS resolver (avoids stale-  │
                │  IP cache across container restarts) │
                └──────────────────────────────────────┘

  Host-mounted volumes on TrueNAS:
    /srv/ursa-oscar/data         (DuckDB + backups + exports)
    /srv/ursa-oscar/cpap-import  (SD-card / DATALOG source)
```

### Containers (production)

All four run on `kairos-net` Docker bridge (joined per Decision 11). Pulled from Docker Hub `brain40/ursa-oscar-*`. Managed by Dockge on TrueNAS.

| Container | Image | Internal port | Host port | Volumes |
|---|---|---|---|---|
| `ursa-oscar-api` | `brain40/ursa-oscar-api:latest` | 8000 | none (internal-only) | `/data` (RW), `/cpap-import` (RO) |
| `ursa-oscar-mcp` | `brain40/ursa-oscar-mcp:latest` | 8000 | 8085 | none (HTTP client only) |
| `ursa-oscar-web` | `brain40/ursa-oscar-web:latest` | 80 | 5063 | none |
| `ursa-oscar-watcher` | `brain40/ursa-oscar-watcher:latest` | none | none | `/data` (RW), `/cpap-import` (RO) |

The API container is the **sole owner** of the DuckDB file. The MCP container reads/writes via API HTTP (Phase 1.3 refactor — see §12 of `12-audit-report.md`).

---

## 2. Port map

| Port | Where | Auth | Notes |
|---|---|---|---|
| `https://your-public-host.example.com/sse` | Cloudflare → `ursa-oscar-mcp:8000` | Bearer or OAuth 2.1 | claude.ai connector + MCP CLI |
| `http://192.168.x.x:8085/sse` | LAN → `ursa-oscar-mcp:8000` | Bearer or OAuth 2.1 | dev / debug bypass |
| `http://192.168.x.x:5063/` | LAN → `ursa-oscar-web:80` | none | Phase 1 placeholder UI (Phase 2 ships real frontend) |
| `5060` originally | — | — | Switched to 5063 because Chrome blocks 5060 as `ERR_UNSAFE_PORT` (SIP) |
| `8082` originally | — | — | Switched to 8085 because 8082 was taken by localrecall on kairos-net |
| LAN host 5065 → `:8000` (dev compose only) | LAN → `ursa-oscar-api:8000` | none | Per Decision 13. **Not exposed in production.** |

---

## 3. Environment variables

Set in Dockge's per-stack environment editor; **not** committed to source. `.env` at repo root is gitignored; `infra/.env.example` documents the structure.

| Container(s) | Var | Required? | Notes |
|---|---|---|---|
| mcp | `URSA_OSCAR_MCP_BEARER_TOKEN` | **yes** | Container exits with ERROR if missing. Static bearer for curl / Claude Desktop / Claude Code. Generate via `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| mcp | `URSA_OSCAR_MCP_BASE_URL` | **yes** | Public URL the server is reached at, e.g. `https://your-public-host.example.com`. Builds OAuth metadata URLs. Wrong value → claude.ai connector OAuth dance fails |
| mcp | `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` | **yes (v0.1.x+)** | The pre-registered OAuth client_id that claude.ai must enter in its connector dialog. DCR disabled. Generate via `python -c "import secrets; print(secrets.token_urlsafe(16))"` |
| mcp | `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` | **yes (v0.1.x+)** | Paired secret. Generate via `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| mcp | `URSA_OSCAR_API_URL` | recommended | Defaults to `http://ursa-oscar-api:8000` (kairos-net DNS). Override only if API container moves |
| api / watcher | `URSA_OSCAR_DB_PATH` | recommended | Defaults to `/data/ursa-oscar.duckdb` |
| api | `URSA_OSCAR_IMPORT_WATCH_PATH` | recommended | Defaults to `/cpap-import` |
| api | `URSA_OSCAR_EXPORTS_PATH` | recommended | Defaults to `/data/exports` |

---

## 4. Auth surface

### Web UI — none (Phase 1)

`ursa-oscar-web` serves a static HTML placeholder. No auth. The real Phase 2 frontend will add `auth_basic` (matching APEX's `apex-web` posture) or token-based auth.

### MCP — OAuth 2.1 + static-bearer fallback

Lifted verbatim from APEX template `mcp-server-architecture-template.md` per ADR-002.

- **Discovery metadata:** `/.well-known/oauth-authorization-server` (RFC 8414) and `/.well-known/oauth-protected-resource` (RFC 9728), auto-mounted by FastMCP from `URSA_OSCAR_MCP_BASE_URL`
- **Authorization endpoint:** `GET/POST /authorize` — `response_type=code` + PKCE (S256) + state. Rejects any `client_id` that isn't the env-pinned pre-registered one. Auto-approves the legitimate client (single-user, no consent screen). Returns 302 with `?code=...&state=...`
- **Token endpoint:** `POST /token` — exchanges `grant_type=authorization_code` (with `code_verifier`) or `grant_type=refresh_token` for an access_token + refresh_token. 1-hour access TTL. Constant-time client_secret compare via `hmac.compare_digest`
- **DCR: DISABLED.** `POST /register` is not mounted (`ClientRegistrationOptions(enabled=False)`). Single OAuth client pre-registered at startup from env vars. Per APEX template §4 + Doc 17 threat model
- **Resource gates:** `RequireAuthMiddleware` wraps `/sse` and `/messages/`. Validates via `provider.verify_token`. 401 responses include `WWW-Authenticate: Bearer resource_metadata=...` so claude.ai can discover OAuth endpoints

Static bearer backward compat: `mcp-server/src/ursa_oscar_mcp/auth.py:UrsaOscarOAuthProvider` overrides `verify_token` to also accept `URSA_OSCAR_MCP_BEARER_TOKEN` (constant-time compare via `hmac.compare_digest`). Curl, Claude Desktop, and Claude Code use this path.

**Required env vars:** `URSA_OSCAR_MCP_BEARER_TOKEN`, `URSA_OSCAR_MCP_BASE_URL`, `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`, `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`. Container exits at startup if any is missing.

Full claude.ai setup procedure in `docs/17-oauth-setup.md`.

### API — none

`ursa-oscar-api` has no host port in production. The only paths to it are through the MCP container (which is bearer-auth'd) or the dev-bypass port 5065 (LAN-only, not in production compose).

---

## 5. Data model

DuckDB schema deployed via `backend/src/ursa_oscar/storage/migrations.py`. Tables (per Design v1.1 § Data Model):

| Table | Purpose |
|---|---|
| `nightly_summary` | One row per night. AHI components, pressure stats, leak stats, equipment settings, session count. Primary key: `date`. |
| `nightly_events` | Individual respiratory events (CA / OA / A / H / RERA / LargeLeak). Indexed on `(date)` and `(event_type)`. |
| `pressure_timeseries`, `flow_timeseries`, `leak_timeseries`, `flow_limit_timeseries`, `tidal_volume_timeseries`, `minute_vent_timeseries`, `resp_rate_timeseries`, `snore_timeseries` | High-resolution per-night waveforms. **Not written by default** in Phase 1 (would inflate disk by ~3 MB/night). Phase 2 charting will populate via `include_timeseries=True` import flag. |
| `manual_logs` | Subjective logs (medications, symptoms, mood, alertness, notes). **Stubbed in Phase 1**; CRUD endpoints return 501. Wired in Phase 3. |
| `config` | Key-value settings table. |
| `import_log` | Append-only audit of import operations. |
| `schema_version` | Migration version tracking. Currently at v1. |

Sequences: `nightly_events_id_seq`, `manual_logs_id_seq`, `import_log_id_seq` (DuckDB doesn't auto-increment without explicit sequences).

---

## 6. REST API

Mounted in `backend/src/ursa_oscar/main.py` via APEX-style blueprints (one module per resource). Phase 1.5 added the `/api/v1` prefix uniformly. All endpoints implemented except where noted.

```
healthz            GET     /healthz                                  liveness probe
nights/            GET     /api/v1/nights?start&end                  list available nights
                   GET     /api/v1/night/{date}                      single night summary
events/            GET     /api/v1/events?date&event_type[]          events for a date, optional type filter
timeseries/        GET     /api/v1/timeseries/{date}?series[]        multi-channel waveform data
                                                                     epoch-ms timestamps, secondary EPAP on pressure
imports/           POST    /api/v1/imports                           trigger sync import (returns ImportLogEntry)
                   GET     /api/v1/imports/{id}                      404 — async jobs land in Phase 4
manual_logs/       GET/POST/PATCH/DELETE /api/v1/manual-logs[/{id}]  Phase 3 (CRUD stubs shape the surface)
exports/           GET     /api/v1/exports/{date}.csv                streaming per-night CSV (Phase 2)
                                                                     Content-Disposition: attachment
                                                                     URSA-OSCAR canonical schema
                   POST    /api/v1/exports                           404 — bulk/range export Phase 3
```

Schema documented at `/openapi.json` (auto-generated by FastAPI per Decision 12). Swagger UI at `/docs`.

Global exception handler in `backend/src/ursa_oscar/main.py` (matches APEX pattern — never returns the default HTML 500). All errors are JSON.

---

## 7. MCP tools

8 tools as of `v0.1.3` — full contract in [`03-mcp-tool-contract.md`](03-mcp-tool-contract.md). All tools call the API container over `kairos-net`.

### Read-only (7)

| Tool | Purpose |
|---|---|
| `get_nightly_summary(date, end_date?)` | Full nightly_summary record(s) — AHI, pressure, leak, equipment, sessions |
| `get_ahi_breakdown(date, end_date?)` | AHI per event type + clinical interpretation (TECSA flag, treatment-status grade) |
| `get_event_distribution_by_hour(date, event_types?)` | Per-hour event histogram for a single night |
| `get_pressure_profile(date, end_date?)` | Pressure percentiles + ceiling-hit flag |
| `get_leak_profile(date, end_date?)` | Leak percentiles + minutes-over-redline + seal-quality interpretation |
| `get_session_breakdown(date)` | Per-session event details for multi-session nights |
| `list_available_nights(start_date?, end_date?, filter_expression?)` | Calendar listing with optional simple predicate filter |

### Write — operational (1)

| Tool | Purpose |
|---|---|
| `trigger_import(source_path)` | Kicks off folder import via API. Idempotent on date. |

### Deferred

Tier 2 (`compare_periods`, `analyze_correlation`, `get_trend`, `get_manual_log_summary`) → Phase 3. Remaining Tier 3 (`inspect_schema`, `run_sql_query`, `get_import_status`, `add_manual_log`, `export_data`) → Phase 4.

---

## 8. Frontend

Phase 2 ships a real React 18 + TypeScript strict + Vite 5 frontend per ADR-001 (no Tailwind, no shadcn, no TanStack Query, raw fetch). Visual aesthetic via 417 lines of `index.css` lifted verbatim from APEX `web/src/index.css` plus a 73-line URSA-OSCAR additions block. Charts via uPlot.

Routes live as of `0.3.1`:
- `/` and `/overview` — Overview: GitHub-style 90-day AHI calendar heatmap with severity tiers + click-through to Daily.
- `/daily/{YYYY-MM-DD}` — Daily View: 4 summary tiles, "Waveforms · N events" header w/ Export CSV button, colored event rug, 7 stacked uPlot tracks (Pressure+EPAP, Leak, Flow Limit, Tidal Vol, Minute Vent, Resp Rate, Snore) with synchronized cursor + zoom, AHI breakdown + Equipment + event-counts sidebars, prev/next-night arrows.
- `/statistics` — time-window pills (7d / 30d / 90d / all), aggregate table over 8 metrics (mean/median/min/max/stddev), 6-tile histogram grid.
- `/events` — date selector, min-duration slider, event-type toggle pills, sortable table, URL-state-bound filters.
- `/import` — server-side path text input, "Start import" button, result tile w/ deep-link to latest imported night.
- `/trends` — Phase 3 placeholder (real `/api/v1/trends` shape comes with the `get_trend` Tier-2 MCP tool).
- `/manual-logs` — Phase 3 placeholder.
- `/settings` — Phase 3 placeholder (device + import config).

nginx in the web container proxies `/api/` and `/healthz` to `ursa-oscar-api:8000` using the Docker DNS resolver pattern (`resolver 127.0.0.11; set $upstream http://ursa-oscar-api:8000`) — avoids the stale-IP-cache bug that bites every kairos-net container that hard-codes upstream IPs.

---

## 9. NAS file layout

```
/srv/ursa-oscar/
├── data/
│   ├── ursa-oscar.duckdb              live DB
│   ├── ursa-oscar.duckdb.wal          write-ahead log (auto-checkpointed on graceful shutdown)
│   ├── backups/                       daily snapshots (Phase 4 cron)
│   └── exports/                       generated export bundles (Phase 2/3)
└── cpap-import/                       SD-card / DATALOG source (RO mount for api)
    ├── 20260507/                      AirSense 11 DATALOG dirs
    ├── 20260508/
    ├── STR.edf                        per-card summary (optional; populates equipment fields)
    ├── Identification.json/.crc       machine ID
    ├── journal.jnl                    firmware log
    └── SETTINGS/
        └── CurrentSettings.json/.crc  current prescription
```

EDF parsing handles ResMed's quirks: annotation-only EDF+D files (CSL.edf, EVE.edf) via a custom TAL parser; high-resolution waveform files (BRP.edf, PLD.edf, SA2.edf) via MNE. pyedflib alone refuses these files. See `backend/src/ursa_oscar/analytics/edf_parser.py`.

---

## 10. Build + deploy

```powershell
# Build all 4 images, tag :version + :latest, push to Docker Hub:
.\infra\build_and_push.ps1 -Version 0.1.3

# Skip the push (build only):
.\infra\build_and_push.ps1 -Version 0.1.3 -SkipPush
```

Mirrors APEX's `infra/build_and_push.ps1` (same param shape, service-array loop). Service array:

```
ursa-oscar-api      backend/Dockerfile
ursa-oscar-mcp      mcp-server/Dockerfile
ursa-oscar-web      frontend/Dockerfile
ursa-oscar-watcher  watcher/Dockerfile
```

Image tags follow semver. `:latest` is repointed on every push. Kevin pulls via Dockge by clicking Update on the `ursa-oscar` stack.

### Local dev

```bash
cd infra
docker compose -f docker-compose.dev.yml up --build
```

Spins up `ursa-oscar-api` (uvicorn with `--reload`), `ursa-oscar-mcp`, `ursa-oscar-web`, `ursa-oscar-watcher`. Source mounts enable hot reload for the API + MCP. Dev compose exposes the LAN dev-bypass port `5065` on the API container (Decision 13) for curl-driven validation without auth headers.

---

## 11. Repo file list

```
C:\dev\URSA-OSCAR\
├── Docs\
│   ├── URSA-OSCAR_Framework.md                v1.0 — scope + intent
│   ├── URSA-OSCAR_Design.md                   v1.1 — locked build spec (15 architect decisions)
│   ├── URSA-OSCAR_Phase0_Kickoff.md           Phase 0 kickoff (codebase inspection)
│   ├── URSA-OSCAR_Phase1_Kickoff.md           Phase 1 kickoff (build + verify)
│   ├── 03-mcp-tool-contract.md                this directory — current MCP tool surface
│   ├── 12-audit-report.md                     Phase 1 completion audit
│   ├── 14-current-architecture-and-filelist.md (this file)
│   ├── 15-build-handover.md                   Phase 1 narrative + Phase 2 prep
│   ├── 17-oauth-setup.md                      claude.ai connector runbook
│   ├── architect-decisions\
│   │   ├── adr-001-frontend-stack.md          inherit APEX's React + custom CSS (no Tailwind)
│   │   ├── adr-002-mcp-server-template-adoption.md   lift APEX template wholesale
│   │   ├── phase-0-apex-findings.md           APEX inspection
│   │   ├── phase-0-fitbitkb-findings.md       fitbitkb inspection
│   │   └── phase-0-synthesis.md               conflicts + resolutions
│   ├── api\
│   │   └── openapi.yaml                       auto-generated from FastAPI (regenerate per release)
│   ├── mcp\
│   │   └── tool-surface.md                    auto-generated from MCP tool docstrings (planned)
│   └── visual-preference\                     APEX screenshots Kevin referenced for theme
│
├── backend\
│   ├── Dockerfile
│   ├── pyproject.toml                         FastAPI, DuckDB, pyedflib, MNE, pandas, Pydantic v2
│   ├── src\
│   │   └── ursa_oscar\
│   │       ├── main.py                        FastAPI factory + lifespan-managed DuckDB
│   │       ├── config.py                      pydantic.BaseSettings
│   │       ├── api\
│   │       │   ├── nights.py                  GET /api/v1/nights, /api/v1/night/{date}
│   │       │   ├── events.py                  GET /api/v1/events
│   │       │   ├── timeseries.py              GET /api/v1/timeseries/{date}?series[] (Phase 2)
│   │       │   ├── manual_logs.py             Phase 3 stub
│   │       │   ├── imports.py                 POST /api/v1/imports (include_timeseries default True since 0.3.0)
│   │       │   ├── exports.py                 GET /api/v1/exports/{date}.csv (Phase 2)
│   │       │   └── health.py                  GET /healthz
│   │       ├── analytics\                     OSCAR-equivalent analytics
│   │       │   ├── edf_parser.py              EDF+D + custom TAL parser; MNE waveform reader
│   │       │   ├── event_detector.py          pass-through from EVE.edf with signal enrichment
│   │       │   ├── leak_detector.py           leak metrics + LargeLeak event detection from PLD
│   │       │   ├── session_analyzer.py        per-session aggregation
│   │       │   ├── settings_parser.py         Identification.json + SETTINGS/CurrentSettings.json (Phase 1.5)
│   │       │   └── summary_builder.py         night-level aggregate (handles Decision 8 quirk)
│   │       ├── ingestion\
│   │       │   ├── airsense11_layout.py       SD-card / DATALOG-flat detection
│   │       │   ├── importer.py                main import pipeline + CLI entry
│   │       │   │                              vectorized time-series writes (0.3.0+, 848s -> 11s)
│   │       │   └── watcher.py                 Phase 4 scaffold (raises NotImplementedError)
│   │       ├── storage\
│   │       │   ├── db.py                      DuckDBManager — RLock-serialized (ADR-004, 0.3.1+)
│   │       │   │                              _MaterializedResult + db.serialized() contextmanager
│   │       │   ├── migrations.py              schema_version + idempotent CREATE (RLock-aware)
│   │       │   ├── schema.sql                 full DDL (v1)
│   │       │   └── repositories\
│   │       │       ├── nights.py              upsert / get_by_date / list_in_range — uses db.serialized()
│   │       │       ├── events.py              bulk_insert / list_for_date / count_for_date — uses db.serialized()
│   │       │       ├── timeseries.py          pandas DataFrame INSERT-SELECT bulk writer + range_query
│   │       │       └── manual_logs.py         Phase 3 stubbed methods (RLock-aware)
│   │       └── models\
│   │           └── domain.py                  Pydantic v2 models for wire types
│   └── tests\
│       ├── conftest.py                        FIXTURE_ROOT + temp_db fixture
│       ├── unit\
│       │   ├── test_storage_roundtrip.py      6 tests — repos
│       │   ├── test_edf_parser.py             6 tests — TAL parser + waveforms
│       │   └── test_importer.py               3 tests — acceptance criteria 6 + 7
│       ├── integration\
│       │   └── test_api_endpoints.py          10 tests — FastAPI smoke
│       └── regression\
│           ├── canonical_targets.py           Decision 15 targets (corrected per audit)
│           ├── test_oscar_parity.py           24 tests — the 1%-match acceptance gate
│           └── fixtures\
│               └── nights\oscar-reference\    4-night fixture set + STR.edf + SETTINGS
│
├── mcp-server\
│   ├── Dockerfile                             CMD: python -m ursa_oscar_mcp (not .server)
│   ├── pyproject.toml                         fastmcp==3.2.4, mcp==1.27.0, httpx (no DuckDB!)
│   ├── src\
│   │   └── ursa_oscar_mcp\
│   │       ├── __main__.py                    uvicorn entry — avoids __main__/import duality
│   │       ├── server.py                      FastMCP instantiation + tool registration
│   │       ├── auth.py                        UrsaOscarOAuthProvider + env-driven config
│   │       ├── envelope.py                    _ok / _err helpers from APEX template §6.1
│   │       ├── helpers.py                     _iso / _coerce_datetime_fields / _safe_path
│   │       ├── client.py                      api_get / api_post — kairos-net HTTP proxy
│   │       └── tools\                         one file per tool
│   │           ├── nightly_summary.py
│   │           ├── ahi_breakdown.py
│   │           ├── event_distribution.py
│   │           ├── pressure_profile.py
│   │           ├── leak_profile.py
│   │           ├── session_breakdown.py
│   │           ├── list_nights.py
│   │           └── trigger_import.py
│   └── tests\
│       ├── verification\
│       │   └── test_auth_boundary.py          6 tests — Starlette TestClient auth boundary
│       └── test_tools.py                      12 tests — currently skipped (API-fixture pending)
│
├── frontend\                                   Phase 2: React 18 + Vite 5 + TypeScript strict
│   ├── Dockerfile                             multi-stage: node:20 build -> nginx:alpine serve
│   ├── nginx.conf                             Docker DNS resolver pattern (avoids stale-IP cache); proxies /api/, /healthz
│   ├── package.json                           react, react-router-dom 6, lucide-react, uplot
│   ├── tsconfig.json + tsconfig.node.json     strict; pythonpath-like alias for src/
│   ├── vite.config.ts
│   ├── index.html                             Vite entry
│   └── src\
│       ├── main.tsx                           React root + BrowserRouter
│       ├── App.tsx                            route table (Layout shell + 8 nested routes)
│       ├── index.css                          417 lines lifted verbatim from APEX + 73 URSA-OSCAR additions
│       ├── api\
│       │   ├── client.ts                      typed fetch wrapper + ApiError + epoch-ms->sec conversion for uPlot
│       │   └── types.ts                       NightlySummary / NightlyEvent / TimeseriesResponse / ImportLogEntry
│       ├── components\
│       │   ├── Layout.tsx                     sidebar nav + outlet
│       │   ├── CalendarHeatmap.tsx            GitHub-style 90-day grid, AHI severity tiers
│       │   ├── Histogram.tsx                  lightweight SVG histogram (no chart lib)
│       │   ├── TimeSeriesChart.tsx            uPlot wrapper w/ ResizeObserver + cursor.sync
│       │   └── EventRug.tsx                   uPlot plugin painting colored ticks at event timestamps
│       └── pages\
│           ├── Overview.tsx                   calendar heatmap + tile summary
│           ├── Daily.tsx                      summary tiles + event rug + 7 stacked TimeSeriesCharts + AHI / Equipment sidebars + Export CSV
│           ├── Statistics.tsx                 time-window selector + aggregate table + 6-tile histogram grid
│           ├── Events.tsx                     date / min-duration / type filters + sortable table + URL-state binding
│           ├── Import.tsx                     server-path text input + POST /api/v1/imports + result tile
│           ├── Trends.tsx                     Phase 3 placeholder
│           ├── ManualLogs.tsx                 Phase 3 placeholder
│           └── Settings.tsx                   Phase 3 placeholder
│
├── watcher\
│   ├── Dockerfile                             idle CMD; Phase 4 fills in the loop
│   ├── pyproject.toml                         watchdog, httpx
│   └── src\
│       └── ursa_oscar_watcher\
│           └── __init__.py                    empty
│
├── infra\
│   ├── docker-compose.yml                     production reference (Docker Hub pull, kairos-net)
│   ├── docker-compose.dev.yml                 local build + hot reload + LAN-bypass port 5065
│   ├── docker-compose.production.yml          TrueNAS-specific paths, json-file logging caps
│   ├── build_and_push.ps1                     PowerShell builder mirroring APEX's
│   ├── verify-mcp-live.sh                     4 curl checks per APEX template §7.4
│   └── .env.example                           committed template; .env at repo root is gitignored
│
├── data\                                      runtime; gitignored
│   ├── ursa-oscar.duckdb
│   ├── backups\
│   └── exports\
│
├── Makefile                                   make up / down / dev / build / test / verify-mcp / verify-mcp-live / import / backup / restore
├── README.md
├── .env                                       gitignored
├── .env.example -> infra/.env.example         (no symlink; .env.example lives in infra/)
└── .gitignore
```

---

## 12. What's deferred from the v1 spec

| Item | Spec doc | Why deferred |
|---|---|---|
| Watcher container's actual file-watch logic | Design v1.2 § Phase 4 | Scaffold container present; CMD is `sleep 2^31` |
| Tier 2 MCP tools (`compare_periods`, `analyze_correlation`, `get_trend`, `get_manual_log_summary`) | Design v1.2 § MCP Tool Surface | Phase 3 |
| Tier 3 MCP tools (`inspect_schema`, `run_sql_query`, `get_import_status`, `add_manual_log`, `export_data`) | Design v1.2 § MCP Tool Surface | Phase 4 |
| Manual logging (write CRUD + UI) | Design v1.2 § Phase 3 | Phase 3 — `/api/v1/manual-logs` stubs shape the surface, UI route exists as placeholder |
| Daily / monthly DuckDB backups via cron | Design v1.2 § Decision 2 | Phase 4 (`make backup` target exists for on-demand) |
| Trends page real content | Design v1.2 § Phase 2 / 3 | UI route present as placeholder; backend `/api/v1/trends` not yet defined (needs `get_trend` Tier-2 shape) |
| Client-side folder upload for Import | Phase 2 review (Kevin's 2026-05-13 ask, clarified) | Phase 2.5 **recommended**. User picks the DATALOG folder from their laptop (SD card in card reader) via native file dialog; browser uploads the tree; server runs the existing importer against a temp dir. New `POST /api/v1/imports/upload` (multipart) + `<DirectoryUpload>` React component using `<input type="file" webkitdirectory>` + streaming progress bar + tempdir cleanup. Keeps the current typed-path input as the on-server-via-SMB fallback. ~1.5–2 days. Removes the SMB-copy step that's currently the friction for any non-test import. |
| Bulk / range CSV export | Phase 2 review (Kevin's 2026-05-13 ask) | Phase 3 alongside `export_data` Tier-3 MCP tool. ~half a day for the multi-night-summary CSV variant. |
| `correlate_with_external` (Fitbit / APEX cross-source) | Framework v1.0 | Decision 6 — agent-mediated, not server-side. Stays deferred forever. |
| MCP tool integration tests with running API container | Design v1.2 § Phase 1 | Still skipped via `pytestmark.skip`. Tracked for Phase 2.5 / 3 — fixture needs API container + seeded DuckDB. |
| `nightly_events_id_seq` desync after partial-rollback import | ADR-004 follow-up | Workaround: wipe + reimport. Correct fix: allocate id inside same transaction as INSERT. |
| `importer.py` exception-swallowing via `try/raise/finally: return` | ADR-004 follow-up | Constraint errors reach HTTP caller via `error_message` but never log. Phase 3 ingestion hardening. |
| Frontend tests (Vitest / Playwright) | Phase 2 review | Phase 3 should establish a Playwright smoke suite covering Overview → Daily → Events → Import. |

**Resolved since Phase 1 snapshot:**
- Real React frontend (Phase 2, shipped in `0.3.x`).
- Equipment-setting fields in `nightly_summary` (Phase 1.5, shipped in `0.2.0`).
- Time-series tables populated on import (Phase 2, `include_timeseries=True` default since `0.3.0`).
- DuckDB cross-container lock (Phase 1.5 ADR-003 — MCP is now an API-proxy).
- DuckDB cross-thread cursor corruption inside the API process (Phase 2 ADR-004 — RLock serialization shipped in `0.3.1`).

---

## 13. Pointers

- Phase 1 audit + completion summary: `Docs/12-audit-report.md`
- Phase 1 build narrative + commit-by-commit story: `Docs/15-build-handover.md`
- Phase 2 build narrative + commit-by-commit story: `Docs/16-phase-2-build-handover.md`
- claude.ai connector setup (one-time): `Docs/17-oauth-setup.md`
- Repo-root entry point: `README.md`
- Per-domain authoritative spec: `URSA-OSCAR_Design.md` (v1.2), `URSA-OSCAR_Framework.md` (v1.0)
- ADR index: `Docs/architect-decisions/`
  - `adr-001-frontend-stack.md` — React + Vite + uPlot + APEX-CSS over Tailwind / shadcn
  - `adr-002-mcp-server-template-adoption.md` — Lift APEX MCP template wholesale
  - `adr-003-mcp-as-api-proxy.md` — MCP container reads via API HTTP, not direct DuckDB (cross-process lock)
  - `adr-004-duckdb-rlock.md` — Process-wide RLock serializes DuckDB access (cross-thread cursor safety)
