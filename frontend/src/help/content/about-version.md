# Version and release notes

URSA-OSCAR ships as four Docker images that are versioned together. The version chip on the Settings → Configuration page shows the exact tag your stack is running.

## Current version

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
- **1.1.8** — Friendlier MCP misconfig handling + unmapped event label diagnostic (this release)

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
