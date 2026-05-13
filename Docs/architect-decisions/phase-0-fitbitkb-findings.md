# Phase 0 — fitbitkb Codebase Findings

**Inspector:** Claude Code (URSA-OSCAR workspace)
**Inspected:** `C:\dev\fitbit-web-ui-app-kb\` (read-only)
**Date:** 2026-05-11
**Purpose:** Validate URSA-OSCAR Design v1.0 MCP / SQLite / tool-surface assumptions against fitbitkb — the system the URSA agent (Claude) already talks to today.

> URSA-OSCAR Decision 5 commits to "match fitbitkb's pattern" for the MCP transport, tool naming, and parameter conventions. This document captures exactly what that pattern is, so Phase 1 can copy it verbatim.

---

## 1. MCP Server Implementation

**SDK:** **FastMCP** (the higher-level wrapper on top of the official `mcp` Python SDK). Imported as `from mcp.server.fastmcp import FastMCP`. fitbitkb does **not** use the lower-level `mcp.server.Server` API directly.

**Entry point:** `src/mcp_server.py`.

**Bootstrap (lines 102-104):**

```python
_auth_provider = _build_auth_provider()
mcp = FastMCP("Fitbit Health Coach", auth=_auth_provider)
cache = FitbitCache()
```

**Server start (line ~845):**

```python
app = mcp.http_app(transport="sse")
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
```

**Transport:** **SSE over HTTP**, bound to `0.0.0.0:8000` inside the container. The server is just `uvicorn` running a FastMCP-produced ASGI app.

**Hosting:** Single Docker container with **supervisord** managing three processes (see §7). The MCP server is one of them, priority 300.

---

## 2. Tool Registration

**Mechanism:** Decorator on plain Python functions:

```python
@mcp.tool()
def get_daily_snapshot(date: str, sessionId: str = "", action: str = "",
                       chatInput: str = "", toolCallId: str = "") -> str:
    """
    Get a holistic 'Morning Briefing' view for a specific date.
    Includes Sleep, Activity, Biometrics, and a calculated Readiness Score.

    Args:
        date: Date in 'YYYY-MM-DD' format (e.g., '2023-10-27').
    """
    ...
```

**Description source:** The **function docstring** is what FastMCP sends to the client as the tool description. First line is the summary; the Args section documents parameters.

**Parameter typing:** Plain Python type hints (`str`, `int`, `float`, optional via defaults). No Pydantic models, no JSON Schema decorator args. FastMCP introspects the signature.

> The `sessionId` / `action` / `chatInput` / `toolCallId` arguments on most tools appear to be Claude-platform passthrough fields kept around for compatibility, not used inside the function body. URSA-OSCAR can ignore these — current FastMCP versions don't require them.

**Return convention:** Every tool returns **`str`** — a pre-formatted, human-readable block of text. Examples:
- `get_daily_snapshot` returns `"\n".join(output)` of a Markdown-style summary
- `run_sql_query` returns `json.dumps(results, indent=2)` (JSON-as-string)
- Errors return `f"Error generating snapshot: {str(e)}"` (also a string)

No MCP `TextContent` / `ImageContent` wrappers; no `{ok, data, error}` envelope. FastMCP auto-wraps the str into the right content type for the wire format.

**Tool inventory (representative; not exhaustive):**

| Tool | Purpose | Source |
|---|---|---|
| `get_daily_snapshot` | Morning-briefing view for one date | mcp_server.py:~240 |
| `get_readiness_breakdown` | Explainer for the calculated readiness score | mcp_server.py:~283 |
| `get_sleep_log` | Sleep metrics across a date range | mcp_server.py:~391 |
| `get_sleep_consistency` | Bedtime / wake-time regularity scoring | mcp_server.py |
| `get_workout_history` | Exercise activity log | mcp_server.py |
| `get_activity_log` | Step / distance / floor activity | mcp_server.py |
| `get_zone_analysis` | HR zone distribution over a range | mcp_server.py:~680 |
| `get_comparative_trends` | Side-by-side period comparison | mcp_server.py |
| `analyze_correlation` | Pearson correlation between two metrics | mcp_server.py |
| `get_badges` | Fitbit badge inventory | mcp_server.py |
| `get_lifetime_stats` | Lifetime totals | mcp_server.py |
| `inspect_schema` | DDL of the cache DB | mcp_server.py |
| `run_sql_query` | Read-only SELECT against cache | mcp_server.py:~790 |

**Resources (separate from tools):**

```python
@mcp.resource("fitbit://prompts/personas")
def get_personas() -> str:
    """Get the library of Health Coach personas"""
    return json.dumps(PERSONAS, indent=2)
```

URSA-OSCAR Design currently has no resource surface defined. fitbitkb's pattern (URI prefix + descriptive name + JSON-as-string body) is available if URSA-OSCAR wants to expose static prompts or persona definitions in Phase 5.

---

## 3. SQLite Schema

**Schema source:** Defined in Python, inside `src/cache_manager.py` `_init_database()` (lines 27-143). Schema lives in raw `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements run on `FitbitCache.__init__()`. No `.sql` file, no ORM, no Alembic.

**Tables (top 6 by relevance):**

| Table | PK | Notable columns |
|---|---|---|
| `sleep_cache` | `date` (TEXT) | sleep_score, reality_score, proxy_score, total_sleep, deep/light/rem/wake minutes, start_time, sleep_data_json, last_updated |
| `daily_metrics_cache` | `date` (TEXT) | resting_heart_rate, steps, calories, distance, floors, active_zone_minutes, fat_burn/cardio/peak minutes, weight, body_fat, spo2, eov, last_updated |
| `advanced_metrics_cache` | `date` (TEXT) | hrv, breathing_rate, temperature, last_updated |
| `activities_cache` | `activity_id` (TEXT) | date, activity_name, duration_ms, calories, avg_heart_rate, steps, distance, activity_data_json, last_updated |
| `cardio_fitness_cache` | `date` (TEXT) | vo2_max, last_updated |
| `cache_metadata` | `key` (TEXT) | value (TEXT), last_updated — holds refresh token, last sync date, etc. |

**Primary key style:** TEXT dates (`"YYYY-MM-DD"`) for time-series tables, TEXT id for activities, TEXT keys for metadata. No `INTEGER PRIMARY KEY AUTOINCREMENT` for the metric tables.

**Indexes:** **None explicitly declared.** The PK index on `date` carries point-lookup and range queries (`WHERE date >= ? AND date <= ?`).

**Data types:**
- Numeric: `INTEGER` (counts, minutes, scores) or `REAL` (HRV, weight, distance)
- JSON blobs: `TEXT` (`sleep_data_json`, `activity_data_json`) — raw Fitbit API payload retained for re-derivation
- Timestamps: `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
- Dates: `TEXT` ISO 8601 strings

**Migrations:** None as a formal mechanism. New columns are added via `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` inline in `_init_database()`. Schema is "forward-only by accretion." Adequate for a single-user homelab; would not scale to a versioned production system.

---

## 4. Caching / Refresh Pattern

This is the most differentiated piece of fitbitkb's design — worth study even though URSA-OSCAR's data source (SD card → local) doesn't have the same rate-limit constraints as Fitbit Web API (130 calls/hour).

**Staleness model:** **Per-metric NULL-based, not TTL.** A date is "missing" data for a given metric type if that column is `NULL`. Re-imports never re-fetch a non-NULL value just because it's old.

**Where it lives:** `cache_manager.py:get_missing_dates(start, end, metric_type)` (lines 266-365). Different `metric_type` arguments map to different `IS NOT NULL` checks:

```python
# Sleep:    WHERE reality_score IS NOT NULL
# HRV:      WHERE hrv IS NOT NULL
# Steps:    WHERE steps IS NOT NULL
```

**Refresh paths (two complementary):**

1. **Background phased builder** (`app.py:background_cache_builder`, line ~614). Threaded loop, hourly cadence. Three phases ordered by Fitbit API cost:
   - **Phase 1 — range endpoints:** Cheapest. One Fitbit call returns 365 days of one metric (steps, calories, distance, floors, AZM, HR, activities, SpO2). Always refreshes "today" regardless of NULL status.
   - **Phase 2 — 30-day blocks:** Cardio fitness / VO2 max. Rolls forward in 30-day windows.
   - **Phase 3 — daily endpoints:** HRV, breathing rate, temperature, sleep. 4 API calls per day × 7-day blocks. Most expensive.
   - Loop pattern: Phase 1 → Phase 2 → Phase 3 → Phase 2 → Phase 3 … until hourly budget (130 calls) is exhausted or no missing data remains.

2. **Reactive sync** for today/yesterday (`mcp_server.py:_trigger_sync(date_str)`). On any tool call that touches a recent date, fires `POST http://localhost:5033/api/refresh_daily_stats {"date": date_str}` to the dashboard process. The dashboard fetches fresh data immediately. This keeps "what happened last night?" queries from returning yesterday's stale snapshot.

**Rate-limit handling (app.py lines ~654-1000):** On HTTP 429 from Fitbit, the builder sets `rate_limit_hit = True` and breaks the phase loop; resumes on the next hour tick. Token-refresh failures pause the builder for 1 hour rather than crashing.

> **For URSA-OSCAR**, the analogous decision is the watcher → API → DuckDB import path. There's no external rate limit, but the same single-writer pattern (only the API container writes) is the right inheritance. fitbitkb's reactive-sync trick (UI/tool nudges the writer for fresh data) is a useful pattern for the Daily View screen too — see synthesis doc.

---

## 5. Error Handling

**Tool-level:** Each `@mcp.tool` function wraps its body in `try / except Exception`. On failure, returns a `str` like:

```python
except Exception as e:
    return f"Error generating snapshot: {str(e)}"
```

No structured error envelope, no MCP-protocol-level error response — the failure is communicated as part of the regular `str` return value. The model on the other end has to parse it from the text.

**SQL safety (mcp_server.py:~790, `run_sql_query`):**

```python
if not normalized.startswith("SELECT"):
    return "⛔ Security Error: Only SELECT queries are allowed."
for word in forbidden:
    if word in normalized:
        return f"⛔ Security Error: Keyword '{word}' is not allowed."
```

Whitelist of `SELECT`-only, plus a blocklist of `INSERT/UPDATE/DELETE/DROP/ALTER/...` tokens. URSA-OSCAR Design § Tier 3 (`run_sql_query`) calls for the same constraint — adopt this exact pattern.

**Background-builder resilience (app.py:~668-693):** Exception around token refresh → log + `time.sleep(3600)` + `continue`. The thread never dies; it just stalls.

**Global handler:** There is **no FastMCP / Starlette global exception middleware**. Errors raised inside a tool that aren't caught by the tool's own try/except would propagate to FastMCP, which would surface them as MCP errors to the client. The convention in this codebase is that tools always self-catch.

---

## 6. Project Structure

```
src/
├── app.py              # Dash UI + Flask API endpoints + background cache builder (~328 KB)
├── mcp_server.py       # FastMCP server, all tools (~33 KB)
├── cache_manager.py    # SQLite abstraction (FitbitCache class, ~34 KB)
├── oauth_callback.py   # Minimal Fitbit OAuth redirect handler (~5 KB)
├── prompts.py          # Health Coach personas library (~2 KB)
├── app_old.py          # Deprecated backup
└── assets/             # Dash static files
```

**Layout style:** **Flat.** No `services/` / `api/` / `db/` subpackages, no domain folders. Each file is monolithic — `app.py` is 328 KB.

**Models:** No formal Pydantic / dataclass models. Data flows as plain Python `dict` / `list[dict]`. Where shape matters, it's documented by usage, not by type.

**Services:**
- `FitbitCache` — singleton-ish wrapper around the SQLite connection. Exposes `get_sleep_data`, `get_daily_metrics`, `set_daily_metrics`, `get_missing_dates`, `store_refresh_token`, `get_refresh_token`, etc.
- `oauth_callback.py` — 30-line Flask app for the Fitbit OAuth redirect.
- Background cache builder — function in `app.py`, run in a thread on dashboard login.

**Runtime SQLite path:** `/app/data/data_cache.db` (hardcoded default in `FitbitCache.__init__`). Bind-mounted via `./data:/app/data` in docker-compose.

> **For URSA-OSCAR**, the design doc already specifies a structured `src/ursa_oscar/` package with `api/`, `analytics/`, `ingestion/`, `storage/`, `models/` subfolders. **Don't** inherit fitbitkb's flat-file layout — it works for a single dev who knows where things are but does not scale to URSA-OSCAR's larger surface area (EDF parser, event detectors, time-series writers, REST API, MCP server). Inherit the FastMCP usage pattern; reject the monolithic file pattern.

---

## 7. Docker Layout

**Base image:** `python:3.10-slim` (`dockerfile`, line 1).

**Single container, three supervisord processes (`supervisord.conf`):**

| Priority | Process | Command | Port |
|---|---|---|---|
| 100 | `oauth-callback` | `python /app/src/oauth_callback.py` | 5032 |
| 200 | `dashboard` | `gunicorn --bind 0.0.0.0:5033 --workers 1 --timeout 300 src.app:server` | 5033 |
| 300 | `mcp-server` | `python /app/src/mcp_server.py` | 8000 |

All three `autostart=true`, `autorestart=true`. One container, multiple ports exposed.

**Volume:** `./data:/app/data` — persists `data_cache.db` and logs.

**Env handling (docker-compose.yml lines 16-26):** Inline `environment:` block with `${VAR:-default}` interpolation. Defaults provided for development; production passes real values from `.env`.

**Healthcheck:** `curl -f http://localhost:5033/api/health` every 60s, 5 retries, 120s start-period grace.

> **For URSA-OSCAR**, the supervisord-in-one-container pattern is **available** as a deployment shape but isn't necessary. URSA-OSCAR Design § Repository Structure splits API, MCP, frontend, and watcher into separate containers (matches APEX, not fitbitkb). The separate-container pattern is preferred for URSA-OSCAR because individual services have meaningfully different scaling/restart characteristics (the watcher can restart freely; the API holds DB connections and shouldn't). **Inherit the supervisord priority-ordered process model only if collapsing back to one container becomes desirable for Kevin's homelab footprint.**

---

## 8. Documentation

`docs/` folder contents (one-line summaries):

| File | Description |
|---|---|
| `03-mcp-tool-contract.md` | MCP tool interface spec |
| `16-claude-mcp-usage-guide.md` | How to use the MCP tools from Claude |
| `AGENT_INSTRUCTIONS.md` | Agent-workflow guidance |
| `API_DOCUMENTATION.md` | Flask API endpoint reference |
| `CACHE_SYSTEM_GUIDE.md` | Cache architecture, refresh strategy |
| `DEPLOYMENT_CHECKLIST.md` | Pre-deploy verification |
| `DEPLOYMENT_GUIDE.md` | Deployment runbook |
| `DOCKER - BUILD and DEPLOY.txt` | Docker build/deploy notes |
| `ENHANCEMENT_ROADMAP.md` | Planned features |
| `FITBIT_API_TECHNICAL_DOCUMENTATION.md` | Fitbit API reference |
| `KBfitbitchat.md` | Knowledge-base chat history |
| `MCP_USAGE_GUIDE.md` | MCP usage patterns |
| `PROJECT_TRANSFER.md` | Handover doc |
| `QUICK_START_SECURITY.md` / `SECURITY_SETUP.md` | Security setup |
| `SLEEP_SCORE_FIX_VALIDATION.md` / `VERIFY_FIXES.md` / `WEIGHT_FIX_DEPLOYED.md` | Bugfix validation logs |
| `mcp-server-architecture-template.md` / `mcp_server_design.md` / `mcp_upgrade_walkthrough.md` | MCP design + upgrade docs |
| `cursor_continue_discussion_*.md` | Editor-session continuation notes |

> The most directly useful reference for URSA-OSCAR's MCP work is `mcp-server-architecture-template.md` — the same name and approximate purpose as the file in APEX. Worth reading both side-by-side before writing URSA-OSCAR's tool layer.

---

## 9. Fitbit OAuth Flow

**OAuth standard:** Fitbit's OAuth 2.0 authorization-code grant.

**Flow:**
1. User logs into the dashboard at `:5033`. Dashboard redirects to Fitbit's authorize endpoint with `CLIENT_ID` + `REDIRECT_URL` (env-configured).
2. Fitbit redirects back to `oauth_callback.py` at `:5032` with `?code=...`.
3. `oauth_callback.py` forwards the code to the dashboard (`f"{DASHBOARD_URL}?code={code}"`).
4. Dashboard exchanges the code for access + refresh tokens at Fitbit's token endpoint.
5. Refresh token is stored base64-encoded in `cache_metadata` table (`store_refresh_token` in `cache_manager.py:~454-464`).
6. Background builder refreshes the access token hourly via `refresh_access_token(current_refresh_token)`.

**MCP-side auth (separate from Fitbit OAuth):** The MCP server has its own `FitbitOAuthProvider` (extends FastMCP's `InMemoryOAuthProvider`). Supports:
- Static bearer token via `MCP_BEARER_TOKEN` (for curl / Claude Desktop / Claude Code).
- OAuth 2.0 with pre-registered client via `MCP_OAUTH_CLIENT_ID` + `MCP_OAUTH_CLIENT_SECRET` (for claude.ai web).
- Callback URL hardcoded to `https://claude.ai/api/mcp/auth_callback`.

> For URSA-OSCAR there is **no upstream OAuth** to manage (data source is the local SD card / filesystem). The only OAuth surface URSA-OSCAR needs is the MCP-side bearer + OAuth pattern, which is identical between fitbitkb and APEX. URSA-OSCAR should copy this pattern verbatim.

---

## Patterns URSA-OSCAR Should Inherit

- **FastMCP + SSE + uvicorn** boilerplate from `mcp_server.py` lines 102-104 and ~845. URSA-OSCAR Design § Tech Stack should be updated from "Python + `mcp` SDK" to **"FastMCP (built on `mcp` SDK), SSE transport"** — see synthesis Conflict 3.
- **`@mcp.tool()` decorator + docstring-as-description + plain-type-hints** pattern. No need for explicit JSON Schema; FastMCP introspection handles it.
- **`inspect_schema` + `run_sql_query` with SELECT-only validation** — Tier 3 escape-hatch pattern is well-proven here.
- **Reactive-refresh trick** for "live today" queries — when URSA-OSCAR Daily View renders last night's data, it should be able to trigger the watcher to re-scan the SD card if data is incomplete.
- **MCP-side OAuth 2.1 + bearer fallback** for both desktop/CLI and web Claude clients.

## Patterns URSA-OSCAR Should **Not** Inherit

- **Flat single-file layout (`app.py` 328 KB).** URSA-OSCAR's structured `src/ursa_oscar/` is correct.
- **Returning `str` from every tool.** URSA-OSCAR Design § MCP Tool Surface specifies structured returns with `interpretation` blocks; that's incompatible with `str`-only and is the right call. FastMCP supports returning dicts / Pydantic models; URSA-OSCAR should use them.
- **In-place `ALTER TABLE` migrations.** URSA-OSCAR Design § Data Model includes a `schema_version` table — use it. Migration runner per Design § Repo Structure (`storage/migrations.py`).
- **NULL-based staleness.** URSA-OSCAR's data source is the SD card; once a night is imported, it's complete. No equivalent partial-data staleness model needed. The Design's "import_log + dedup-on-date" approach is correct.
- **No formal data models.** URSA-OSCAR Design uses Pydantic domain models (`models/domain.py`) — keep this.

---

## Unresolved (Phase 0)

None blocking. The MCP SDK naming distinction (FastMCP vs raw `mcp`) is a documentation correction, not an architectural change.
