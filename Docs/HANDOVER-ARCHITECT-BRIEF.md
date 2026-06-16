# Architect brief — Fitbit / Google Health modernization

**Audience:** technical architect reviewing the project framing before a developer starts work.
**Companion doc** (for the implementing developer, not required reading for the architect): [HANDOVER-FROM-URSA.md](HANDOVER-FROM-URSA.md). Reference it if you want the long version.

---

## Context

I have two working homelab projects:

- **URSA-OSCAR** (1.1.7, publicly released June 2026) — self-hosted CPAP analytics with optional AI-assistant integration. Four-container Docker stack: FastAPI + DuckDB + React/uPlot + FastMCP. Single-tenant, operator-owned, no cloud backend. Public repo: `burrellka/URSA-OSCAR`.
- **fitbitkb** — older Fitbit Web API analytics codebase. Generates a 16-page static "Wellness Report" PDF covering HR, sleep, activity, body composition, HRV, breathing rate, SpO2, etc. The underlying logic (database, services, calc, caching, MCP server) is working and worth preserving.

**The catalyst:** Fitbit Web API + OAuth are shutting down in **September 2026** (Google migration to Health Connect / Google Health REST). Forces a migration anyway. I'm taking the opportunity to modernize the UX at the same time.

## Mandate

**Port the existing fitbitkb to URSA-OSCAR's UI shell. Keep its underlying engine intact.** Specifically:

- KEEP: existing database, services, cache, calculation logic, MCP server, the Reality Score / Proxy Score sleep methodology
- REPLACE: API client (Fitbit Web API → Google Health), UI shell, navigation pattern, visual language, charting library
- ADD: URSA's AI assistant (in-app + external claude.ai connector), profile system, in-app help, methodology-disclosed PDF reports, segmented multi-page navigation (instead of "one long report")

Inheriting URSA's architectural posture:
- Four-container Docker stack (api / web / mcp / sync) — same shape
- Auto-managed secrets at first boot (Fernet master key, JWT, service tokens) — no operator key ceremony
- Single bind-mounted `/data` volume — persistence boundary
- Single-tenant trust model — no multi-user, no email recovery, no cloud sync, no telemetry
- Version-introspection (each container reports own version; UI chips read those)
- MCP-as-thin-proxy (tools call REST, never reach into DB)
- Public-friendly docker-compose with per-OS install walkthroughs

## Decisions I need your sign-off on

For each: my proposed direction + the reasoning + the alternative. Push back where you disagree.

### 1. Standalone product or feeder into URSA's MCP?

**Proposing: standalone with its own MCP connector.** Operators run it parallel to URSA-OSCAR; claude.ai gets two Custom Connectors (one per project) and Claude correlates across them in conversation. Cleaner trust boundary (each project owns its own auth, secrets, OAuth client). Cost: operator has to register two connectors.

Alternative: feeder — extend URSA's MCP with Fitbit/Health tools. Single connector, smoother Claude UX, but couples release cycles and blurs the trust boundary.

### 2. Google Health Connect vs. Google Health REST API?

**RESOLVED — not actually a decision (architect note, 2026-06-08).** This was a false binary conflating two unrelated Google products. **Health Connect** is an on-device Android data store — not our target. The Fitbit Web API successor is the **Google Health API**, live now at `https://health.googleapis.com/v4/` — the cloud-to-cloud, account-centric REST surface. Exactly one viable path: migrate to Google Health API v4. No deliberation required. (Note for production, not testing: all Google Health API scopes are classified *Restricted* and require a privacy/security review before non-personal use.)

**What IS a live risk (this replaces the original question):** metric parity is *not* guaranteed at launch. Multiple sources confirm some Fitbit metrics are unavailable from Google v4 today (HR webhooks specifically). This threatens the crown-jewel data — sleep stages and HRV. The real day-0 task is therefore a **parity audit via a standing test harness** that calls both APIs live and value-diffs them. Harness + a code-grounded checklist are already built at `c:\dev\fitbit-web-ui-app\tools\parity-harness\`. We do not write the adapter until the harness shows every metric we ship is present AND value-faithful; until then the old app keeps running.

### 3. Migration boundary with existing fitbitkb code

**Proposing: surgical migration, not rewrite.** Audit fitbitkb on Day 1; identify what survives the port. Swap the API client cleanly (vendor adapter pattern), keep the DB schema if it makes sense, port the calc logic intact. Layer URSA's UI shell on top.

Alternative: clean rewrite, harvest concepts only. Higher cost, longer timeline, but removes any Fitbit-Web-API-shaped technical debt.

### 4. Time-series storage strategy

The existing fitbitkb has its own DB (NOT DuckDB — confirmed; details TBD on audit). Worth re-evaluating against multi-year wearable scale:

- HR @ 5-60s cadence over 5 years: ~3M-32M points
- Steps @ 1-min over 5 years: ~2.6M
- Sleep stages @ 30s over 5 years: ~5M
- Weight: episodic, trivial

Total volume is comfortable for most modern DBs. **Want your read on whether to keep the existing storage shape or modernize.** My instinct: keep it unless the audit reveals real friction.

### 5. Multi-profile vs. multi-instance for households

URSA chose multi-instance (one CPAP user = one Docker stack) for trust-boundary cleanliness. Households with two Fitbit users could go either way — actual multi-profile in one instance, or two instances on different ports.

**Proposing: two instances**, matching URSA's pattern. Consistency, simpler auth model, snapshot-based backups stay coherent per-user. Open to multi-profile if you see a strong reason.

## What we are NOT doing (preempting scope discussion)

- No cloud sync, no email recovery, no user registration — breaks the homelab trust posture
- No multi-tenancy — household = multiple instances
- No telemetry, no analytics-on-analytics — operator owns their data, nothing leaves
- No support for non-Google wearables in v1 (Apple Health, Garmin, Whoop) — possibly in v2; out of scope for this migration
- No mobile-native app — browser-based, responsive UI, accessible from phone/tablet via LAN or reverse proxy

## Proposed first-week shape

| Day | Output |
|---|---|
| **0 (GATE)** | **Secrets + parity gate before any code.** (a) Rotate the old Fitbit refresh token + OAuth client secret — treat as compromised; the Google OAuth migration forces this anyway. (b) Confirm clean-history plan for the new repo: `git init` fresh, never copy the old `.git`; `.gitignore` ships before first commit (already done at `c:\dev\fitbit-web-ui-app\.gitignore`). (c) Stand up the parity harness and capture a first run. Nothing proceeds until these clear. |
| 1 | Dev audits fitbitkb — "what survives, what changes, what's new" memo |
| 2-3 | Four-container scaffolding stood up; one URSA-styled chart rendering one real data series |
| 4-5 | Left-rail nav skeleton with all sections placeholder-rendered; URSA theme + chart conventions locked |
| EOW1 | Full vertical slice of one section (proposing Sleep — highest content density in the current report) tagged `0.1.0`, demo-able |

## The ask

Skim this brief (~5 min). Skim the companion HANDOVER doc if you want depth on the URSA side (~20 min). Send back:

1. Sign-off or pushback on each of the 5 architectural decisions
2. Anything I'm missing — Google Health API surface, time-series patterns, trust boundary considerations, deployment topology
3. Anything in the "not doing" list you'd argue should be reconsidered

Once I have your read, dev proceeds with the audit and we're off.

— Kevin
