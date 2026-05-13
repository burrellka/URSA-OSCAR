# ADR-003 — MCP-as-API-proxy architecture (supersedes part of Decision 2)

**Status:** Accepted
**Date:** 2026-05-12
**Supersedes (partial):** `URSA-OSCAR_Design.md` Decision 2 — specifically the claim "Concurrent reads supported; single-writer pattern enforced" as it pertains to cross-container DuckDB access.
**Inputs:** Phase 1 build experience; `Docs/12-audit-report.md` Finding 3; `Docs/15-build-handover.md` Deviation 1.

---

## Context

URSA-OSCAR `URSA-OSCAR_Design.md` Decision 2 commits to DuckDB embedded as the sole storage engine. The original v1.0/v1.1 wording included an explicit concurrent-access model:

> Concurrent access model:
> - API container: read+write (handles imports, manual log inserts)
> - MCP container: read-only (URSA agent queries)
> - Watcher container: signals API to perform writes, never writes directly
> - UI: queries via API, never touches DB directly

Phase 1 implementation followed this verbatim — the `mcp-server/src/ursa_oscar_mcp/client.py` originally opened a read-only DuckDB connection against `/data/ursa-oscar.duckdb`, bind-mounted into both the API container (read+write) and the MCP container (read-only).

In production deployment, every MCP read tool returned:

```
IO Error: Could not set lock on file "/data/ursa-oscar.duckdb":
Conflicting lock is held in PID 0. See also
https://duckdb.org/docs/stable/connect/concurrency
```

DuckDB's actual cross-process lock model is more restrictive than Decision 2 assumed: when one process holds the file in **write** mode (the API container's FastAPI lifespan-managed connection), other processes cannot open the same file even in **read-only** mode. The DuckDB concurrency docs at the URL above are explicit:

> Only one DuckDB instance can write to a database file at a time. Any process that opens a DuckDB database in writable mode (the default) prevents others from doing the same. If multiple processes need to access the same file concurrently, they all need to open it in read-only mode.

"Read-only by all processes" is not viable for URSA-OSCAR because the API must remain a writer (imports, manual logs in Phase 3, audit-log inserts).

## Decision

The MCP container does not open DuckDB at any point. The `mcp-server` image has no `duckdb` runtime dependency and no `/data` volume mount.

All MCP tools call HTTP endpoints on the `ursa-oscar-api` container over the `kairos-net` Docker network. The API container is the sole owner of the DuckDB file. The MCP server becomes a pure transport + auth + interpretation layer:

```
claude.ai → Cloudflare Tunnel → ursa-oscar-mcp:8000 (SSE + OAuth)
                                       │
                                       ▼  HTTP over kairos-net
                                 ursa-oscar-api:8000 (FastAPI + DuckDB owner)
                                       │
                                       ▼
                                 ursa-oscar.duckdb
```

### Tool-to-endpoint mapping (canonical, post Phase 1.5)

| Tool | Backend call |
|---|---|
| `get_nightly_summary` (single date) | `GET /api/v1/night/{date}` |
| `get_nightly_summary` (range) | `GET /api/v1/nights?start=&end=` |
| `list_available_nights` | `GET /api/v1/nights?start=&end=` (filter applied MCP-side) |
| `get_ahi_breakdown` | `GET /api/v1/nights?start=&end=` + `GET /api/v1/events?date=` per night |
| `get_event_distribution_by_hour` | `GET /api/v1/events?date=&event_type=` |
| `get_pressure_profile` | `GET /api/v1/nights?start=&end=` |
| `get_leak_profile` | `GET /api/v1/nights?start=&end=` |
| `get_session_breakdown` | `GET /api/v1/events?date=` |
| `trigger_import` | `POST /api/v1/imports` |

`ursa-oscar-api` resolves via Docker DNS (`http://ursa-oscar-api:8000`); the MCP container needs no special networking beyond `kairos-net` membership.

### What stays from Decision 2

- DuckDB as the storage engine (no migration to a client/server DB)
- Single `.duckdb` file (no sharding)
- API container as the writer
- Watcher container signals API to write, never writes directly
- UI queries via API, never touches DB directly
- ~3 MB/night storage characteristics, sub-second analytical queries inside the API process

### What changes from Decision 2

- The MCP container is no longer a DuckDB reader.
- The MCP container has no `/data` volume mount.
- The MCP container has no `duckdb` Python dependency.
- Cross-container DB access is HTTP, not file-shared.

## Consequences

**Positive:**
- The cross-process lock failure mode is eliminated. There is exactly one process touching the DuckDB file, ever.
- The MCP container is smaller (~80 MB shaved vs. the previous build).
- The architecture mirrors fitbitkb's effective pattern more closely (where the MCP queries through the local Dash server's endpoints, not the SQLite file directly).
- API endpoint reuse: every MCP query path is also a REST endpoint, exercised by the Phase 2 frontend. No "tool only" code paths to maintain.

**Negative:**
- One additional intra-container HTTP hop per MCP tool call (~5-10ms inside `kairos-net`). Acceptable at homelab scale; non-issue at single-user query volume.
- MCP container now has a runtime dependency on the API container. `depends_on: ursa-oscar-api` already enforces this at compose level. If the API container is down, MCP returns `{"ok": false, "code": "ERROR", "error": "Could not reach API container: ..."}` — well-formed envelope, not a 5xx.
- The 12 in-process MCP tool tests in `mcp-server/tests/test_tools.py` are no longer valid (they assumed direct DuckDB). Currently marked `pytestmark.skip` pending the Phase 1.5 work of writing an API-container fixture.

**Revisit triggers:**
- If we ever need <1ms MCP→DB latency: revisit by sharing a connection pool via uds (Unix-domain socket) or by collapsing API + MCP into one process. Not relevant at Phase-1-through-Phase-3 scope.
- If DuckDB ships true multi-process concurrent-read-with-writer support: revisit and consider going back to direct read-only opens. Track DuckDB release notes.

## Validation

End-to-end verification of the API-proxy architecture, run from outside `kairos-net` against the live deployment:

```
$ MSYS_NO_PATHCONV=1 URSA_OSCAR_MCP_BEARER_TOKEN=<...> \
    python backend/tests/_scratch_mcp_call.py \
    https://your-public-host.example.com \
    get_nightly_summary --arg date=2026-05-08

{"ok": true, "data": {
    "date": "2026-05-08", "session_count": 1, "total_ahi": 11.443,
    "median_pressure": 7.36, "p95_pressure": 9.94, ...
}}
```

Same value the in-process regression suite returns; same value the canonical_targets table expects (within tolerance). The hop count: `Windows → Cloudflare → MCP container → API container → DuckDB → back the same path`. No lock errors. No degraded values.

The full per-deploy curl check (`infra/verify-mcp-live.sh`) passes 4/4 against both LAN and public endpoints.

## References

- Decision 2 in `URSA-OSCAR_Design.md` — the storage decision this ADR partially supersedes
- `Docs/12-audit-report.md` Finding 3 — the production-deploy diagnostic that surfaced the lock conflict
- `Docs/15-build-handover.md` Deviation 1 — the build-narrative description of the fix
- DuckDB Concurrency docs: <https://duckdb.org/docs/stable/connect/concurrency>
- APEX `docs/mcp-server-architecture-template.md` — the template URSA-OSCAR's MCP server lifts from (ADR-002); APEX's MCP holds a Mongo client directly because Mongo's lock model doesn't have this restriction

## Implementation

Landed in Phase 1 commit `cdc821a`:

- `mcp-server/src/ursa_oscar_mcp/client.py` replaced (`DuckDBManager` → `api_get` / `api_post` httpx helpers)
- All 8 tool modules in `mcp-server/src/ursa_oscar_mcp/tools/` rewritten to call API endpoints
- `mcp-server/pyproject.toml` — `duckdb` removed from dependencies
- All three compose files — `/data:/data:ro` mount and `URSA_OSCAR_DB_PATH` env removed from MCP service; `URSA_OSCAR_API_URL: http://ursa-oscar-api:8000` env added
- Phase 1.5 will repoint these to `/api/v1/*` per Decision 16

Image affected: `brain40/ursa-oscar-mcp` 0.1.2 → 0.1.3.
