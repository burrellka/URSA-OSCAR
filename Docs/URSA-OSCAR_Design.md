# URSA-OSCAR — Refined Design Document

**Version:** 1.2
**Date:** May 12, 2026
**Status:** Phase 1 complete (deployed, 9/9 acceptance gate green). Phase 1.5 pending. Phase 2 cleared after Phase 1.5.
**Supersedes:** v1.1 (May 11, 2026)
**Extends:** `URSA-OSCAR_Framework.md` v1.0

---

## Changelog from v1.1

| Change | Reason | Reference |
|---|---|---|
| Decision 2 partially superseded by ADR-003 | DuckDB cross-process lock model more restrictive than Decision 2 assumed; MCP container can't open DuckDB while API holds write lock | ADR-003, audit Finding 3 |
| `trigger_import` promoted from Tier 3 to Tier 1 | Load-bearing for both URSA agent and Phase 2 UI import flow | Phase 1 build experience |
| Web port corrected: 5060 → 5063 | Chromium blocks 5060 as ERR_UNSAFE_PORT (SIP) | Phase 1 handover, commit 573fa85 |
| MCP port corrected: 8082 → 8085 | 8082 conflict with localrecall on kairos-net | Phase 1 handover, commit adb66e8 |
| TrueNAS volume root locked: `/srv/ursa-oscar/` | Production deployment path | Phase 1 handover |
| Canonical targets corrected: 5/8 halved, 5/9 RERA=1 | OSCAR session-duplication quirk on 5/8; my error on 5/9 | Decision 15, Phase 1 architect chat |
| Phase 1 marked complete (9/9 acceptance gate green) | Phase 1 deployed live, validated end-to-end | Phase 1 handover, audit report |
| New Phase 1.5 inserted between Phase 1 and Phase 2 | Wire equipment-settings parsing + add `/api/v1` prefix before frontend work | Architect chat 2026-05-12 |

---

## Document Hierarchy

1. **`URSA-OSCAR_Framework.md`** — Intent and scope.
2. **`URSA-OSCAR_Design.md` (this document)** — Build spec.
3. **`Docs/architect-decisions/adr-NNN-*.md`** — Append-only ADRs.
4. **`Docs/architect-decisions/phase-N-*.md`** — Phase findings + synthesis.
5. **`Docs/NN-*.md`** — Operational docs (audit, handover, MCP contract, architecture, OAuth setup).

---

## Architect Decision Log

### Decision 1 — Analytics core: port OSCAR's event detection (scoped)

Unchanged from v1.0/1.1. Phase 1 implementation validated against 4-night regression fixture; all AHI components match OSCAR within 1%. The 5/8 OSCAR session-duplication quirk is handled per Decision 8.

### Decision 2 — Storage: DuckDB embedded, single file (**partially superseded by ADR-003**)

**Original decision (retained for audit trail):** DuckDB as sole storage engine. Embedded library, single file, deployed as dependency of API and MCP containers. No separate database container.

**Superseded element:** "MCP container: read-only (URSA agent queries)" was incorrect. DuckDB's cross-process lock model rejects read-only opens from any process when the file is held in write mode by another. Concurrent reads work only within a single process.

**Resolution (ADR-003):** MCP container does not open DuckDB. All MCP tools call HTTP endpoints on the API container over `kairos-net`. API is the sole DuckDB owner. ~5-10ms additional latency per tool call; simpler concurrency story. See `Docs/architect-decisions/adr-003-mcp-as-api-proxy.md`.

**What stays:** DuckDB as the storage engine, single file, embedded in the API container only. Storage characteristics (~3 MB/night compressed, ~1.5 GB/year, sub-second analytical queries) unchanged.

### Decision 3 — Daily View charting: 1:1 OSCAR parity

Unchanged. uPlot, all 8-10 stacked synchronized tracks, shared X-axis zoom/pan, event overlays. Target for Phase 2.

### Decision 4 — Phase scope: unified per framework

Unchanged. Phase 2 ships Daily View + Overview + Statistics + basic CSV export in single sweep.

### Decision 5 — MCP transport: FastMCP + SSE over HTTP

Unchanged. FastMCP 3.2.4 + mcp 1.27.0 pinned. SSE transport. OAuth 2.1 + bearer fallback per ADR-002. Phase 1 implementation deployed and validated.

### Decision 6 — Cross-source correlation: agent-mediated

Unchanged. `correlate_with_external` not on URSA-OSCAR tool surface.

### Decision 7 — Frontend stack (resolved v1.1)

Unchanged. React 18 + TS + Vite + hand-rolled CSS custom properties, copying APEX's `index.css` verbatim plus URSA-OSCAR chart palette additions. ADR-001.

### Decision 8 — 5/8 night-assignment quirk handling

Unchanged. Summary CSV double-counted events on nights where EVE/PLD file start times differed (5/8 specifically: 35-second skew triggered OSCAR's session-splitter to create two synthetic sessions, every event attributed to both). Parser-found values are ground truth. Documented in `canonical_targets.py` with provenance comments.

### Decision 9 — Architect/decision document conventions

Unchanged. Append-only decision log; ADRs in `Docs/architect-decisions/` capture evolutions.

### Decision 10 — Adopt APEX MCP server template wholesale

Unchanged from v1.1. Template lifted into Phase 1. Pinned versions per ADR-002. Auth posture (DCR off, fail-fast env vars, static bearer + OAuth 2.1, constant-time bearer comparison) inherited. Phase 1 validation: 6/6 in-process auth boundary tests pass; 4/4 per-deploy curl checks pass.

### Decision 11 — Docker network: join `kairos-net`

Unchanged. Production stack deployed on kairos-net alongside APEX. URSA agent reaches both via existing network.

### Decision 12 — Backend framework: FastAPI

Unchanged. Phase 1 implementation uses FastAPI; OpenAPI auto-generated. Blueprints-as-modules pattern inherited from APEX (`backend/src/ursa_oscar/api/<resource>.py`).

### Decision 13 — LAN dev-bypass port (dev compose only)

Confirmed. Dev compose exposes port 5065 (unauthenticated read-only API). Production compose has no API host port. Pattern inherited from APEX (which uses 5055).

### Decision 14 — Public hostname: reserved, TLS in Phase 4

**Updated v1.2:** Public hostname `your-public-host.example.com` is **live as of Phase 1**, fronted by Cloudflare Tunnel. TLS termination working. claude.ai connector registered and serving. This decision is effectively complete; the original v1.1 wording deferred TLS work to Phase 4 but operational priorities accelerated it.

Frontend public hostname (e.g., `ursa-oscar.example.com`) still deferred — LAN-only via `http://192.168.x.x:5063/` is sufficient through Phase 2.

### Decision 15 — Regression fixture minimum: 4 nights (with corrections)

**Updated v1.2:** Canonical targets corrected. Phase 1 regression suite (24 tests) passes against the corrected table on all 4 nights.

**Corrected canonical targets (v1.2 authoritative):**

| Date | Sessions | Total Time (min) | AHI | CA | A | OA | H | RERA | Med Pressure | 95% Pressure |
|---|---|---|---|---|---|---|---|---|---|---|
| 5/7 | 3 | 409 | 11.736 | 77 | 0 | 3 | 0 | 0 | 6.96 | 8.82 |
| 5/8 | 2 | 409 | ~11.3 (recompute from halved counts) | 46 | 2 | 28 | 1 | 0 | 7.36 | 9.94 |
| 5/9 | 1 | 423 | 7.376 | 47 | 0 | 3 | 2 | **1** | 7.32 | 9.32 |
| 5/10 | 2 | 441 | 3.129 | 17 | 0 | 4 | 2 | 1 | 7.54 | 9.50 |

**5/8 corrections:** Halved per OSCAR's session-duplication quirk (35s skew between EVE and PLD file starts caused OSCAR's session-splitter to double every event count). Parser-found values are 47 CA / 28 OA / 2 A / 1 H — those are the ground truth. AHI recomputes proportionally.

**5/9 correction:** RERA was incorrectly 0 in v1.1 Decision 15. Both the Daily Details CSV and Summary CSV "RE Count" column show 1 RERA; the AirSense 11 emits this as an "Arousal" event class which OSCAR maps to "RERA" (AASM-aligned label translation).

**Label mapping addition:** `"Arousal" → "RERA"` formalized in the EDF parser. Phase 1 implementation includes this mapping; validated via cross-check on 5/10 (parser found 1 Arousal, canonical target was 1 RERA, match confirmed after mapping).

### Decision 16 — Phase 1.5: equipment-settings parsing + API versioning (NEW v1.2)

**Decision:** Insert a Phase 1.5 mini-sprint between Phase 1 and Phase 2 covering two items:

1. **Wire equipment-settings parsing.** STR.edf and SETTINGS/CurrentSettings.json are present at `/cpap-import` root but the parser doesn't consume them yet. As a result, 7 columns in `nightly_summary` (machine_model, mode, min_pressure_setting, max_pressure_setting, epr_level, ramp_time_minutes, humidity_level, mask_type) stay NULL on every row. Phase 2's Daily View needs these to render the equipment-settings panel correctly.

2. **Add `/api/v1/` prefix.** Phase 1 endpoints live at `/api/*`. Adding versioning before Phase 2's frontend wires against the API costs nothing; adding it after means a frontend refactor. Existing endpoints become `/api/v1/night/{date}`, `/api/v1/events`, `/api/v1/nights`, `/api/v1/imports`, `/healthz`. The `/healthz` endpoint stays unprefixed (operational convention).

**Rationale:** Both items are pure additions / non-breaking refactors. Both have meaningful downstream impact on Phase 2 quality. Combined effort: 1-2 hours of Claude Code work. Deferring either creates Phase 2 rework debt.

**Acceptance criteria for Phase 1.5:**

1. STR.edf parsed; `nightly_summary` rows populated for all relevant equipment-setting columns on a fresh import of the 4 fixture nights.
2. SETTINGS/CurrentSettings.json parsed where present.
3. All Phase 1 endpoints move to `/api/v1/*` (except `/healthz`).
4. MCP tools updated to call the new versioned endpoints.
5. Regression suite still passes (24/24).
6. Auth boundary tests still pass (6/6).
7. Verify-mcp-live still passes (4/4).
8. Docker images bumped to 0.2.0 (api, mcp, web).

---

## Technology Stack (Locked v1.2)

Unchanged from v1.1 except:

| Layer | Technology | Notes |
|---|---|---|
| Storage | DuckDB (embedded, **API container only**) | Per ADR-003 — MCP container doesn't open DuckDB |
| MCP tool execution | HTTP calls to API container over kairos-net | Per ADR-003 |

All other rows unchanged.

---

## MCP Tool Surface (v1.2)

**Tier 1 — Read tools + import (8 tools):**
- `get_nightly_summary(date, end_date=None)` ✅ deployed
- `get_ahi_breakdown(date, end_date=None)` ✅ deployed
- `get_event_distribution_by_hour(date, event_types=None)` ✅ deployed
- `get_pressure_profile(date, end_date=None)` ✅ deployed
- `get_leak_profile(date, end_date=None)` ✅ deployed
- `get_session_breakdown(date)` ✅ deployed
- `list_available_nights(start_date=None, end_date=None, filter_expression=None)` ✅ deployed
- `trigger_import(source_path="/cpap-import")` ✅ deployed (**promoted from Tier 3 in v1.2**)

**Tier 2 — Analytical (Phase 3):** `compare_periods`, `analyze_correlation`, `get_trend`, `get_manual_log_summary`

**Tier 3 — Operational (Phase 3-4):** `add_manual_log` (Phase 3), `inspect_schema` (Phase 4), `run_sql_query` (Phase 4), `get_import_status` (Phase 4), `export_data` (Phase 4)

Full contract in `Docs/03-mcp-tool-contract.md`.

---

## Production Topology (deployed as of Phase 1 close)

```
Internet ──HTTPS──> Cloudflare Tunnel ──> 192.168.x.x:8085
                                                │
                                                ▼
                                       ursa-oscar-mcp container
                                       (FastMCP + OAuth, no DuckDB)
                                                │
                                                ▼ HTTP over kairos-net
                                       ursa-oscar-api container
                                       (FastAPI + DuckDB owner)
                                                │
                                                ▼
                                       /srv/ursa-oscar/data/ursa-oscar.duckdb

Side cars:
  ursa-oscar-web container     (port 5063, placeholder until Phase 2)
  ursa-oscar-watcher container (idle scaffold until Phase 4)

Public:  https://your-public-host.example.com/sse
LAN MCP: http://192.168.x.x:8085/sse
LAN Web: http://192.168.x.x:5063/
```

All four services on `kairos-net`. API has no host port (internal only). MCP has the only public host port + Cloudflare passthrough.

---

## Repository Structure

Per v1.1 with minor adjustments per actual Phase 1 build:
- Added `Docs/03-mcp-tool-contract.md`, `Docs/12-audit-report.md`, `Docs/14-current-architecture-and-filelist.md`, `Docs/15-build-handover.md`, `Docs/17-oauth-setup.md` (operational docs, APEX numbering convention)
- `mcp-server/src/ursa_oscar_mcp/__main__.py` exists (Phase 1 fix for tools/list duality bug; see handover deviation 2)
- `infra/verify-mcp-live.sh` exists (per-deploy curl check)
- Frontend remains placeholder; Phase 2 work fills it

---

## Phase Plan

### Phase 0 — Codebase Inspection ✅ COMPLETE

Findings in `Docs/architect-decisions/phase-0-*.md`. ADRs 001 + 002 accepted.

### Phase 1 — Core Ingestion + MCP ✅ COMPLETE

9/9 acceptance gate green. Deployed live. ADR-003 covers the one architectural deviation (DuckDB lock → API-proxy). Audit report in `Docs/12-audit-report.md`. Build handover in `Docs/15-build-handover.md`.

### Phase 1.5 — Equipment-settings parsing + API versioning (NEW v1.2)

Per Decision 16. Two items, 1-2 hours of work. Acceptance criteria in Decision 16.

**Prerequisite for Phase 2 kickoff.**

### Phase 2 — Web UI: Daily View + Overview + Statistics + Export

Per v1.0/1.1. Acceptance criteria unchanged. **Add:** import UI flow as part of Phase 2 critical path — use it to import the held-back 5/9 + 5/10 nights as the live UI test.

Frontend stack per ADR-001 (React 18 + TS + Vite + custom CSS, copy APEX's `index.css`).

API endpoints to wire against (post Phase 1.5):
- `GET /api/v1/nights` and `GET /api/v1/nights?start=&end=`
- `GET /api/v1/night/{date}`
- `GET /api/v1/events?date=` (with optional `event_type=`)
- `POST /api/v1/imports` (for import UI)
- `POST /api/v1/exports` (stub today; CSV in Phase 2)

### Phase 3 — Manual Logging + Trends

Per v1.0. Adds Tier 2 MCP tools.

### Phase 4 — Automation + External Integration

Per v1.0. Adds watcher service activation + Tier 3 operational tools.

### Phase 5 — Advanced Analytics

Per v1.0.

---

## Acceptance Gate (Phase 1) — Final Results

All 9 criteria green per `Docs/12-audit-report.md`:

1. `make test` — regression 24/24 + 5/8 quirk handled ✅
2. `make verify-mcp` — auth boundary 6/6 ✅
3. `make verify-mcp-live HOST=...` — 4/4 against both LAN and public URLs ✅
4. URSA agent → `get_nightly_summary("2026-05-08")` returns full data ✅
5. All 8 Tier 1 tools surfaced in claude.ai with rich docstrings ✅
6. 4-night import <60s ✅ (observed 6-15s)
7. DuckDB ≤20 MB after fixture import ✅ (observed ~120 KB without timeseries)
8. `grep -i token` on MCP stderr empty ✅
9. Dev curl works without auth in dev compose; prod returns 401 or refused ✅

---

## Risk Register (Updated v1.2)

| Risk | Status | Notes |
|---|---|---|
| OSCAR parity harder than expected | **Resolved** | 4/4 fixture nights match within 1%. The 5/8 quirk was caught and handled. |
| ResMed firmware changes EDF layout | Open | Phase 1.5 STR.edf work introduces firmware version detection. |
| DuckDB cross-process locks | **Resolved** | ADR-003 — API-proxy architecture. Decision 2 partially superseded. |
| uPlot rendering 720k × 8 tracks | Open | Phase 2 concern. Mitigation in place (downsampling for non-zoomed view). |
| 4-night regression set has blind spots | Open, monitored | Suite grows organically. Phase 2 UI import of 5/9 + 5/10 doubles the live data set. |
| `__main__`/import duality bugs in MCP | **Resolved + documented** | Pattern + diagnostic shortcut captured in handover. |
| Bearer/secret exposure in logs | Monitored | `grep -i token` runs clean post-deploy. Rotation procedure in `Docs/17-oauth-setup.md`. |
| MCP/API latency from API-proxy hop | Open, low | ~5-10 ms observed. Revisit if user-perceptible. |
| Backup cron not yet running | Open | Phase 4. `make backup` exists, scheduler doesn't. |

---

## Document Maintenance

Architect decisions append-only. Superseded decisions retained with supersedence marker (see Decision 2 for the canonical example). ADRs append-only.

---

**End of Design Document v1.2**
