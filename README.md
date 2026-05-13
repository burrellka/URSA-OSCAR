# URSA-OSCAR

Self-hosted CPAP analytics + MCP server. Replaces OSCAR's desktop-only workflow with a homelab-deployed service that an AI assistant (via MCP) can query directly.

**Status:** Phase 2 complete. Current image tag: `0.4.2` on Docker Hub (`brain40/ursa-oscar-{api,mcp,web,watcher}`).

**License:** [GNU GPL-3](LICENSE). See [COPYRIGHT](COPYRIGHT) for the project copyright notice and the OSCAR-project acknowledgement.

---

## What URSA-OSCAR is

A small self-hosted analytics stack that:

- Imports ResMed AirSense 11 SD-card exports (DATALOG/, EVE.edf, BRP/PLD/SA2.edf, STR.edf, SETTINGS/CurrentSettings.json) into a single embedded DuckDB.
- Computes per-night summaries (AHI components, pressure / leak / event distributions) with OSCAR-equivalent fidelity.
- Serves a React + uPlot web UI for daily / overview / events / statistics review.
- Exposes an MCP server (FastMCP + SSE, OAuth 2.1 + PKCE + static bearer fallback) so an AI assistant like Claude can call `get_nightly_summary`, `get_ahi_breakdown`, `trigger_import`, etc. — eight tools today.

**What URSA-OSCAR adds on top of OSCAR's prior art:**
- Network-accessible service (Docker on a NAS), not a desktop application.
- AI-assistant integration via MCP for conversational analysis.
- Foundation for subjective + objective correlation (Phase 3 — manual logging + correlation tools).

## Architecture (one paragraph)

Python 3.11 + FastAPI + DuckDB embedded + FastMCP-over-SSE. React 18 + TypeScript + Vite + hand-rolled CSS (no Tailwind/shadcn — see ADR-001). Docker Compose on a NAS, images on Docker Hub. Auth on the MCP surface: static bearer + OAuth 2.1 + PKCE, DCR disabled. See [`Docs/architect-decisions/adr-002-mcp-server-template-adoption.md`](Docs/architect-decisions/adr-002-mcp-server-template-adoption.md) for the MCP template lineage and [`Docs/URSA-OSCAR_Design.md`](Docs/URSA-OSCAR_Design.md) for the current authoritative spec.

## Layout

```
backend/      FastAPI + analytics + ingestion
mcp-server/   FastMCP server (lifted from APEX template — ADR-002)
frontend/     React + Vite + uPlot
watcher/      File-watcher daemon scaffold (Phase 4)
infra/        Docker Compose + PowerShell build script + .env example
Docs/         Framework, Design, ADRs, MCP tool contract, OAuth setup
data/         Runtime data — gitignored (DuckDB, vocab.json, profile.json)
```

## Quickstart (dev loop)

```powershell
# 1. Copy infra/.env.example to .env at repo root and fill in real values.
#    Generate the three MCP secrets per inline instructions in that file.
Copy-Item infra\.env.example .env

# 2. Bring up dev stack
docker compose -f infra\docker-compose.dev.yml up --build

# 3. Smoke-test via the LAN dev-bypass port (5065)
curl http://localhost:5065/healthz
curl http://localhost:5065/api/v1/nights

# 4. Verify MCP auth boundary against the running container
$env:HOST = "http://localhost:8082"
$env:URSA_OSCAR_MCP_BEARER_TOKEN = "dev-bearer-token-please-rotate"
bash infra\verify-mcp-live.sh

# 5. Run backend + MCP test suites (no Docker required)
cd backend ; python -m pytest -v ; cd ..
cd mcp-server ; python -m pytest -v ; cd ..
```

Open the web UI at `http://localhost:5063`.

## Production deploy (Docker Compose)

Copy [`infra/docker-compose.production.yml`](infra/docker-compose.production.yml) into your container manager (Dockge, Portainer, raw `docker compose`). Set the MCP secrets, public hostname, and volume host paths in the env block. Then pull + recreate:

```bash
docker compose pull
docker compose up -d --force-recreate
```

The web UI is at `<host>:5063`, MCP SSE at `<host>:8085/sse`. See [`Docs/17-oauth-setup.md`](Docs/17-oauth-setup.md) for connecting an AI assistant (claude.ai connector setup).

## Build + push to Docker Hub

```powershell
.\infra\build_and_push.ps1 -Version 0.4.2                       # build + push
.\infra\build_and_push.ps1 -Version 0.4.2 -SkipPush             # build only
.\infra\build_and_push.ps1 -Version 0.4.2 -DockerHubUser yourns # publish under your own namespace
```

## Acknowledgements

URSA-OSCAR ports event-detection and analytics concepts from the [OSCAR project](https://www.sleepfiles.com/OSCAR/) (Open Source CPAP Analysis Reporter), which is itself GPL-licensed. The nightly aggregation, AHI computation, and pressure / leak / event handling owe their correctness to OSCAR's prior art.

Not affiliated with ResMed, Anthropic, Apple, or the OSCAR project.

## Contributing

Contributing guide is forthcoming (Phase 3 work). For now: issues and PRs welcome on this repo. Keep PRs small, write tests where they make sense, **no PII or real CPAP recordings in fixtures** — the project ships with anonymized targets only.
