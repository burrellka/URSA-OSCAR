# 03 — MCP Tool Contract

**Authoritative as of:** 2026-05-12 (image tag `0.1.3`)
**Server:** FastMCP 3.2.4 + mcp 1.27.0 (pinned per ADR-002)
**Transport:** SSE over HTTPS
**Auth:** OAuth 2.1 + PKCE (claude.ai web) or static bearer (CLI / Claude Desktop / Claude Code)

URSA-OSCAR exposes a **read-mostly tool surface** designed for the URSA agent (Claude) to query CPAP analytics during conversational sleep-coaching sessions. Tools are stateless from the client's perspective: each call is one JSON-RPC `tools/call` request to `/messages/?session_id=...` after an SSE-established session.

## Wire format

Every tool returns one of two shapes inside the MCP `result.content[0].text` payload:

```json
{"ok": true,  "data": { ... }}
{"ok": false, "error": "human-readable",  "code": "NOT_FOUND" | "INVALID_INPUT" | "INVALID_OPERATION" | "ERROR"}
```

Helpers `_ok()` / `_err()` (lifted verbatim from APEX template §6.1) wrap every return. The envelope is the same one APEX uses.

## Tool catalog (8 tools as of 0.1.3)

### Tier 1 — Read tools (7)

#### `get_nightly_summary(date, end_date=None)`

Returns the full `nightly_summary` record(s) for a CPAP night or date range. AHI broken into obstructive / central / hypopnea / RERA components, pressure percentiles (median / 95% / 99.5%), EPAP equivalents, leak statistics, time-in-apnea, equipment settings, session count.

- **`date`** (str, required) — YYYY-MM-DD.
- **`end_date`** (str | None) — YYYY-MM-DD. When set, returns a list of summaries from `date` through `end_date`.

Returns: single object (data) or list (data[]).

Use when the user asks: *"How was last night?"* / *"Show me my CPAP data for May 10."* / *"What's my AHI been this week?"*

#### `get_ahi_breakdown(date, end_date=None)`

AHI broken down by event type plus clinical-interpretation hints:
- `obstructive_treatment_status`: `well_controlled` | `partial_control` | `inadequate_control`
- `central_apnea_concern`: `none` | `mild` | `elevated` | `significant`
- `tecsa_likely`: `true` when centrals ≥50% of total AHI on a ≥5 AHI night

Counts, per-event-type indices, percent-of-total split, plus a `notes` list of human-readable observations.

- **`date`** (str, required)
- **`end_date`** (str | None) — counts summed across the range, indices recomputed over total time

Use when the user asks: *"Were my apneas mostly central or obstructive?"* / *"Is my CPAP working?"* / *"How much of my AHI is TECSA?"*

#### `get_event_distribution_by_hour(date, event_types=None)`

Per-hour event counts for ONE night. Reveals time-of-night patterns (central clusters at 2-4 AM REM windows, position-dependent obstructives at session start, end-of-night events from mask drift).

- **`date`** (str, required)
- **`event_types`** (list[str] | None) — restrict to e.g. `["ClearAirway"]` for central-only views

Returns `{"date": str, "hours": [{"hour": int 0-23, "counts": {event_type: int}}, ...]}`.

Use when the user asks: *"When in the night do my centrals cluster?"* / *"Show me last night's events by hour."*

#### `get_pressure_profile(date, end_date=None)`

Median / 95% / 99.5% pressure for a night or date range. Includes a `ceiling_hit` boolean (machine reached `max_pressure_setting * 0.97` or higher) and a recommendation string.

- **`date`** (str, required)
- **`end_date`** (str | None)

Use when the user asks: *"What pressure am I averaging?"* / *"Is my CPAP pressure maxing out?"*

#### `get_leak_profile(date, end_date=None)`

Leak percentiles + minutes-over-redline + `seal_quality` interpretation (`good` / `marginal` / `poor`). AirSense 11 redline is 24 L/min; sustained excursions ≥10 seconds become LargeLeak events.

- **`date`** (str, required)
- **`end_date`** (str | None)

Use when the user asks: *"Are my masks leaking?"* / *"How was my leak last night?"*

#### `get_session_breakdown(date)`

Per-session details for nights with multiple mask-on periods. Returns each session_id with first/last event timestamps and per-event-type counts. Useful when a night was interrupted (mask off / bathroom / readjust).

- **`date`** (str, required)

Use when the user asks: *"Was the bad AHI in the first or second session?"*

#### `list_available_nights(start_date=None, end_date=None, filter_expression=None)`

Calendar / summary listing. Returns one entry per available night with AHI, session count, total time. Optional simple-predicate filter (`AHI < 5`, `session_count >= 2`). Anything more complex uses the Tier-3 `run_sql_query` escape hatch (deferred to Phase 4).

- **`start_date`** (str | None)
- **`end_date`** (str | None)
- **`filter_expression`** (str | None) — supported keys: `AHI`, `session_count`; operators `<`, `<=`, `>`, `>=`, `=`, `!=`.

Use when the user asks: *"What nights do I have CPAP data for?"* / *"Show me all nights with AHI under 5."*

### Tier 3 — Operational (1)

#### `trigger_import(source_path="/cpap-import")`

Kicks off a folder import via the API container over `kairos-net`. Idempotent — re-importing a date overwrites prior data for that date. The default `/cpap-import` is the bind-mounted CPAP source from the TrueNAS host.

- **`source_path`** (str, default `/cpap-import`) — container-side path to a DATALOG dir or SD-card root

Returns:
```json
{"ok": true, "data": {
   "nights_imported": int,
   "earliest_date": "YYYY-MM-DD" | null,
   "latest_date": "YYYY-MM-DD" | null,
   "status": "completed" | "failed",
   "source_path": "/cpap-import"
}}
```

Use when the user asks: *"Import the new nights"* / *"Re-import everything from the SD card"* / *"Pull in last week's data."*

## Architecture: MCP-as-API-proxy

Per Decision 2 (DuckDB embedded) URSA-OSCAR uses a single DuckDB file owned exclusively by the API container. DuckDB's cross-process lock model prevents read-only opens from other processes when the file is held in write mode, so the MCP container does **not** open DuckDB at all. All tools call HTTP endpoints on `ursa-oscar-api:8000` (kairos-net internal DNS):

```
claude.ai  ──HTTPS──>  Cloudflare Tunnel
                            │
                            ▼
                    ursa-oscar-mcp:8000  ──HTTP──>  ursa-oscar-api:8000
                    (SSE + OAuth)                    (DuckDB owner)
```

Tools map to API endpoints:

| Tool | Backend |
|---|---|
| `get_nightly_summary` (single) | `GET /api/night/{date}` |
| `get_nightly_summary` (range) | `GET /api/nights?start=&end=` |
| `list_available_nights` | `GET /api/nights?start=&end=` (filter applied MCP-side) |
| `get_ahi_breakdown` | `GET /api/nights?start=&end=` + `GET /api/events?date=` (per night) |
| `get_event_distribution_by_hour` | `GET /api/events?date=&event_type=` |
| `get_pressure_profile` | `GET /api/nights?start=&end=` |
| `get_leak_profile` | `GET /api/nights?start=&end=` |
| `get_session_breakdown` | `GET /api/events?date=` |
| `trigger_import` | `POST /api/imports` |

The tool surface is otherwise unchanged from the in-process design in `URSA-OSCAR_Design.md` § MCP Tool Surface.

## Deferred (Phase 3+)

Tier 2 (analytical) and the remaining Tier 3 tools deferred per Design v1.1 Phase 3:

| Tool | Phase | Notes |
|---|---|---|
| `compare_periods` | 3 | side-by-side metrics across two date ranges |
| `analyze_correlation` | 3 | Pearson against manual_logs metrics |
| `get_trend` | 3 | linear regression slope / R² over a metric |
| `get_manual_log_summary` | 3 | requires manual_logs collection (Phase 3) |
| `add_manual_log` | 3 | write tool — first Manual Logging dependency |
| `inspect_schema` | 4 | DuckDB DDL dump |
| `run_sql_query` | 4 | SELECT-only escape hatch with keyword blocklist |
| `get_import_status` | 4 | async-job tracking (Phase 4 watcher) |
| `export_data` | 4 | CSV / JSON / OSCAR-compat export bundles |

`correlate_with_external` (cross-source Fitbit/Apex correlation) is intentionally **not** on the URSA-OSCAR tool surface per Design Decision 6 — cross-source correlation is agent-mediated.

## Tool description discipline

Each tool's MCP description comes from the Python docstring (FastMCP convention; surfaces in claude.ai's connector dialog + `tool_search`). Docstrings include 3-5 "use when the user asks" example queries to help claude.ai route conversational requests to the right tool without explicit invocation.

Snake_case names, descriptive verb prefixes (`get_`, `list_`, `analyze_`, `trigger_`), structured return values with `interpretation` blocks where clinically useful. Matches fitbitkb's convention so the URSA agent's tool-routing heuristics generalize.

## Logging discipline (security)

The MCP server never logs bearer tokens, OAuth client secrets, or access tokens. OAuth issuance logs the `client_id` and `expires_at` only. Per APEX template §9.1.

Post-deploy verification:
```bash
docker logs ursa-oscar-mcp 2>&1 | grep -i "$URSA_OSCAR_MCP_BEARER_TOKEN"   # expect empty
docker logs ursa-oscar-mcp 2>&1 | grep -i "$URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET"   # expect empty
```

## Validation harness

See `mcp-server/tests/verification/test_auth_boundary.py` — six in-process Starlette `TestClient` assertions per APEX template §8:
1. `/.well-known/oauth-authorization-server` reachable; no `registration_endpoint`
2. `POST /register` → 4xx (DCR off)
3. `POST /messages/` without bearer → 401 with `resource_metadata=...` in `WWW-Authenticate`
4. PKCE auth-code flow with pre-registered client yields access_token
5. Issued access_token unblocks `/messages/`
6. Static bearer also unblocks `/messages/`

Plus `infra/verify-mcp-live.sh` — four curl one-liners against a deployed endpoint:
```bash
HOST=https://your-public-host.example.com \
  URSA_OSCAR_MCP_BEARER_TOKEN=... \
  bash infra/verify-mcp-live.sh
```
