# Version and release notes

URSA-OSCAR ships as four Docker images that are versioned together. The version chip on the Settings → Configuration page shows the exact tag your stack is running.

## Current version

**1.1.13** — Stable-prefix caching for the AI proxy system prompt. Second-turn latency drops sharply on llama.cpp / LocalAI when its cross-request prefix cache is on.

llama.cpp (and every runtime that inherits its prefix / KV cache) reuses the KV state of the leading byte-identical run of tokens across requests. The match starts at position 0 and stops at the first differing byte — so anything volatile at the FRONT of the system prompt (a clock, a live location, a per-turn UUID) invalidates the cache for everything behind it. URSA's system prompt was almost all stable, but ended with a `Current viewing: <navigation state>` line that changed when the operator moved between Daily View / Trends / a specific night. That one volatile tail was near the front of the assembled prompt in 1.1.12 because the tool index (also stable) was appended AFTER it. Every turn re-read every byte after the volatile line — the persona template, the user profile, the tool index. On a Gemma-4-class CPU box this was a real chunk of TTFT.

The 1.1.13 fix splits the template into `render_system_prompt_parts() → (stable, volatile)` and reassembles as: `[stable persona + profile + date] + [stable AVAILABLE TOOLS index] + [volatile "Current viewing: …"]`. Every stable byte now precedes the volatile tail. On turn 2 of the same session, llama.cpp's cache hits the entire ~3,900-token leading run and only reprocesses the ~50-byte volatile line plus your new message. `render_system_prompt()` is retained as a backward-compat façade returning `stable + "\n\n" + volatile` so any caller that doesn't care about caching still gets a single string with unchanged content. Pattern lifted directly from KAIROS's `docs/stable-prefix-caching-for-sibling-devs.md` (their D74); URSA is the first sibling to adopt.

Byte-stability is guaranteed by a 6-test audit in `backend/tests/unit/test_stable_prefix_caching.py`: the stable prefix hashes SHA256-identical across two turns with different `current_view`, the volatile suffix actually differs, the volatile template can only reference `current_view_context` (no `{today_date}`, no profile, no clock), the backward-compat façade still returns `stable + "\n\n" + volatile`, `today_date` stays in the stable half (a per-day value is stable across a chat session and re-caches once at midnight — acceptable), and the chat endpoint's assembly order puts every stable byte before the volatile tail.

Two things have to hold for the win to actually land in production: the code side (guaranteed by the tests) AND the inference server's cross-request prefix cache turned on. For LocalAI that's a server-config concern (`--parallel` + slot cache, or the `promptcache` option in its config). If your code side is on 1.1.13 but turn-2 latency doesn't drop, the engine cache is off.

The `arch-ai-context` Help topic is updated with the stable-prefix explanation and the "code side ≠ engine side" gotcha.

Reference: KAIROS `docs/stable-prefix-caching-for-sibling-devs.md` + `docs/heavy-tool-architecture-for-sibling-devs.md` (read-only cross-project reference — URSA implements its own version). The 1.1.12 progressive-tool-disclosure work is a prerequisite: the tool index is one of the largest single blocks in the stable prefix, so keeping it stable is where most of the caching win comes from.

**1.1.12** — Progressive tool disclosure. Per-turn AI tool tax cut by roughly two-thirds; large context-budget win for local LLM operators.

Prior to 1.1.12, URSA shipped all 15 tool schemas (~5,300 tokens) on every chat turn even when the model only ever called one or two. On a Gemma-4-class local LLM with a modest context window that fixed cost was real latency, not just cost. The 1.1.12 architecture — lifted from KAIROS's progressive-tool-disclosure spec, independently converged with Vitals' equivalent — tiers tools into a small always-on **core** set (get_nightly_summary, get_user_profile, load_tools) and larger **deferred** groups (analytics, trends, advanced-analysis, reports, logs) held behind a compact `AVAILABLE TOOLS` index in the system prompt. The model activates a group on demand via the new `load_tools` discovery tool. For obvious intents ("show me my AHI trend"), a cheap deterministic lexical pre-pass activates the matching group BEFORE the first model call so the extra round-trip is avoided entirely. Typical per-turn tool cost drops from ~5,300 tokens to ~1,000-1,500 tokens.

Implementation: new `ai_proxy/tool_index.py` (DeferredCatalog + build_tool_index + resolver), new `ai_proxy/tool_prepass.py` (stopword-tuned keyword matcher, capped at 2 groups per pre-pass), tool metadata registry in `tools.py` (`TOOL_META` + `GROUP_LABELS` + `core_descriptors()` / `deferred_descriptors()` / `descriptors_by_group()`), chat-endpoint wiring in `api/ai.py` (pre-pass runs before adapter.chat; load_tools calls intercepted and used to mutate active_tools before dispatch). Every commit is atomic and revertable: slice 1 (metadata, no behavior change), slice 2 (load_tools + index + chat integration), slice 3 (lexical pre-pass), slice 4 (this doc update + version bump). Test coverage: 84 pass across tool_index + tool_prepass + ai_proxy + help_no_drift.

The `arch-ai-context` Help topic is updated with the new token math, deferred-group table, and revised "where to cut" advice.

Reference: KAIROS `proxy/src/core/tool_index.py` + `tool_prepass.py` (read-only cross-project reference — URSA implements its own version).

**1.1.11** — Operator-tunable AI request timeout + a new Help topic documenting exactly what URSA sends to the model per turn.

Two operator-facing changes for the AI Assistant. First, the HTTP read timeout for LLM streaming requests is now configurable at Settings → AI Assistant → Request timeout. Range 5-1800 seconds. Leave blank to inherit the family default: 300 seconds (5 minutes) for the Local LLM provider preset, 120 seconds (2 minutes) for cloud providers (Claude, OpenAI, Gemini, OpenRouter, Groq, Custom). Local defaults are longer because thinking-mode local models on CPU can spend 90+ seconds on the chain-of-thought before emitting the first content token; cloud APIs stream within seconds and a long wait usually means a real network problem worth surfacing. The Test connection button uses min(30s, operator setting) so the Settings page doesn't hang for 5 minutes against an unreachable endpoint. The `MaskedConfig` response now includes `effective_timeout_seconds` alongside the operator's stored value so the UI can render "300s (default)" placeholder text.

Second, a new Help topic — *Architecture and deployment → What URSA sends to the AI model* — spells out exactly what data URSA puts into the model's context window on every request. Token accounting for each of the five components (system prompt template ~3,500 tokens, runtime context ~200-500, tool descriptors ~5,300 for the 15 tools, conversation history growing per turn, your new message), where operators can trim if running a small local model, and what URSA explicitly does NOT send. Written for the Gemma-4 / Qwen3 / DeepSeek-R1 local-LLM operator with a fixed context budget who needs to reason about capacity before an extended chat. Content grounded in the actual `prompt.py`, `tools.py`, and adapter source rather than paraphrased.

Also includes the `1.1.11` version bump across all four container images and the `AiProxyConfig` schema extension (`timeout_seconds: int | None`, range-guarded 5-1800). Pydantic validation catches bad input at the API layer; the frontend also validates client-side before PATCH so the operator gets an inline error rather than a 400.

**1.1.10** — Multi-EVE session clustering fix. Closes a silent-data-loss bug where AHI computed to 0 on nights when ResMed recorded multiple near-adjacent sub-sessions within a single mask-on period.

Symptom: a fresh import of a recent night shows AHI = 0 in the app, even though myResMed shows the real number. The bug was in `discover_sessions` (`backend/src/ursa_oscar/analytics/edf_parser.py`). The 1.1.3 fix correctly clustered EDF files within a 30-second window into a single logical session, but the per-cluster bucket used "first-seen wins per kind" merge logic. When ResMed wrote two CSL+EVE pairs in the same cluster (a 20-second mask-on test at 21:59:39, then the real sleep session starting at 22:00:00, sharing one BRP/PLD/SA2 waveform stream), the cluster kept only the first EVE — typically the near-empty test EVE — and the second EVE's 30+ respiratory events fell silently on the floor. AHI = events / hours, so events ≈ 0 produced AHI ≈ 0 even though the session duration was correct.

The fix carries CSL and EVE files as tuples (`csl_paths`, `eve_paths`) on the `SessionEDFs` dataclass rather than single paths. `events_for_session` iterates over every EVE in the cluster, parses each, and merges into a chronologically-sorted event list. The legacy `eve_path` / `csl_path` single-path fields are retained as compat shims pointing at the first file in the cluster (so any code that hasn't been updated to the tuples still works). Waveform files (BRP/PLD/SA2) remain singletons because the device writes one continuous stream per mask-on period regardless of how many CSL/EVE pairs precede it. Verified end-to-end against the operator's June 21 and June 22 SD-card data — both nights now compute correct AHIs (4.66/hr and 1.32/hr respectively) matching myResMed; both were 0 pre-1.1.10. One new regression test (`tests/unit/test_edf_parser.py::test_discover_sessions_preserves_multiple_eves_in_one_cluster`) constructs the failing 2026-06-22 file layout and asserts both EVEs are retained.

**Operators upgrading from 1.1.5-1.1.9 need to re-import affected nights.** The watcher imports new data only; nights that were imported with the pre-1.1.10 importer have the wrong events stored. After redeploying 1.1.10, force a re-import via Settings → Maintenance → Trigger import (or `docker compose restart ursa-oscar-watcher`). For a wholesale re-import: delete the affected `nightly_summary` and `nightly_events` rows from DuckDB and let the watcher process the SD card folder again. The session-boundary fix in 1.1.3 had the same operational shape.

**1.1.9** — **SECURITY**. Closes an authentication-bypass vulnerability in the MCP container. Operators running the MCP add-on must redeploy and rotate secrets.

The MCP container's OAuth provider extends FastMCP's upstream `InMemoryOAuthProvider`, whose `authorize()` method auto-approves with no human-consent step (the upstream docstring is explicit about this: *"Simulates user authorization and generates an authorization code"*). URSA never overrode `authorize`. Combined with the RFC 7591 Dynamic Client Registration endpoint that 1.1.5 enabled by default, this meant any caller who could reach the public MCP URL could `POST /register` to self-register a client, immediately complete the OAuth dance (no human approval required), receive a valid bearer token, and call all 17 MCP tools against the operator's CPAP data. The `client_secret` on the pre-registered claude.ai client was not an effective gate because attackers could mint their own client + secret via DCR.

The 1.1.9 fix makes DCR opt-in (`URSA_OSCAR_MCP_DCR=true`, default off) and adds a redirect-URI allowlist (`URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS`) for the pre-registered client so non-claude.ai MCP clients (KAIROS, etc.) can connect via the shared `client_secret` + their own callback without needing DCR at all. With DCR off, `/.well-known/oauth-authorization-server` no longer advertises `registration_endpoint`, `POST /register` returns 404, the persisted client store at `/data/mcp_oauth_clients.json` is no longer loaded on boot (so any client that self-registered during the 1.1.5–1.1.8 open window is dead), and the only callback URLs that can complete the flow are the operator-configured allowlist. Verification tests assert the secure default; the DCR-on path remains tested via the existing persistence suite. Discovered by the Google Health / Vitals architect during a Vitals security review and brought to URSA because the same upstream library is shared. Two upstream signals were missed when 1.1.5 enabled DCR: the class name `InMemoryOAuthProvider` and the source-level "for testing purposes" docstring. The defense-in-depth follow-up is Cloudflare Access (Zero Trust) in front of the MCP URL so the OAuth provider isn't directly reachable from the public internet.

**Operators upgrading**: redeploy 1.1.9, rotate `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` and `URSA_OSCAR_MCP_BEARER_TOKEN` to fresh random values (any client that registered during the open window has credentials that, while no longer loaded after 1.1.9 boot, were valid at the moment of issuance), delete `/data/mcp_oauth_clients.json` inside the MCP container's volume, and in claude.ai / Claude Code re-add the connector with the rotated credentials. Operators running KAIROS or other non-claude.ai MCP clients need to add their callback URL to `URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS` and reconnect those clients with the rotated `client_secret` instead of DCR self-registration.

**1.1.8** — Friendlier MCP misconfiguration handling + unmapped event label diagnostic.

Two defensive changes surfaced by operators upgrading from earlier compose files. First, the MCP container now prints a clear actionable banner on startup when its required env vars are missing, telling the operator the cheapest fix first (comment out or delete the `ursa-oscar-mcp:` service block) before walking through the OAuth-setup path. Operators upgrading from 1.1.4 or earlier carried forward a compose file where the MCP service was active by default; without the new banner they hit a confusing restart loop and reached for the forum to debug. The banner now puts the no-MCP path first and links to the optional-addon guide for users who actually want claude.ai connector support. Second, the EDF parser now emits a one-time WARN log when it encounters an event annotation label that isn't in `EVENT_LABEL_MAP`. The event is still stored (with event_type = raw label, per the existing fallback), but the WARN line in the api logs surfaces the specific label so operators can report it and the map can be extended. Background: firmware variants emit different raw strings for the same clinical event (e.g. "Hypopnea" vs "Hyp" vs other variants), and URSA's map was curated against one specific AirSense 11's output. Until 1.1.8, the symptom of a label mismatch was "the Events page only shows ClearAirway events even though OSCAR shows Hypopnea and RERA" — diagnosable only via screenshot comparison. Now: `docker compose logs ursa-oscar-api` shows the exact unmapped string.

**1.1.7** — Multi-year archive support for the browser folder-upload import.

The browser-side folder-upload endpoint (`POST /api/v1/imports/upload`) was silently capped at 1000 files by Starlette's default multipart `max_files` setting. A ResMed AirSense night produces 4-7 files (event EDF + breath/pressure/sad waveforms + .crc + .json), so the cap topped out at ~167 nights — about 5.5 months of data. Long-time CPAP users with multi-year OSCAR archives hit it as `400 — "Too many files. Maximum number of files is 1000."` before any of URSA's own sanitization could even run. The endpoint now parses multipart manually via `request.form(max_files=100_000)` instead of using FastAPI's `File(...)` declaration, raising the practical ceiling to 100K files (well past any real CPAP archive — 10 years × 365 nights × 7 files = ~25.5K). The per-file 10 MB size cap, the suffix allowlist, the path-traversal sanitizer, and nginx's 5 GB `client_max_body_size` all remain unchanged. One new regression test sends 1100 synthesized files and asserts the cap doesn't fire. Reported by an Apnea Board tester (Darth Copious) during the public-release shake-out — the same testing session that surfaced the install-doc overhaul shipped between 1.1.6 and 1.1.7.

**1.1.6** — Two MCP-server fixes surfaced by KAIROS (a DCR-registered MCP client connected since 1.1.5): refresh-token cascade-delete on access-token expiry, and a 405-via-redirect-cascade artifact on POSTs missing the trailing slash.

When a DCR-registered MCP client's access token expired naturally (default 1-hour lifetime), the upstream FastMCP `InMemoryOAuthProvider`'s cleanup path cascaded through the access↔refresh map and deleted the associated refresh token as well. Per RFC 6749 §6, refresh tokens are designed to outlive their access tokens precisely so the client can request a new access token after the short-lived one expires. The cascade broke that contract: KAIROS would get a 401 on its expired access token, try to refresh, and URSA's `/token` endpoint would return `invalid_grant: "refresh token does not exist"` because the refresh had been auto-deleted by the prior `verify_token` call. `UrsaOscarOAuthProvider.load_access_token` now overrides the upstream behavior: on natural expiry it removes only the access token plus the now-dangling map entries, leaving the refresh token alive in the store. Explicit revocation paths (`/revoke` endpoint, `revoke_token`, and refresh-token rotation in `exchange_refresh_token`) still cascade as the spec permits.

The MCP container's uvicorn now passes `proxy_headers=True, forwarded_allow_ips="*"`, so Starlette generates trailing-slash redirects (`/messages` → `/messages/`) using the client's original HTTPS scheme instead of the proxy-to-container HTTP hop. Without this, a POST to the non-slash path got a 307 to `http://...`, the client followed HTTPS→HTTP→HTTPS, the redirect cascade dropped the POST body and downgraded the method, and the resulting GET to `/messages/` (POST-only mount) returned 405. KAIROS saw it as a confusing 405 where 401 was expected; with the trailing slash, URSA was already returning 401 correctly — the 405 was purely the redirect artifact.

Three new regression tests cover the natural-expiry preserve-refresh path, the unexpired-access happy path, and the explicit-revocation still-cascades scope confirmation.

**1.1.5** — RFC 7591 Dynamic Client Registration on the MCP server.

Prior versions had DCR disabled — the only OAuth client the MCP server would authenticate was the pre-registered claude.ai client. Any other MCP client (KAIROS, third-party MCP clients, anything that follows the MCP spec's standard discovery flow) was unable to register its own redirect_uri and got `400 Bad Request` at `/authorize`. The MCP spec requires DCR support for general-purpose servers; URSA now provides it. `POST /register` per RFC 7591: caller supplies `client_name`, `redirect_uris`, `grant_types`, `response_types`, gets back a fresh `client_id` + `client_secret`. Registrations persist to `/data/mcp_oauth_clients.json` and survive container restart. The pre-registered claude.ai client is reconstructed from env vars on every boot (env vars remain the source of truth for that one specific client). Operators upgrading from earlier 1.1.x: the MCP container's `/data` mount must change from `:ro` to `:rw` so the JSON file can be written; both compose templates have been updated. 5 regression tests cover DCR registration, persistence across restart, exclusion of the pre-registered client from disk persistence, and graceful handling of corrupt stores.

**1.1.4** — Local-model UX polish.

This is the version that added the malformed-tool-call diagnostic. When an under-capable local model (Qwen3-4b on CPU, etc.) tries to emit a JSON tool-call as text content and gives up after a few characters, the chat panel previously rendered the partial JSON literally (the user saw a confusing single `{` or `{"`). The chat handler now detects this shape (text content under 10 chars starting with `{`, `stop_reason="stop"`, no tool_calls) and surfaces a friendly diagnostic message with concrete next steps (switch to Claude API, use a larger local model, or run on GPU). The version-introspection refactor that landed in 1.1.3 means image-version chips are now self-reporting; operators no longer keep image tags and display env vars in sync.

**1.1.3** — Session boundary fix + thinking-mode model support + version self-introspection.

Fixed: the EDF importer was bucketing files by clock-minute prefix, which split a single ResMed session into two whenever the boot moment straddled a minute boundary (events file at `01:04:53`, waveforms at `01:05:00`, 7 seconds apart but in different minute buckets). Replaced with sliding-window temporal clustering (30-second tolerance). Existing data with the bug needs a force re-import to pick up the corrected session boundaries.

Fixed: the OpenAI-compat adapter silently discarded `delta.reasoning` (Qwen3 via LocalAI / Ollama) and `delta.reasoning_content` (DeepSeek-R1) deltas. Adapter now reads both naming conventions and emits a `reasoning` event the chat panel renders as a collapsible "Reasoning" trail.

Fixed: Settings page image-version chips are now self-introspecting (API reads its own version from packaging metadata; MCP exposes `/version`; watcher writes `/data/versions/watcher.txt` at startup; web bakes its version via Vite at build time). The previous env-var coordination pattern is retained as optional overrides only.

The headline content of the 1.1 release line (in-app Help, `get_help_topic` MCP tool, About modal) was introduced in 1.1.0; the chat-panel auth fix landed in 1.1.1; the Test connection button discipline and refreshed Gemini provider preset landed in 1.1.2.

## Release lineage

The path to 1.0 is captured in the Docs/WIP/ build handovers in the repository. A short summary:

- **Phase 1** (0.1 – 0.4) — Ingestion, schema, basic UI scaffolding
- **Phase 2** (0.5 – 0.6) — Operational polish, Settings page, MCP health check
- **Phase 3** (0.7 – 0.9) — Manual logs, analytics, vocabulary autocomplete, Trends page
- **Phase 4** (0.9.x) — Async import queue, watcher daemon, device clock offset
- **Phase 5** (0.9 – 0.9.10) — AI Assistant in-app chat, multi-provider support, system prompt template
- **Phase 5.5** (0.9.8) — Strict version pinning, per-session pressure cache
- **Phase 6 Ticket 6.1** (0.10) — Multivariate and lag correlation
- **Phase 6 Ticket 6.2** (0.11) — Predictive modeling, counterfactuals
- **Phase 6 Ticket 6.3** (0.12) — Provider PDF reports
- **Phase 6.4 + 6.4.1** (0.13.0 – 0.13.1) — Single-user authentication, auto-managed service tokens
- **0.13.2 + 0.13.3** — Scheme-aware cookie hotfix, Origin/Referer fallback
- **0.13.4** — Usage-rate breakdown, no-session UX clarity
- **0.13.5** — `safe_projection` (sample-size + physical-bounds guards on trend projections)
- **0.13.6** — Anthropic prompt caching on the Claude adapter
- **1.0.0** — Version-only release marking the close of pre-1.0 work
- **1.1.0** — Documentation, Help System, AI integration
- **1.1.1** — Auth fix for in-app chat + `generate_report` MCP tool
- **1.1.2** — Test connection button discipline + refreshed Gemini preset
- **1.1.3** — Session boundary fix + thinking-mode model support + version self-introspection
- **1.1.4** — Local-model malformed-tool-call diagnostic
- **1.1.5** — RFC 7591 Dynamic Client Registration on the MCP server
- **1.1.6** — Refresh-token cascade-delete fix on the MCP server
- **1.1.7** — Multi-year archive support for the browser folder-upload import
- **1.1.8** — Friendlier MCP misconfig handling + unmapped event label diagnostic
- **1.1.9** — **SECURITY**: closes MCP authentication-bypass; DCR opt-in + redirect allowlist
- **1.1.10** — Multi-EVE session clustering fix; AHI=0 on multi-mask-on nights
- **1.1.11** — Operator-tunable AI request timeout + Help topic documenting model-context contents
- **1.1.12** — Progressive tool disclosure (KAIROS pattern) — cuts per-turn tool tax by ~2/3
- **1.1.13** — Stable-prefix caching (KAIROS D74) — reorders system prompt so llama.cpp / LocalAI's cross-request KV cache hits (this release)

## How to check the running version

- **Web UI**: Settings → Configuration → "API image version" / "MCP image version" / "Web image version" / "Watcher image version" chips
- **Docker host**: `docker images brain40/ursa-oscar-* --format "{{.Repository}}:{{.Tag}}"`
- **Compose env**: the `URSA_OSCAR_*_IMAGE_VERSION` env vars in your compose file are the canonical pins

## Upgrade procedure

```bash
docker compose pull
docker compose up -d --force-recreate
```

No URSA-OSCAR release has required data migrations since 1.0 — the DuckDB schema is stable and all auth/secret state is auto-managed. If you skip multiple versions, the upgrade still works because nothing in `/data` is version-coupled.

## Backward compatibility

Within the 1.x line, URSA-OSCAR commits to:

- No breaking changes to the public MCP tool surface (existing tools keep their names, arguments, and response shapes)
- No breaking changes to the public REST endpoints' response shapes (additive fields only)
- No silent schema migrations that change existing column meanings
- No removal of features without a deprecation period

Major version bumps (2.x) would only happen if a breaking change to the data model or API surface becomes necessary. There's no such change currently planned.
