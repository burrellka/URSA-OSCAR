# URSA-OSCAR

Self-hosted CPAP analytics platform with built-in AI assistant. Replaces OSCAR's desktop-only workflow with a homelab-deployed service you can analyze conversationally — bring-your-own-key for Claude, OpenAI, Gemini, OpenRouter, Groq, or a local LLM.

**Status:** Phase 5 complete. Current image tags:
- `brain40/ursa-oscar-api:0.9.1`
- `brain40/ursa-oscar-web:0.9.0`
- `brain40/ursa-oscar-mcp:0.7.0`
- `brain40/ursa-oscar-watcher:0.9.0`

**License:** [GNU GPL-3](LICENSE). See [COPYRIGHT](COPYRIGHT) for the project copyright notice and the OSCAR-project acknowledgement.

---

## What URSA-OSCAR does

A self-hosted CPAP analytics stack that:

- **Ingests** ResMed AirSense 11 SD-card exports (DATALOG/, EVE.edf, BRP/PLD/SA2.edf, STR.edf, SETTINGS/CurrentSettings.json) into a single embedded DuckDB
- **Computes** per-night summaries — AHI broken into central / obstructive / hypopnea / RERA components, pressure / leak percentiles, mask-on time, equipment settings — with OSCAR-equivalent clinical math
- **Serves** a React + uPlot web UI for daily / overview / events / trends / data management
- **Exposes** an MCP server (FastMCP + SSE, OAuth 2.1 + PKCE + static bearer fallback) so Claude.ai (and similar) can call `get_nightly_summary`, `compare_periods`, `analyze_correlation`, `get_trend`, and 7 more tools
- **Brings the agent experience in-app** via a chat panel on Daily View — configure any of seven LLM providers in Settings; conversations stay in your browser, API keys are encrypted at rest server-side
- **Automates re-imports** via a file-watcher daemon that detects new DATALOG dirs, waits for filesystem quiescence, and triggers an async import job — optionally firing a webhook on completion for downstream automation

**Compared to OSCAR's prior art:**
- Network-accessible service (Docker on a NAS), not a desktop application
- AI-assistant integration via MCP for conversational analysis
- Subjective + objective correlation (manual logging + correlation tools)
- Async import queue + hands-off auto-import + webhook fanout

---

## Quick start

### Pull the images

```bash
docker pull brain40/ursa-oscar-api:0.9.1
docker pull brain40/ursa-oscar-web:0.9.0
docker pull brain40/ursa-oscar-mcp:0.7.0
docker pull brain40/ursa-oscar-watcher:0.9.0
```

### Deploy with Docker Compose

See [`infra/docker-compose.yml`](infra/docker-compose.yml). Tailor the env block:

```yaml
URSA_OSCAR_IMAGE_VERSION: 0.9.1
URSA_OSCAR_MCP_IMAGE_VERSION: 0.7.0
URSA_OSCAR_WEB_IMAGE_VERSION: 0.9.0
URSA_OSCAR_WATCHER_IMAGE_VERSION: 0.9.0

# MCP secrets — Claude.ai connector configuration. See Docs/17-oauth-setup.md.
URSA_OSCAR_MCP_BEARER_TOKEN: <generate via `openssl rand -base64 32`>
URSA_OSCAR_MCP_OAUTH_CLIENT_ID: <generate>
URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET: <generate>
URSA_OSCAR_MCP_BASE_URL: https://your-mcp-public-url.example

# Phase 5 — Fernet master key for at-rest API key encryption.
# Leave unset on first boot; the API generates one to /data/secret_key.gen
# with mode 0600, then logs an operator action line. Copy the value here,
# delete the .gen file, redeploy.
URSA_OSCAR_SECRET_KEY:

# Optional — auto-import webhook (Phase 4). POSTed after each successful
# import. Wire to ntfy, Slack, Home Assistant, etc.
URSA_OSCAR_IMPORT_WEBHOOK_URL:
```

Bring it up:

```bash
docker compose up -d
```

Then visit the web UI at the port you mapped (default 5063).

### First-start operator action

The API logs a warning on its first boot:

```
URSA_OSCAR_SECRET_KEY is unset. Generated a fresh Fernet key and wrote
it to /data/secret_key.gen. Copy this value into your compose env as
URSA_OSCAR_SECRET_KEY=<value>, then delete /data/secret_key.gen.
```

Do that, redeploy. Subsequent boots use the persisted key to decrypt the API keys you'll add for the AI assistant.

### Configure an LLM (optional but recommended)

1. Open the web UI → Settings → **AI Assistant** (`/settings/ai`)
2. Pick a provider (Claude / OpenAI / Gemini / OpenRouter / Groq / Local LLM / Custom)
3. Paste an API key (your own — URSA-OSCAR is bring-your-own-key)
4. Test connection → save → enable
5. Open Daily View → click "Ask URSA"

API keys are stored encrypted at rest. Conversations stay in your browser. Tool calls execute against your own DuckDB; the LLM only sees what the tools return.

---

## Architecture

One sentence: Python 3.11 + FastAPI + DuckDB + FastMCP-over-SSE + React 18 + TypeScript + Vite, deployed as four containers on Docker Compose with bind-mounted state on a NAS.

For depth see:

- [**`Docs/30-developer-guide.md`**](Docs/30-developer-guide.md) — the maintainer's-eye view: repo layout, container roles, request walkthroughs, schema, build/test/deploy, how to add a new feature
- [`Docs/URSA-OSCAR_Design.md`](Docs/URSA-OSCAR_Design.md) — authoritative product + design spec
- [`Docs/URSA-OSCAR_Framework.md`](Docs/URSA-OSCAR_Framework.md) — vision + product framing
- [`Docs/03-mcp-tool-contract.md`](Docs/03-mcp-tool-contract.md) — MCP tool envelope spec
- [`Docs/17-oauth-setup.md`](Docs/17-oauth-setup.md) — connecting an AI client over MCP
- [`Docs/architect-decisions/`](Docs/architect-decisions/) — ADRs: no Tailwind, MCP-as-thin-proxy, DuckDB concurrency rules, etc.

### Container map

```
                              kairos-net
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                │
   │   ┌──────────────────────┐                                     │
   │   │  ursa-oscar-api      │  ←─── sole owner of DuckDB          │
   │   │  :8000 (internal)    │       writer; lifespan-managed      │
   │   │  ┌──────────────┐    │                                     │
   │   │  │ DuckDB file  │    │                                     │
   │   │  │ + JSON files │    │                                     │
   │   │  │ + secrets.enc│    │                                     │
   │   │  └──────────────┘    │                                     │
   │   └──▲────────▲──────────┘                                     │
   │      │HTTP    │HTTP                                            │
   │      │        │                                                │
   │   ┌──┴─────┐  │  ┌────────────────┐   ┌──────────────────┐    │
   │   │ MCP    │  └──│ web (nginx)    │←──│ watcher (daemon) │    │
   │   │ FastMCP│     │ /api/* proxy   │   │ /cpap-import     │    │
   │   │ + OAuth│     │ + SPA static   │   │ poll + webhook   │    │
   │   └────────┘     └───────▲────────┘   └──────────────────┘    │
   │                          │                                     │
   └──────────────────────────┼─────────────────────────────────────┘
                              │
                          Browser
                          (chat panel + Daily View)
```

---

## Development

Full developer guide at [`Docs/30-developer-guide.md`](Docs/30-developer-guide.md). Quick start:

```bash
git clone https://github.com/burrellka/URSA-OSCAR.git
cd URSA-OSCAR

# Backend
cd backend && pip install -e ".[dev]" && cd ..

# Frontend
cd frontend && npm install && cd ..

# Run the test suites (~7-8 minutes total)
cd backend && pytest --ignore=tests/regression  # 135 tests
cd mcp-server && pytest                          # 18 tests
cd watcher && pytest                             # 13 tests
```

For a hot-reload dev loop, run the API + Vite dev server separately — the dev guide covers the env vars.

### Build + push

```powershell
# Build all four images at version X.Y.Z, push to Docker Hub
.\infra\build_and_push.ps1 -Version 0.9.1 -DockerHubUser brain40

# Build only, no push
.\infra\build_and_push.ps1 -Version 0.9.1 -SkipPush
```

---

## Phase history (high-level)

- **Phase 1** — ingestion pipeline, OSCAR-parity nightly summaries, REST surface
- **Phase 2** — React + uPlot web UI, daily/overview/events/statistics pages, Docker Compose deploy
- **Phase 3** — analytics (compare_periods, analyze_correlation, get_trend), manual logging, hard-delete purge, bulk export, browser folder upload, sessions table, session-exclusion toggle
- **Phase 4** — async import queue, file-watcher daemon with webhook, DeviceClock display offset (handles ResMed's no-DST behavior), chart sizing polish
- **Phase 5** — In-app AI chat panel + 7-provider preset registry (Claude + OpenAI + Gemini + OpenRouter + Groq + Local + Custom), 11 LLM tools, Fernet-encrypted secret storage, MCP fixture tests unblocked

---

## Acknowledgements

URSA-OSCAR ports event-detection and analytics concepts from the [OSCAR project](https://www.sleepfiles.com/OSCAR/) (Open Source CPAP Analysis Reporter), which is itself GPL-licensed. The nightly aggregation, AHI computation, and pressure / leak / event handling owe their correctness to OSCAR's prior art.

Not affiliated with ResMed, Anthropic, OpenAI, Google, Apple, or the OSCAR project.

---

## Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. **Never commit PII, real CPAP recordings, or unredacted screenshots** — the project ships with anonymized targets only and any operator-specific data is gitignored.

For non-trivial changes, open an issue first to discuss the approach. The developer guide ([Docs/30-developer-guide.md](Docs/30-developer-guide.md)) is the easiest entry point for understanding how a change spans the four containers.
