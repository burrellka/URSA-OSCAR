# Architecture overview

URSA-OSCAR is four Docker containers sharing a `/data` volume. This page is the high-level mental model — what each container does, how they talk to each other, and where your data lives.

## The four containers

```
                              ┌──────────────────────┐
                              │  ursa-oscar-api      │
                              │  FastAPI / Python    │
                              │  Sole DuckDB writer  │
                              │  ┌─────────────────┐ │
                              │  │ /data/*.duckdb  │ │
                              │  └─────────────────┘ │
                              └▲────────▲──────▲─────┘
                               │        │      │
                  ┌────────────┘        │      └───────────────┐
                  │ HTTP                │ HTTP                  │ HTTP
                  │                     │                       │
        ┌─────────┴─────────┐  ┌────────┴──────────┐  ┌─────────┴──────────┐
        │ ursa-oscar-web    │  │ ursa-oscar-mcp    │  │ ursa-oscar-watcher │
        │ nginx + React     │  │ FastMCP / SSE     │  │ Python daemon      │
        │ Port 5063 (host)  │  │ Port 8085 (host)  │  │ Bind-mount watcher │
        └───────────────────┘  └───────────────────┘  └────────────────────┘
                  ▲                     ▲                       │
                  │ HTTPS               │ HTTPS                 │ File system
                  │                     │                       │
                  │              ┌──────┴──────┐         ┌──────┴──────┐
                  │              │ claude.ai   │         │ CPAP card / │
              Browser            │ Connector   │         │ network drop│
                                 └─────────────┘         └─────────────┘
```

### ursa-oscar-api

The center of gravity. FastAPI + Python. Owns the DuckDB file. Every other container talks to the API over HTTP; nobody else opens DuckDB directly (per ADR-004's single-writer rule).

What lives here:

- The REST API surface (`/api/v1/*`)
- The ingestion pipeline (parse EDFs, write to DuckDB)
- The analytical compute (correlations, trends, predictions)
- The PDF report generator (WeasyPrint)
- The AI proxy (multi-provider chat + SSE streaming)
- The authentication layer (Argon2id passwords, JWT sessions, service tokens)
- The OAuth + secrets surfaces for MCP setup

### ursa-oscar-mcp

The MCP (Model Context Protocol) server. FastMCP + SSE + OAuth 2.1 + PKCE. Exposes 17 MCP tools so AI assistants outside URSA-OSCAR (claude.ai's Custom Connector, Claude Desktop, MCP CLIs, etc.) can query your CPAP data.

The MCP container doesn't have its own data store. It's a thin proxy: when an MCP tool is invoked, the container makes an HTTP call to the api container, transforms the response into MCP's envelope shape, and sends it back to the AI client.

### ursa-oscar-web

Nginx + the React UI bundle. Static-content container. Routes browser requests:

- `/api/v1/*` → proxied to the api container
- Everything else → serves the React SPA

The container has no API logic of its own. Replacing the SPA bundle (e.g., for a fork) is just rebuilding this image.

### ursa-oscar-watcher

A Python daemon that polls the bind-mounted CPAP source directory. When it detects new files (a refreshed SD card, an rsync'd backup), it waits for the file tree to be quiescent for 30 seconds, then POSTs to the api container's `/imports` endpoint to trigger an async import.

Watcher has its own service token (`/data/service_tokens/watcher.jwt`) for authenticating to the api. Optional webhook fires on import completion (Home Assistant, ntfy, Slack, etc.).

## Data flow walkthroughs

### "Upload a folder from my laptop"

```
Browser (folder picker)
  → POST /api/v1/imports/upload (web container nginx proxies to api)
  → api container saves to temp dir, queues an ImportJob
  → ImportWorker (background task in api) parses the EDFs, writes to DuckDB
  → Browser polls /api/v1/imports/jobs/{id} until status=completed
  → Browser refreshes the Overview heatmap
```

### "claude.ai asks: what was my AHI last night?"

```
claude.ai → SSE request to MCP container's /sse (with OAuth bearer)
  → MCP container's verify_token checks the bearer (OAuth / static / JWT)
  → AI emits a tool call: get_nightly_summary(date=...)
  → MCP container's get_nightly_summary tool calls api container's /api/v1/nights/{date}
  → api container reads DuckDB, returns the row
  → MCP container wraps in MCP envelope, sends back to claude.ai
  → claude.ai responds to user with the AHI value
```

### "Watcher detects new files on the SD card"

```
SD card data lands on bind-mount (rsync, copy, etc.)
  → Watcher's scandir-poll detects new file count + mtime
  → Watcher's quiescence timer resets (30 seconds)
  → Watcher's quiescence timer elapses (no more changes)
  → Watcher POSTs /api/v1/imports with the source_path
    (auth header: Bearer <watcher.jwt>)
  → api container queues an ImportJob
  → Async ImportWorker parses + writes
  → Watcher polls the job until terminal
  → Webhook fires (if configured)
```

## The kairos-net network

All four containers attach to an external Docker network called `kairos-net`. (Named after the host's existing convention; pick any external network when deploying.) This network is the only way containers reach each other — they can talk by service name (`ursa-oscar-api`) rather than by IP.

Operators put their other home-server containers on the same network if those services need to query URSA-OSCAR (e.g., Home Assistant calling the webhook URL hosted by another service).

## The /data volume

A single bind-mounted directory holds:

- `ursa-oscar.duckdb` — the analytical database
- `master.key` — Fernet key for the AI secrets store
- `secrets.enc` — encrypted AI provider API keys
- `jwt_secret` — JWT signing secret
- `service_tokens/mcp.jwt`, `service_tokens/watcher.jwt` — auto-managed service auth
- `auth.json` — operator password hash + bootstrap state
- `profile.json` — user profile (visible to AI)
- `vocab.json` — autocomplete vocabulary
- `system_prompt_template.txt` — operator's custom AI system prompt (if set)
- `ai_config.json` — masked-config view of provider preferences
- `exports/` — directory where generated CSVs land
- `import_jobs/` — temp directories for in-flight folder uploads

This directory is the **entire** state of URSA-OSCAR. Back it up; you've backed up everything. Restore it elsewhere; you've moved the entire system.

## Trust boundary

The trust boundary is: anyone with **host file access to `/data`** can act as the operator. Operator password, AI keys, JWT signing secret — all live in `/data`. Standard Linux file perms (mode 0600 on the sensitive files) protect against same-host non-root users, but a determined attacker with root on the host has access to everything.

Appropriate for single-tenant homelab use. See **Single-tenant trust boundary** for the specific implications and what URSA-OSCAR explicitly is NOT designed for.

## Why the API is the sole DuckDB writer

DuckDB acquires an OS-level lock on the database file whenever it's opened — and the lock blocks even read-only opens from other processes. If both the api and MCP containers opened DuckDB independently, the second one to start would fail.

ADR-004 codifies the architectural fix: api container = sole writer, RLock serialization between threads inside the api container, every other container reaches data via HTTP. The MCP server is therefore a thin proxy — it doesn't query DuckDB, it queries the api. Same with the watcher.

This decision pre-dates the auth work (Phase 6.4), so it's been the architecture from early Phase 3.
