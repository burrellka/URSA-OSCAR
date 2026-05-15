# URSA-OSCAR — Architecture Deep Dive

Audience: developers who've read [`30-developer-guide.md`](30-developer-guide.md) and want the systems-thinking layer beneath the file-by-file map. This document explains *why* the architecture looks the way it does, where the design pressure came from, and what trade-offs were made (and could be re-made).

---

## Table of contents

1. [The DuckDB-as-bottleneck story](#1-the-duckdb-as-bottleneck-story)
2. [Why the API container owns everything](#2-why-the-api-container-owns-everything)
3. [The two presentation layers over one tool surface](#3-the-two-presentation-layers-over-one-tool-surface)
4. [Sync vs async — the import queue](#4-sync-vs-async--the-import-queue)
5. [Timezone fiction](#5-timezone-fiction)
6. [Streaming: SSE end-to-end](#6-streaming-sse-end-to-end)
7. [Encrypted secrets: where to draw the line](#7-encrypted-secrets-where-to-draw-the-line)
8. [Session exclusion: the recompute pattern](#8-session-exclusion-the-recompute-pattern)
9. [Watcher: fingerprint + quiescence](#9-watcher-fingerprint--quiescence)
10. [Frontend state: deliberately small](#10-frontend-state-deliberately-small)

---

## 1. The DuckDB-as-bottleneck story

DuckDB v1.x acquires a file-level lock on the database file. The lock is *process-level*, not connection-level: while a writer is connected, **no other process** — not another DuckDB instance, not a separate read-only connection from another container — can open the file. This is a deliberate design choice on DuckDB's side; it sacrifices multi-process concurrency for transactional simplicity.

Our original Phase 1 design had each container open its own DuckDB connection (the MCP container would open read-only, the API container read-write). That broke immediately when both containers started:

```
DuckDB error: IOException — file is locked by another process
```

Two options to recover:
1. **Switch databases** — Postgres, SQLite-with-WAL, etc. Costs the columnar query performance that makes URSA-OSCAR's analytics fast (DuckDB's percentile, GROUP BY, window-function performance is significantly better than row-store equivalents on the same hardware).
2. **Switch architecture** — make one container the sole DB owner; others talk to it over HTTP.

We picked option 2 (ADR-003). The trade-off: every cross-container read is now an HTTP round-trip with JSON serialization overhead. In practice the overhead is microseconds and doesn't matter at single-operator scale.

### Concurrency inside the API container

The API process itself is single-machine multi-threaded (uvicorn's thread pool + asyncio). DuckDB's *intra-process* concurrency model is a single writer + multiple readers via the Python `RLock`. We wrap mutations in `db.serialized()` (see `storage/db.py`), which acquires the lock for the duration of the block:

```python
with db.serialized() as conn:
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM nightly_summary WHERE date = ?", (date,))
        conn.execute("INSERT INTO nightly_summary ...", (...))
        conn.execute("COMMIT")
    except:
        conn.execute("ROLLBACK")
        raise
```

The `BEGIN`/`COMMIT`/`ROLLBACK` triplet is important — DuckDB allows multi-statement transactions but doesn't autocommit individual statements at the application level. Most repository functions follow this pattern.

Reads typically *don't* need serialization — multiple `SELECT`s can run concurrently against the same connection. But repositories that read-then-write (e.g., the `sessions.toggle()` helper which queries existence then inserts/deletes) wrap the whole sequence in `serialized()` to avoid TOCTOU bugs.

ADR-004 captures the full ruleset.

---

## 2. Why the API container owns everything

Once we accepted "API is the only thing that can touch DuckDB," the rest of the architecture fell into place:

| Container | Job |
|---|---|
| **api** | DuckDB owner. Analytics. Ingestion. AI proxy. Async worker. Profile/vocab JSON file state. |
| **mcp** | HTTP wrapper that re-exposes API endpoints as MCP tools to Claude.ai. Owns auth (bearer + OAuth). |
| **web** | Static React SPA + nginx reverse-proxying `/api/*` → api container. Zero state. |
| **watcher** | Polling daemon that detects new SD-card data on a bind-mounted path and enqueues import jobs via the API's HTTP surface. |

The clean separation has follow-on benefits:

- **MCP container can be restarted without touching the API.** Auth config changes, FastMCP version upgrades, etc. don't risk the data layer.
- **Web container is purely static** — a CDN or aggressive nginx caching helps it scale even though a homelab user doesn't need that.
- **Watcher is independent.** A watcher bug stops auto-imports but doesn't take down the system. The web UI continues to serve.

### Inter-container DNS

We use Docker's embedded DNS (`127.0.0.11`) so container hostnames (`ursa-oscar-api`, `ursa-oscar-mcp`) resolve to current IPs on the `kairos-net` network. **Critical for nginx (`frontend/nginx.conf`):**

```nginx
resolver 127.0.0.11 valid=30s ipv6=off;
map "" $ursa_api_upstream { default "ursa-oscar-api"; }

location /api/ {
    proxy_pass http://$ursa_api_upstream:8000;
    ...
}
```

The `map` + variable indirection forces nginx to re-resolve the hostname per-request. Without it, nginx caches the IP at config-load time, and when the API container restarts (which can change its IP), nginx 502s until you restart the web container too. We hit this exact bug in APEX before lifting the fix here.

---

## 3. The two presentation layers over one tool surface

URSA-OSCAR has eleven analytical tools the user can call:

```
get_nightly_summary       compare_periods         get_pressure_profile
get_ahi_breakdown         analyze_correlation     get_leak_profile
list_available_nights     get_trend
get_event_distribution_by_hour                    get_user_profile
get_manual_log_summary
```

There are **two** ways these get exposed to an AI:

### A) MCP server, for Claude.ai

The MCP container has `mcp-server/src/ursa_oscar_mcp/tools/<tool_name>.py` files, each decorated with `@mcp.tool()`. Claude.ai's connector OAuths in, calls a tool over SSE, the MCP server `httpx.get()`s the equivalent API endpoint, wraps the response in `{ok, data}` envelope, and returns it.

### B) AI proxy, for the in-app chat panel

The API container has `backend/src/ursa_oscar/ai_proxy/tools.py` which exports `TOOL_DESCRIPTORS` (the same 11 tools' OpenAI-style function-calling descriptors) and `execute_tool(name, args, api_base_url)` which executes a tool by calling the API's own endpoints via httpx.

### Why two implementations of the same thing?

Because they have different surface contracts:

- **MCP** has its own protocol with specific framing, OAuth handshakes, SSE streaming envelope, etc. FastMCP handles all of that but requires the `@mcp.tool()` decorator.
- **AI proxy** needs JSON-schema descriptors for OpenAI-style function calling (or Anthropic's `tool_use`), plus an executor it can call from inside a chat-completion loop.

Both implementations *route to the same API endpoints*. The endpoint is the source of truth; the wrappers are presentation layers. Adding a new tool means:

1. Add the API endpoint (where the math lives)
2. Add a thin MCP wrapper (a 10-15 line file under `mcp-server/src/.../tools/`)
3. Add an entry to `TOOL_DESCRIPTORS` + `_TOOL_ROUTING` in `ai_proxy/tools.py`

The MCP wrapper and the AI proxy descriptor are independent — you could ship a new tool to one without the other if you wanted (though we generally ship them in pairs).

### Why not a single shared registry?

We considered importing the MCP tool functions directly into the AI proxy. Two reasons we didn't:

- **Dependency direction.** The MCP server depends on `fastmcp==3.2.4` which pins `starlette==1.0.0`, conflicting with FastAPI's expected starlette range. Importing the MCP package into the API process would clobber FastAPI.
- **MCP decorators carry framework cruft.** The `@mcp.tool()` decorator registers the function with FastMCP's runtime, expects specific argument conventions, etc. Reusing it inside the AI proxy would require fighting the framework for behavior we don't want there.

So we accept a small amount of duplication (the docstrings + descriptors mirror each other) in exchange for clean dependency boundaries.

---

## 4. Sync vs async — the import queue

Phase 1's importer was synchronous. The browser POSTed to `/imports`, the HTTP request held open for the duration of the import (could be 10+ seconds for a multi-night card), then returned an `ImportLogEntry`.

That worked at single-user, small-card scale. It broke when:
- The browser timed out waiting (default fetch timeout in some setups)
- The operator wanted to navigate elsewhere mid-import (they couldn't see status)
- The watcher daemon got added (Phase 4 Ticket 3) — it would have had to either block on the import or accept zero visibility into it

Phase 4 Ticket 2 introduced the async queue (table `import_jobs`, status state machine `queued → running → {completed | failed | orphaned}`). The `POST /imports` endpoint became a tiny job-enqueue: insert a row, return `{id, status: "queued"}` immediately. The actual parse runs in a background asyncio task in the API process.

### Why in-process rather than Celery / Redis / external worker?

Single-operator system. We don't need to scale out. An external worker introduces:
- Another container to deploy + monitor
- Another inter-container dependency (Redis or similar)
- Cross-process job state synchronization (where Redis would help, but we'd still need to think about it)

DuckDB is already the durable backing store for everything else; reusing it for job state is a clean fit.

### Why `asyncio.to_thread` for the import body?

`import_path()` is CPU-bound (EDF parsing + numpy math), not I/O-bound. Running it directly in the asyncio event loop would block other requests (including the chat panel's SSE streams) for the duration. `asyncio.to_thread(...)` offloads to the default thread pool (`concurrent.futures.ThreadPoolExecutor`), keeping the event loop responsive.

### Orphan recovery

If the API container restarts mid-import, the in-flight job's row stays in `running` status forever — the asyncio task that owned it is gone. On startup, `mark_orphaned_on_startup()` flips any `running` rows to `orphaned` with an explanatory `error_message`. The operator sees orphans in the UI and can decide whether the import actually committed (per-night atomicity means DuckDB commits successful nights before erroring; orphans usually mean "most nights are fine, the last one didn't make it") or whether to retry.

---

## 5. Timezone fiction

This deserves its own section because it bit us hard in Phase 4.

### What ResMed actually does

The ResMed AirSense 11 records timestamps as wall-clock strings with no timezone metadata. The EDF header field `recording_starttime` is literally `13.05.26 21:35:07`. The filename is `20260513_213507_BRP.edf`. The device's clock is whatever the operator set it to; the device doesn't know about timezones.

### What the operator's wall-clock actually is

The operator's actual local time depends on their timezone + DST. ResMed devices don't auto-adjust for DST, so a device set to EST in November 2025 still reads EST in May 2026 — but the operator is now in EDT.

### How we handle it

**Server-side: pure fiction.** All timestamps are treated as naive Python `datetime` objects throughout the entire stack. We never apply any timezone conversion. The DB stores `TIMESTAMP` (DuckDB's no-tz type). The API serializes as ISO 8601 without a `Z` suffix.

**Client-side: optionally shifted at display time.** Phase 4 Ticket 4 added the `DeviceClock` config to `UserProfile.display`:

```typescript
{
  country: "USA",
  mode: "static_offset",
  static_offset_minutes: -300,    // device thinks it's UTC-5 year-round
  auto_dst: true,                 // compute DST shift per-night
  manual_offset_minutes: 0
}
```

When the operator's actual timezone in May 2026 is UTC-4 (EDT) but the device is UTC-5 (EST), the difference is +1 hour. The `formatWithOffset()` helper in `frontend/src/lib/timeOffset.ts` applies this shift before rendering. With `auto_dst: true`, the shift is computed per-timestamp using the browser's `Intl.DateTimeFormat()` for the operator's locale — handling DST transitions automatically.

**The AI proxy gets the context.** When rendering the system prompt, `prompt.py:_describe_device_clock()` translates this config into a paragraph the LLM can reason about: "The user's CPAP device records timestamps in a fixed UTC offset of -5.0 hours. The UI auto-adjusts for DST. When the user says 'last night', that's in their browser's local time; URSA-OSCAR applies the offset to render. Tool queries that take a date should use the date the DEVICE wrote (which is what's in the DB)."

**Critical invariant:** server data is always device-naive. The shift exists only at display. Exports, API responses, and the data the AI agent sees through tools are all in device-clock. Doing any conversion server-side would silently corrupt the data for users who don't have the offset issue.

---

## 6. Streaming: SSE end-to-end

The chat panel needs real-time response streaming. Three layers of streaming have to cooperate:

### Layer 1: LLM → adapter

The adapter (Claude or OpenAI-compat) opens a streaming connection to the provider. Each adapter exposes an async generator yielding `AiStreamEvent` objects:

```python
async def chat(...) -> AsyncIterator[AiStreamEvent]:
    async with client.stream("POST", url, json=body) as resp:
        async for line in resp.aiter_lines():
            # decode SSE frame, yield normalized event
            yield AiStreamEvent(event_type="text", payload={"text": ...})
```

Different providers have different SSE event shapes — Anthropic emits `content_block_start`, `content_block_delta`, `message_delta`, `message_stop`; OpenAI emits `chunk` objects with `choices[0].delta`. The adapter translates to our normalized set: `text`, `tool_call_start`, `tool_call_input`, `tool_call_complete`, `tool_result`, `complete`, `error`.

### Layer 2: adapter → server-side tool loop → client

The API endpoint (`api/ai.py:chat`) wraps the adapter iteration in a multi-turn loop:

```python
async def event_generator():
    messages = list(req.messages)
    for loop_n in range(8):
        pending_tool_calls = []
        async for event in adapter.chat(messages, tools, system_prompt):
            yield _sse_pack(event)
            if event.event_type == "tool_call_complete":
                pending_tool_calls.append(...)
            elif event.event_type == "complete":
                break
        if not pending_tool_calls:
            return  # done
        # Execute tools, append results, loop back into adapter
        for tc in pending_tool_calls:
            result = await execute_tool(...)
            yield _sse_pack(AiStreamEvent(event_type="tool_result", ...))
            messages.append(AiMessage(role="tool", content=...))
```

Each iteration of the outer loop is one LLM turn. When the LLM emits a tool call, the server executes it, emits a `tool_result` event for the UI, appends the result to the conversation, and loops back into `adapter.chat()` to continue. The 8-iteration cap is a safety against runaway tool loops (small models have been observed to chain-call indefinitely).

### Layer 3: API → web → browser

The browser uses `fetch()` + `ReadableStream` to consume the SSE stream (not `EventSource`, because `EventSource` doesn't support POST bodies):

```typescript
const resp = await fetch(`${BASE}/ai/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
});
const reader = resp.body!.getReader();
const decoder = new TextDecoder();
let buffer = '';
while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 2);
        if (!frame.startsWith('data: ')) continue;
        yield JSON.parse(frame.slice(6)) as AiStreamEvent;
    }
}
```

### nginx buffering hazard

The web container's nginx is in the middle. By default nginx **buffers** proxy responses, which would defeat the SSE point — the browser would see nothing until the entire LLM response finished. Two safeguards:

1. The API sets `X-Accel-Buffering: no` on the response (nginx's documented way to opt out per-request)
2. The nginx config has `proxy_read_timeout 300s` so a long-running stream doesn't get cut

In practice on real homelab hardware this Just Works — the SSE chunks arrive in the browser within ~50ms of the API forwarding them.

---

## 7. Encrypted secrets: where to draw the line

Phase 5 needed to store API keys (Claude, OpenAI, Gemini, etc.). Three options:

1. **Plain JSON in `/data/secrets.json`** — simplest, but anyone with filesystem access reads keys. For a homelab on a private NAS this is *fine* but feels wrong.
2. **OS keyring (`keyring` Python lib)** — best for desktop apps. Doesn't translate to Docker containers (no OS keyring inside the container; bind-mounting the host's keyring is gross).
3. **Symmetric encryption at rest with an operator-managed master key** — the Fernet approach.

We picked option 3 (Decision 7). Trade-offs:

- **Pro:** Defense-in-depth. Even if someone gets a copy of `/data/secrets.enc`, it's useless without the master key.
- **Pro:** Master key lives in environment variable — the same place the rest of URSA-OSCAR's secrets live (MCP bearer, OAuth client secret). Operationally consistent.
- **Con:** First-start UX has a manual step. The API generates the key on first boot, writes to `/data/secret_key.gen`, and logs a warning. Operator copies into compose env, redeploys, deletes file. Two-step but documented.
- **Con:** If the operator loses the master key, all stored secrets become inaccessible. We treat that as a re-enter-your-keys event, not a data loss event — the secrets themselves aren't generative.

### Threat model boundaries

URSA-OSCAR's threat model is **single operator on a private network**. We don't defend against:
- Compromised TrueNAS host (everything's accessible at that point)
- Compromised browser (the LLM keys are sent over HTTPS to the cloud provider anyway)
- LAN-side passive eavesdropping (TLS via Cloudflare Tunnel or similar handles this)

We DO defend against:
- Bind-mount drive removed + read offline → secrets are encrypted
- Backup files copied off-NAS → same
- Read-the-DuckDB → secrets aren't in DuckDB
- Misconfigured nginx that accidentally serves /data/* → secrets are encrypted, plus they're in `.enc` and `.gen` files (no plaintext)

The Settings UI never sees secret values. Only `api_key_set: bool` per provider. The "Replace key" affordance lets the operator overwrite without seeing the prior value.

---

## 8. Session exclusion: the recompute pattern

Phase 4 Ticket 1 introduced session-level exclusion. The UX: operator unchecks a session in the Daily View → night's AHI / mask-on / event counts recompute from the remaining sessions.

The implementation introduces a pattern worth understanding because it'll repeat in future analytics features.

### The data shape

- `sessions(date, session_id, start_ts, end_ts, mask_on_minutes)` — one row per non-empty CPAP session. Populated by the importer.
- `excluded_sessions(date, session_id, excluded_at)` — operator's exclusion list. Inserts = exclude; deletes = re-include. PK is `(date, session_id)`.
- `nightly_summary` — *cached* aggregate. Source of truth is `sessions` + `nightly_events` + `*_timeseries` + `excluded_sessions`.

### The recompute math

`analytics/recompute_summary.py:recompute_for_date(date)`:

1. Load all sessions for the date (LEFT JOIN excluded_sessions → `excluded: bool`)
2. Filter to non-excluded
3. Sum `mask_on_minutes` → `total_time_minutes`
4. min/max `start_ts`/`end_ts` → `start_time`/`end_time`
5. Filter `nightly_events` WHERE `session_id IN (non-excluded ids)` → recount per-type → re-derive per-hour indices
6. Filter `*_timeseries` to rows with timestamps between non-excluded session intervals (multi-range BETWEEN) → recompute percentiles + leak redline math
7. Preserve equipment fields (machine_model, mode, EPR level, …) from existing row
8. UPSERT `nightly_summary`

### The math correctness invariant

When zero sessions are excluded, `recompute_for_date()` must produce a `NightlySummary` numerically equivalent to what the importer originally wrote (modulo `last_updated` + float precision in percentile round-trip). This is locked down by `test_recompute_with_no_exclusions_matches_original`. Any future change to either the importer's math OR the recompute's math that breaks this invariant fails the test loudly.

### Why "recompute from cached aggregates" rather than "always recompute from raw"?

Because the recompute is *cheap* once the sessions table is populated. Reading `nightly_events` filtered by `session_id IN (...)` is a single indexed query. Reading the time-series tables for percentiles is the bigger cost but still <100ms even on a full night's 25-Hz waveforms.

We deliberately do NOT re-parse EDFs. The importer is the only path that touches raw EDF; from there on, the database is canonical. This means:
- The recompute works against zero-EDF-on-disk databases (after the operator deletes the SD card)
- It runs in ~50ms even on a multi-session night
- It's easy to test (no EDF fixture required for recompute tests)

### Re-import preserves exclusions

When the importer re-parses a night (force re-import), it writes a fresh "all sessions included" `nightly_summary`. After the per-night write, if `excluded_sessions` has rows for that date, the importer calls `recompute_for_date()` to re-apply the operator's exclusions. End result: exclusions survive re-imports.

---

## 9. Watcher: fingerprint + quiescence

Phase 4 Ticket 3 activated the watcher container. The design avoids two failure modes:

1. **Trigger while a copy is still in progress.** If you `rsync` an SD card to the bind-mount, files arrive over seconds. An overeager watcher would fire mid-copy, the importer would see a partial DATALOG, and we'd write incomplete data.
2. **fsevents over network filesystems are unreliable.** Watchdog's inotify-based monitoring doesn't work reliably over SMB / NFS, which is what most homelab bind-mounts use.

Solution: **fingerprint-based polling + quiescence window**.

### Fingerprint shape

```python
Fingerprint = tuple[tuple[str, float], ...]
```

For each immediate child of the `DATALOG/` directory:
- name (`"20260513"`)
- `max(dir_mtime, newest_file_mtime)` (one float)

Sorted by name. The whole tuple is the fingerprint.

We use `max(dir_mtime, file_mtime)` because Windows updates directory mtime asynchronously after file writes — two consecutive scans of an idle tree return different fingerprints if we track them separately, and the watcher never reaches quiescence. Collapsing to `max()` converges as soon as the file write completes. (We caught this bug with a deterministic test failure during Phase 4 build.)

### The loop

```
loop forever:
  fp = compute_fingerprint(watch_path)
  if fp != last_fp:
    last_fp = fp
    last_change_time = now()
    log("fingerprint changed")
    continue
  
  if last_change_time and (now() - last_change_time) >= quiescence_seconds:
    if not tracked_job_id:
      job = POST /imports
      tracked_job_id = job.id
      last_change_time = None  # don't re-fire until fresh change
  
  if tracked_job_id:
    job = GET /imports/jobs/{id}
    if job.status in TERMINAL:
      if webhook_url:
        POST webhook_url with completion payload
      tracked_job_id = None
  
  sleep(poll_interval)
```

The single-tracked-job state prevents duplicate enqueue while a job is in flight. The watcher trusts the API's `skip_existing` to deduplicate at the data layer, so even multiple enqueues of the same source would only re-import once. The single-tracked-job is more about UI clarity ("don't show 6 'queued' jobs when 5 of them will no-op").

---

## 10. Frontend state: deliberately small

The React app has three categories of state:

1. **Server data, re-fetched per page** — `nightly_summary`, events, time-series, sessions, profile, AI config. No global cache (no TanStack Query — see ADR-001). When you navigate to Daily View, it re-fetches. Cheap; the data isn't huge.

2. **Local component state** — chart hover, modal open/close, edit-form drafts. Plain `useState`. Resets on remount, which is fine.

3. **Persisted browser state** — three localStorage keys:
   - `ursa_oscar_daily_compact` — Phase 4 Compact view toggle
   - `ursa_oscar_chat_{date}` — Phase 5 chat conversations (per Daily View date)
   - The DeviceClock config is stored server-side in `profile.json`, not localStorage; the frontend reads it via `GET /profile` and caches in component state during Daily View's render

No Redux, no Zustand, no Context (beyond what React Router provides). The architect's ADR-001 was opinionated about this: the app's state surface is small enough that hand-rolled `useState` + targeted re-fetches read better than introducing a state-management library that everyone learning the codebase would have to learn first.

### When this stops working

If we ever add cross-device conversation sync (Phase 6+ feature), the chat state has to move server-side. At that point introducing a state library is worth re-evaluating — but the architecture today doesn't need one.

---

If you find the architecture wanting in a specific way and have ideas, open an issue. The maintainers are willing to revisit any of these decisions if there's a concrete reason. Specifically: the no-Tailwind, no-shadcn, no-state-library decisions are *opinions*, not laws. They're documented in ADRs but ADRs can be superseded.
