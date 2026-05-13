# Phase 0 — Synthesis: Conflicts, Inheritances, and Escalations

**Inspector:** Claude Code (URSA-OSCAR workspace)
**Date:** 2026-05-11
**Status:** Phase 0 final deliverable. Awaiting Kevin / architect review before Phase 1.
**Sibling docs:** `phase-0-apex-findings.md`, `phase-0-fitbitkb-findings.md`

---

## TL;DR

Phase 0 inspection found **3 conflicts** between the URSA-OSCAR Design v1.0 working hypotheses and the actual state of APEX / fitbitkb:

1. **Tailwind hypothesis is wrong.** APEX uses hand-rolled CSS custom properties, not Tailwind. The kickoff requires escalation on this exact case. *Resolution: inherit APEX's CSS-tokens approach verbatim. Codified in ADR-001.*
2. **Backend framework mismatch.** Design Tech-Stack table locks FastAPI; APEX uses Flask. *Resolution: keep FastAPI for URSA-OSCAR (justified below), and note the divergence in the doc.*
3. **MCP SDK naming.** Design says "Python + `mcp` SDK"; APEX and fitbitkb both use **FastMCP** (the higher-level wrapper). *Resolution: documentation update — change "mcp SDK" to "FastMCP" throughout the Design doc, pin to `fastmcp==3.2.4 + mcp==1.27.0` per APEX's template.*

**Additional finding (added after second-pass reading of APEX Docs 14, 17, and `mcp-server-architecture-template.md`):** APEX has a battle-tested, explicitly-reusable MCP server template (§10 "Onboarding a new project — checklist") designed for cross-project reuse. URSA-OSCAR Phase 1's MCP server should adopt this template wholesale. Codified in ADR-002.

**0 unresolved blockers.** Nothing prevents Phase 1 from starting once Kevin agrees with the proposed resolutions to the three conflicts. The 30-night regression fixture set (Risk Register row 7) is a Phase 1 prerequisite, not a Phase 0 blocker.

---

## 1. Hypothesis-vs-Reality Matrix

| # | URSA-OSCAR Design hypothesis | Reality | Conflict? |
|---|---|---|---|
| 1 | Frontend: React + Vite + Tailwind (Decision 7) | React + Vite ✓ ; **Tailwind ✗** (custom CSS variables) | **YES** — explicit escalation per kickoff |
| 2 | Backend: FastAPI (Tech-Stack table) | APEX is **Flask** | **Soft** — both Python, conscious divergence |
| 3 | MCP SDK: "Python + `mcp` SDK" (Tech-Stack table) | APEX + fitbitkb both use **FastMCP** | **YES** — terminology / API surface mismatch |
| 4 | Deployment: Docker Compose | ✓ APEX uses Docker Compose with the exact shape Design specifies (per-service containers, `.env` interpolation, Docker Hub pull) | No |
| 5 | Storage: DuckDB embedded (Decision 2) | N/A — neither sibling uses DuckDB. APEX=Mongo, fitbitkb=SQLite | No (intentional divergence, Decision 2 reasoning still holds) |
| 6 | MCP transport: SSE (Decision 5) | ✓ Both siblings use SSE over HTTP | No |
| 7 | MCP auth: not explicitly resolved in Design | OAuth 2.1 + bearer-token fallback in both siblings | No (gap to fill in Phase 1, not a conflict) |
| 8 | Tool surface convention: snake_case, descriptive, structured returns with `interpretation` blocks | fitbitkb uses snake_case + descriptive docstrings ✓; tools return **`str`** ✗ | No conflict — URSA-OSCAR is more structured, which is appropriate for analytical tools. Design wins. |
| 9 | Config: `pydantic.BaseSettings` (Design § Tech-Stack via FastAPI conventions) | APEX uses plain `os.environ.get` | No conflict — URSA-OSCAR's stricter approach is an upgrade |
| 10 | Repo structure: `src/ursa_oscar/` with subpackages | fitbitkb is flat; APEX uses subpackages | No conflict — URSA-OSCAR is closer to APEX's pattern, which is correct |

---

## 2. The Three Conflicts In Detail

### Conflict 1 — Tailwind hypothesis falsified

**Design § Decision 7:** "Frontend framework will be selected to match APEX's stack after Phase 0 codebase inspection. Hypothesis: React + Tailwind, based on the APEX screenshot aesthetic … If different stack, surface to architect for re-evaluation before Phase 2 frontend work begins."

**Reality:** `web/package.json` has no `tailwindcss` dep. No `tailwind.config.*` exists. `web/src/index.css` defines a complete design system in CSS custom properties (~60 tokens covering colors, spacing, shadows, typography). APEX's `docs/05-frontend-spec.md` describes Tailwind tokens, but the running implementation is plain CSS.

**Why APEX went this way (confirmed by Doc 14 §12):** Doc 14's "Deferred from spec" table explicitly endorses the custom-CSS choice: *"Theme: full Tailwind / shadcn migration — Custom CSS reaches the same Jobscan aesthetic faster; rebuild not on the v1 critical path."* This is not "we haven't gotten to it yet" — it's a deliberate decision that the hand-rolled token set is the right answer for this aesthetic.

**Proposed resolution for URSA-OSCAR:**

- **Drop the Tailwind hypothesis.** Frontend stack: **React 18 + TypeScript + Vite + hand-rolled CSS custom properties** (copy APEX's `web/src/index.css` as the starting token sheet).
- **Keep** the React + Vite + TS choice — that part of Decision 7 is confirmed.
- **Inherit** the rest of APEX's frontend stack:
  - `react-router-dom` ^6 for routing (confirmed in use in APEX)
  - `lucide-react` for icons
  - `@dnd-kit/*` for drag-drop (only if URSA-OSCAR needs it — Daily View won't; Manual Logging spreadsheet view might)
  - No UI primitive library (no shadcn / Radix / MUI)
  - **No** server-state library (TanStack Query). APEX uses raw `fetch()`. URSA-OSCAR's API surface is small enough that this is fine; revisit if Phase 2 chart rendering benefits from caching.
  - **No** form library (react-hook-form). APEX uses native form elements + custom validation. URSA-OSCAR Phase 3 Manual Logging may want one — defer decision until then.
  - *(Note: my earlier draft of this synthesis listed react-query / RHF / zod as inheritances based on `package.json` declarations. Verified via grep against `web/src/` — none are actually imported. Doc 14 §8 confirms current stack is raw fetch + custom CSS.)*
- **Design tokens:** start from APEX's index.css verbatim (Inter font, accent `#2563eb`, status colors, glass-card grammar, sidebar shell). URSA-OSCAR-specific additions (event-flag chart palette for OA / CA / H / RERA / Large Leak, time-series chart axis colors) extend the token set rather than replacing it.

**Why this is the right call (not just the cheap call):**

1. Operational consistency. Kevin's homelab will have APEX and URSA-OSCAR side-by-side. Sharing the visual language means muscle memory transfers; CSS bugs in one are diagnosable from the other; future Claude sessions in either project recognize the patterns.
2. uPlot rendering (Decision 3) does not benefit from Tailwind. uPlot styles its canvas directly; the chart surrounds are layout-level CSS where custom properties + a global stylesheet are perfectly adequate.
3. The Daily View has 8-10 stacked synchronized charts — heavy custom layout work. Less utility-class noise = easier to read the layout code.

**Resolution (greenlit 2026-05-11):** See [`adr-001-frontend-stack.md`](adr-001-frontend-stack.md). URSA-OSCAR Design Decision 7 marked resolved; Tech Stack row updated.

### Conflict 2 — FastAPI vs Flask

**Design § Tech Stack:** Backend framework = FastAPI.
**Framework doc § Key Architectural Decisions row 1:** "Python (FastAPI) likely best for EDF parsing ecosystem (pyedflib, mne). Node possible but EDF libraries weaker. **Match Apex stack if reasonable.**"

**Reality:** APEX is Flask. So "match Apex stack" → Flask, but Design has already locked FastAPI.

**Why FastAPI is still the right call for URSA-OSCAR despite divergence from APEX:**

1. **Automatic OpenAPI generation.** URSA-OSCAR Design § Repo Structure explicitly calls for `docs/api/openapi.yaml`. FastAPI emits this for free. With Flask, we'd be reproducing APEX's hand-written-markdown approach, which has already drifted from the running code in APEX (per memory `feedback_verify.md` — "Antigravity's failure mode was reporting completion ahead of evidence" — hand-written specs drift; generated ones can't).
2. **Pydantic-first request handling.** URSA-OSCAR Design has rich domain models (Pydantic) in `models/domain.py`. FastAPI's `def endpoint(body: Model)` makes those load-bearing without per-handler boilerplate. APEX's per-handler `try / except ValidationError` is 4-6 lines of repeated code per endpoint.
3. **Async-native.** EDF parsing is CPU-bound but the API surface (Daily View serving 8 time-series tracks for one date, MCP tool calls, watcher webhooks) is I/O-bound. FastAPI's `async def` paths handle this without Flask's `flask[async]` adapter layer.
4. **Lower divergence cost than it looks.** Both are Python, both serve JSON, both run under uvicorn/gunicorn in Docker, both consume the same `.env` config style. The "match APEX stack" goal is really about operational shape (Docker Compose + Makefile + Docker Hub + TrueNAS pull), not source-level framework choice. We match the operational shape; we upgrade the framework.

**Proposed resolution:**

- **Keep FastAPI** for URSA-OSCAR.
- Add a one-line note to `URSA-OSCAR_Design.md` § Tech Stack acknowledging the divergence and the reasoning.
- Mirror APEX's blueprints-as-modules style in URSA-OSCAR's `api/` folder (one file per resource: `nights.py`, `events.py`, `manual_logs.py`, `imports.py`, `exports.py`) so the project layouts are visually similar even though the framework underneath differs.

**Action items if greenlit:** Add the divergence note. No code impact.

### Conflict 3 — `mcp` SDK vs FastMCP

**Design § Tech Stack:** "MCP server: Python + `mcp` SDK, SSE transport. Matches fitbitkb."
**Design § Decision 5:** "URSA-OSCAR MCP server exposes SSE transport over HTTP, matching fitbitkb's pattern."

**Reality:** Both fitbitkb and APEX use **FastMCP**, the higher-level wrapper:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("URSA-OSCAR", auth=auth_provider)

@mcp.tool()
def get_nightly_summary(date: str) -> dict:
    """..."""
    ...

app = mcp.http_app(transport="sse")
uvicorn.run(app, host="0.0.0.0", port=8081)
```

The lower-level `mcp.server.Server` API is technically possible but materially more verbose (manual tool registration, manual schema construction, manual SSE wiring). Neither sibling uses it.

**Proposed resolution:**

- Update `URSA-OSCAR_Design.md` § Tech Stack row to **"FastMCP 3.2.4 + mcp 1.27.0 (pinned), SSE transport, OAuth 2.1 + bearer fallback per APEX template (ADR-002)"**. Version pinning matters — `mcp-server-architecture-template.md` §2 warns that FastMCP's `InMemoryOAuthProvider` and `ClientRegistrationOptions` APIs drift across majors.
- Update Decision 5 to clarify that "matching fitbitkb's pattern" specifically means FastMCP + decorator-based tool registration + uvicorn SSE.
- **Diverge from fitbitkb on tool return types.** Design § MCP Tool Surface specifies structured returns with `interpretation` blocks. fitbitkb returns `str` everywhere. The APEX template formalizes the right shape: `{"ok": True, "data": {...}}` / `{"ok": False, "error": "...", "code": "NOT_FOUND" | "INVALID_INPUT" | "INVALID_OPERATION" | "ERROR"}`. URSA-OSCAR adopts this envelope per ADR-002.

**Action items (greenlit 2026-05-11):** Documentation correction in the Design doc; ADR-002 captures the wholesale template adoption.

---

## 2a. Major Inheritance — Adopt the APEX MCP Server Template Wholesale

Discovered during second-pass reading: APEX has `docs/mcp-server-architecture-template.md`, an explicitly reusable boilerplate extracted from production apex-mcp v0.17.5. §1 audience: *"A Claude Code session standing up a new MCP server for any project."* §10 "Onboarding a new project — checklist" is literally written for URSA-OSCAR Phase 1.

**What URSA-OSCAR Phase 1 inherits verbatim:**

1. **`server.py` skeleton (~150 LOC)** — auth provider, OAuth wiring, static-bearer fallback, fail-fast env-var checks, FastMCP instantiation, uvicorn entry. Per the template: *"This is ~150 lines of boilerplate that handles all auth, transport, and discovery. Drop your tools after the marker."* Domain-specific changes: rename `ApexOAuthProvider` → `UrsaOscarOAuthProvider`; replace Mongo `get_db()` import with DuckDB connection helper.
2. **Pinned dependency versions** (template §2):
   - `fastmcp==3.2.4`
   - `mcp==1.27.0`
   - `pydantic>=2.13`
   - `starlette==1.0.0`
   - `uvicorn==0.46.0`
   - `python-multipart>=0.0.27`
   - (replace `motor==3.7.1` with DuckDB's Python bindings)
3. **The response-envelope convention.** Every tool returns `{"ok": True, "data": ...}` on success or `{"ok": False, "error": str, "code": str}` on failure. Standard codes: `NOT_FOUND`, `INVALID_INPUT`, `INVALID_OPERATION`, `ERROR`. URSA-OSCAR's `_ok()` / `_err()` helpers lifted verbatim from template §6.1.
4. **Helper functions lifted verbatim from template §6:**
   - `_iso(v)` — defensive datetime → ISO string (handles datetime, str pass-through, None)
   - `_coerce_datetime_fields_in_patch(patch)` — symmetric on the write side
   - `_safe_path(*parts)` — path-traversal defense for any tool that writes to disk
   - Event-emission pattern for audit-log inserts (adapted to DuckDB INSERTs against URSA-OSCAR's `import_log` and any future event table)
5. **Required env vars (template §4 + Doc 17):**
   - `URSA_OSCAR_MCP_BEARER_TOKEN` — static bearer for curl / Claude Desktop / Claude Code
   - `URSA_OSCAR_MCP_BASE_URL` — public URL the server is reached at, used to build OAuth metadata URLs (this one was **missing from my earlier synthesis** — required, container fails fast without it)
   - `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` — pre-registered single OAuth client (DCR off)
   - `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` — paired secret
   - Container exits fast if any is missing; this is the security posture, not a paranoia
6. **DCR explicitly disabled.** `ClientRegistrationOptions(enabled=False)`. The `/register` endpoint is not mounted. Per Doc 17's threat model: this is the load-bearing security boundary for a public MCP server.
7. **Verification harness using Starlette `TestClient`** (template §8). In-process tests of:
   - Discovery endpoints reachable, `registration_endpoint` absent
   - `POST /register` returns ≠ 200/201 (DCR off)
   - `POST /messages/` without bearer returns 401 with `resource_metadata=...` in `WWW-Authenticate`
   - Full auth-code + PKCE flow with the pre-registered client yields an access token
   - Issued token unblocks `/messages/`
   - Static bearer also unblocks `/messages/`
   - **Add to Phase 1 deliverables.** ~10 min of code; catches 100% of bad-OAuth-config bugs before Docker.
8. **Per-deploy curl verification** (template §7.4). Four shell one-liners that confirm a fresh deployment is healthy. Bake into `make verify-mcp` or similar.
9. **Logging discipline.** Never log bearer tokens, client_secrets, or access tokens. Issuance logged with `client_id` + `expires_at`. Per template §9.1: *"verify with `grep -i token ./logs/*.log` post-deploy."*

**What URSA-OSCAR explicitly diverges from the template on:**

- **Database access.** Template uses Mongo / Motor / ObjectId / slug-resolution. URSA-OSCAR uses DuckDB (Decision 2). The `_resolve_thing(db, ref)` pattern doesn't apply — URSA-OSCAR's primary keys are dates (`'YYYY-MM-DD'`) and integer IDs, both unambiguous. Strip slug logic; keep the helper-function structure.
- **Multi-night tools.** Template's "single entity by ref" pattern (`get_thing(thing_id)`) maps roughly to URSA-OSCAR's `get_nightly_summary(date, end_date=None)` — but date-range tools are richer than single-entity tools. Use the template envelope but expand the data payload shape.
- **File attachments.** Template §6.9 covers base64-over-MCP for file uploads. URSA-OSCAR's ingestion is filesystem-side (SD card → watched folder → API import), not MCP-side. The `_safe_path()` helper is still useful for the export tool.

**Why this matters:** Phase 1's MCP server work was a multi-day design exercise in my original mental model. With this template, it becomes "copy template, swap data layer, add the 16 URSA-OSCAR tools." Days of work compressed into hours, with the security posture already audited against a production deployment.

**Codified in:** [`adr-002-mcp-server-template-adoption.md`](adr-002-mcp-server-template-adoption.md).

---

## 3. Patterns To Inherit (Not Currently Called Out In Design)

These are good patterns from APEX / fitbitkb that the Design doc doesn't currently mention. Worth folding in before Phase 1.

### From APEX

1. **Repo-root `Makefile` + PowerShell `build_and_push.ps1`.** Kevin's workstation is Windows; `make` + PowerShell is the established homelab convention. URSA-OSCAR Design § Repo Structure lists a Makefile generically — recommend explicitly committing to the PowerShell-builder pattern. *Action: add to `Makefile` + `infra/build_and_push.ps1` in Phase 1 scaffold.*
2. **External Docker network for cross-service homelab comms.** APEX uses `kairos-net` (external). URSA-OSCAR will likely want to be on the same network so the URSA agent's MCP client can reach the URSA-OSCAR MCP server. *Action: confirm with Kevin which network name to use (`kairos-net`? a new `ursa-net`?).*
3. **Domain-event audit log.** APEX writes `application_created` / `application_status_changed` / `application_archived` events to a dedicated collection on every state transition. URSA-OSCAR Design already has `import_log` but not a general event audit. Worth considering for: manual log create/update/delete, settings changes, imports. *Action: optional — defer to Phase 3 when manual logs ship.*
4. **Soft-delete-by-default with `?hard=true` opt-in.** APEX pattern. Useful for manual logs especially (subjective data, accidental deletes, longitudinal continuity). *Action: adopt for `manual_logs` table in Phase 3.*
5. **LAN dev-bypass port** (per memory `reference_dev_bypass_port.md`, port 5055). Useful for curl-driven validation of the URSA-OSCAR API during Phase 1 without going through MCP auth. *Action: add an unauthenticated read-only port to the URSA-OSCAR API container in dev compose.*
6. **MCP-side OAuth 2.1 + bearer-token fallback.** Both siblings have this; Design doc doesn't specify URSA-OSCAR's MCP auth. *Action: add a Design § "MCP Authentication" subsection — bearer for CLI / desktop, OAuth 2.1 for claude.ai, hardcoded callback `https://claude.ai/api/mcp/auth_callback`.*

### From fitbitkb

7. **Reactive-refresh trick.** When a tool / UI touches a recent date, fire an HTTP POST to the writer to refresh that date. URSA-OSCAR analog: Daily View loading "last night" can trigger the watcher to re-scan the SD-card mount in case data has been written since the last hourly scan. *Action: optional — add to Phase 4 once the watcher is fully wired.*
8. **`run_sql_query` SELECT-only + keyword blocklist.** URSA-OSCAR Design § Tier 3 already names this tool. The exact safety pattern (`startswith("SELECT")` + blocklist of `INSERT/UPDATE/DELETE/DROP/ALTER`) is the right thing to copy verbatim. *Action: copy fitbitkb's validator into `mcp-server/src/ursa_oscar_mcp/tools/run_sql_query.py` in Phase 1.*
9. **`inspect_schema` returning DuckDB DDL.** fitbitkb does this for SQLite; URSA-OSCAR Design Tier 3 names it; DuckDB has `PRAGMA show_tables` / `DESCRIBE` equivalents. *Action: trivial Phase 1 task, already on the list.*
10. **Documenting tool descriptions in docstrings, not external schema files.** fitbitkb's `@mcp.tool()` + docstring pattern is the cleanest. Design § MCP Tool Surface puts descriptions in YAML in the framework doc — that's spec, not source. The source-of-truth lives in the Python docstring. *Action: keep both in sync — generate `docs/mcp/tool-surface.md` from docstrings in CI rather than hand-editing it.*

---

## 4. Patterns To **Not** Inherit

### From APEX

- **MongoDB.** Decision 2 locks DuckDB.
- **Flask.** Conflict 2 resolution above.
- **`pyproject.toml` workspace package for shared models** (`packages/python-models/apex_models`). URSA-OSCAR's models are not shared across separate repos; they live with the backend in `backend/src/ursa_oscar/models/`. Simpler.
- **Manual `request.json` + `try/except ValidationError`.** FastAPI's automatic body validation replaces this.
- **Hand-written `04-rest-api-spec.md`.** FastAPI generates `/openapi.json` and Swagger UI at `/docs`. Use them.
- **`.env` committed with real secrets.** URSA-OSCAR Design § Repo Structure lists `.env.example` (committed) and `.env` (gitignored). Keep them separate from the start. The APEX pattern of committing real creds depends on "private repo + network isolation"; URSA-OSCAR can do better.
- **Antigravity-style overconfidence in documentation.** Per memory `feedback_verify.md`, APEX's `docs/05-frontend-spec.md` says Tailwind + shadcn but the code uses neither. Don't write spec docs ahead of code; generate docs from code where possible.

### From fitbitkb

- **Flat single-file layout** (`app.py` is 328 KB). URSA-OSCAR's structured `src/ursa_oscar/` with subpackages is correct.
- **`str`-returning tools.** Use structured returns (dicts / Pydantic) per Design § MCP Tool Surface.
- **In-place `ALTER TABLE` migrations.** URSA-OSCAR has `storage/migrations.py` and a `schema_version` table — use them properly from day one.
- **NULL-based per-metric staleness.** Not applicable — URSA-OSCAR's data source is the SD card; once a night is parsed, it's complete. Dedup-on-date is the right pattern.
- **Three-process supervisord in one container.** URSA-OSCAR's split-container design (api / mcp-server / frontend / watcher) is correct. Different scaling / restart characteristics warrant separate containers.
- **No formal data models.** Pydantic everywhere in URSA-OSCAR.

---

## 5. Unresolved Items (for Kevin / architect)

These are items Phase 0 surfaced but cannot resolve unilaterally. Listed by priority.

### 5.1 Frontend stack confirmation (BLOCKING for Phase 2)

Per kickoff: "If APEX is not React/Vite/Tailwind, pause and escalate before continuing."

Conflict 1 above proposes a resolution. **Need Kevin's explicit confirmation** to:
- Drop the Tailwind hypothesis
- Adopt APEX's hand-rolled CSS-tokens approach
- Inherit `react-query` + `react-hook-form` + `zod` + `lucide-react` from APEX

If Kevin disagrees and wants Tailwind in URSA-OSCAR (maybe to avoid maintaining two hand-rolled stylesheets in parallel), that's a fine call — the regression risk is small. But the Design doc explicitly called out this as an escalation trigger, so I'm escalating.

**Status:** Awaiting Kevin's decision.

### 5.2 Backend framework — keep FastAPI or switch to Flask? (BLOCKING for Phase 1)

Conflict 2 above proposes keeping FastAPI with stated reasoning. **Soft blocker** because Phase 1 backend scaffolding starts immediately and depends on this.

The conservative move is to keep FastAPI. The "match APEX exactly" move is to switch to Flask. Recommend FastAPI for reasons listed under Conflict 2; will defer to Kevin / architect if they prefer Flask.

**Status:** Awaiting confirmation.

### 5.3 MCP SDK terminology (RESOLVED)

Conflict 3 — doc correction. **Resolved 2026-05-11.** Tech Stack row updates to "FastMCP 3.2.4 + mcp 1.27.0 (pinned), SSE transport." Codified in ADR-002 (template adoption covers this).

### 5.4 MCP auth model for URSA-OSCAR (RESOLVED)

**Resolved 2026-05-11.** ADR-002 commits URSA-OSCAR to the APEX template's auth pattern verbatim. Static bearer + OAuth 2.1 + PKCE, DCR disabled, pre-registered single client, fail-fast env-var checks. Four required env vars: `URSA_OSCAR_MCP_BEARER_TOKEN`, `URSA_OSCAR_MCP_BASE_URL`, `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`, `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`. Doc 17 is the operational runbook; URSA-OSCAR Phase 4 ships its own variant of that runbook with the URSA-OSCAR hostname / env-var names. No further architectural decision needed.

### 5.5 Docker network — `kairos-net`, `ursa-net`, or new? (NON-BLOCKING for Phase 1)

APEX uses external network `kairos-net`. URSA-OSCAR needs to decide:
- Join `kairos-net` (URSA agent in any container on that network can reach the MCP server)
- Define a new external network (e.g., `ursa-net`)
- Use its own internal compose network and expose MCP via a host port

Recommend joining an existing homelab network so the URSA agent's MCP client doesn't need port-mapping gymnastics. Need Kevin's call on the name.

**Status:** Awaiting confirmation.

### 5.6 Public hostname / TLS termination for the MCP server (NON-BLOCKING for Phase 1)

If URSA-OSCAR's MCP server needs to be reachable from claude.ai (web), it needs a public hostname (e.g., `your-public-host.example.com` matching the APEX pattern from memory `reference_apex_public_endpoints.md`) and TLS termination.

For Phase 1 / Phase 2 / Phase 3, claude.ai integration can wait — desktop / CLI Claude with bearer-token auth on LAN is sufficient for dogfooding. Phase 4 should be when public access lands.

**Status:** Defer to Phase 4 prep.

### 5.7 30-night regression fixture set (Phase 1 prerequisite, NOT a Phase 0 deliverable)

Per Design § Risk Register row 7: "30 nights of regression test data not yet exported from OSCAR — High likelihood, Low impact — Kevin: export reference data before Phase 1 implementation begins."

Kevin's kickoff doc says the fixtures are "already staged at `backend\tests\regression\fixtures\`" — but I'm flagging here that I did **not** verify this during Phase 0 (the kickoff explicitly scoped Phase 0 to APEX / fitbitkb inspection, and the URSA-OSCAR backend is empty so far). When Phase 1 starts, the first concrete check should be: do the fixtures exist? Are they complete? Is the canonical AHI / event-count target list provided?

**Status:** Phase 1 day-one verification item.

---

## 6. Recommended Next Steps

1. **Kevin reviews this synthesis** + the two findings docs.
2. **Kevin resolves Conflicts 1, 2, 3** (or pushes back on any of them).
3. **Claude Code updates `URSA-OSCAR_Design.md`** in a single batch reflecting the resolutions:
   - § Tech Stack row "Frontend framework" → React 18 + TS + Vite + hand-rolled CSS (no Tailwind), inheriting APEX's tokens
   - § Tech Stack row "Backend framework" → FastAPI (note divergence from APEX's Flask, with reasoning)
   - § Tech Stack row "MCP server" → FastMCP (built on `mcp` Python SDK), SSE transport
   - § Decision 7 → resolved
   - § Decision 5 → clarified to reference FastMCP specifically
   - Add a new subsection "MCP Authentication" under § Decision 5 capturing the pattern from §5.4 above
4. **Kevin (or architect) confirms 5.5 (Docker network)** and any of 5.6 / 5.7 they want resolved now.
5. **Phase 1 begins** once the Design doc is updated and Kevin gives the green light.

---

## 7. Phase 0 Deliverable Checklist

- [x] `Docs/architect-decisions/phase-0-apex-findings.md` written
- [x] `Docs/architect-decisions/phase-0-fitbitkb-findings.md` written
- [x] `Docs/architect-decisions/phase-0-synthesis.md` written (this file)
- [x] No files modified in `C:\dev\APEX\` or `C:\dev\fitbit-web-ui-app-kb\` (read-only constraint respected)
- [ ] Summary message delivered to Kevin
- [ ] Phase 1 greenlight received from Kevin
