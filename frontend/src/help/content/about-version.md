# Version and release notes

URSA-OSCAR ships as four Docker images that are versioned together. The version chip on the Settings → Configuration page shows the exact tag your stack is running.

## Current version

**1.1.3** — Session boundary fix + thinking-mode model support.

This is the version that fixed two bugs:

1. The EDF importer was bucketing files by clock-minute prefix, which split a single ResMed session into two whenever the boot moment straddled a minute boundary (events file at `01:04:53`, waveforms at `01:05:00`, 7 seconds apart but in different minute buckets). The result was a duplicated session inflating mask-on duration by the length of the affected session. Replaced with sliding-window temporal clustering (30-second tolerance) that handles the device's normal boot-to-waveform offset while still keeping legitimate session restarts (50+ seconds apart) separate. Existing data with the bug needs a force re-import to pick up the corrected session boundaries.

2. The OpenAI-compat adapter silently discarded `delta.reasoning` (Qwen3 via LocalAI / Ollama) and `delta.reasoning_content` (DeepSeek-R1) deltas. Thinking-mode models would emit 80+ seconds of chain-of-thought into a void, then the stream would close without ever surfacing an answer. The adapter now reads both naming conventions and emits a new `reasoning` event type. The chat panel renders these as a collapsible "Reasoning" trail above the assistant's content, open by default while in-flight (so the user sees activity), collapsed once the final answer arrives. Stream timeout bumped to 300 seconds for thinking-mode models.

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
- **1.1.3** — Session boundary fix + thinking-mode model support (this release)

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
