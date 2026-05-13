# Phase 0 — APEX Codebase Findings

**Inspector:** Claude Code (URSA-OSCAR workspace)
**Inspected:** `C:\dev\APEX\apex-system\` (read-only)
**Date:** 2026-05-11
**Purpose:** Validate URSA-OSCAR Design v1.0 architectural assumptions against APEX, the most directly comparable homelab service Kevin operates today.

> **Scope note.** This is an inspection report, not a critique of APEX. URSA-OSCAR aims to inherit APEX's deployment shape, UI language, and operational conventions for homelab consistency. Where URSA-OSCAR's locked design diverges, the divergence is called out explicitly and routed to `phase-0-synthesis.md` for resolution.

---

## 1. Frontend Stack

**Framework + build:** React 18 + TypeScript + Vite. Confirmed in `web/package.json` (`react ^18.2.0`, `vite` devDep) and `web/vite.config.ts` (dev server :5173, proxies `/api` → `http://localhost:8000`).

**Styling:** **Hand-rolled CSS, NOT Tailwind.** Single global stylesheet at `web/src/index.css` defines the entire design system via CSS custom properties. No `tailwind.config.*` file present; no `tailwindcss` in `package.json`.

**Component library:** **No shadcn/ui, no Radix, no Headless UI, no MUI.** Components are custom-built and consume the global class system (`.glass-card`, `.btn-primary`, `.data-table`, `.status-pill`, etc.). Despite `docs/05-frontend-spec.md` referencing Tailwind + shadcn, the implementation does not use either.

**Routing + state:**
- `react-router-dom` ^6 for routing — confirmed in use.
- `@tanstack/react-query` ^5, `react-hook-form` ^7, `zod` ^3 are **declared in `package.json` but not imported anywhere in `web/src/`** (verified via grep: zero source-file matches for any of the three). They are aspirational deps from an earlier design intent that was never wired up.
- Doc 14 (`14-current-architecture-and-filelist.md`, the authoritative current-state doc) §8 confirms: *"No Tailwind / shadcn / TanStack Query (those were called for in `docs/05-frontend-spec.md` but the current stack uses custom CSS + raw fetch)."*
- Reality: **raw `fetch()` for API calls**, no form library, no client-side server-state cache. List-page filtering / sorting is custom code (`lib/useSortable.tsx`, per-page `rowMatchesFilters` helpers).

**Other:** `lucide-react` for icons. `@dnd-kit/*` for kanban + sortable lists.

**Page-level structure:** `web/src/pages/` contains route components (PipelineKanban, ApplicationDetail, Targets, Recruiters, Sessions, IdentityContext, etc.). Shared components live in `web/src/components/shared/`. Layout root is `web/src/Layout.tsx` (sidebar + main content shell).

---

## 2. Backend Stack

**Language:** Python 3.11+.

**Framework:** **Flask** (with `flask[async]` for async route support), served by **Gunicorn** in production. Entry at `api/app/__init__.py` exposes the `app` Flask instance. Dev runs via `flask run --reload`; prod via Gunicorn (configured in `api/Dockerfile`).

> This is the most significant divergence from the URSA-OSCAR Design tech-stack table, which locks FastAPI. See `phase-0-synthesis.md` § Conflict 2.

**Database:** **MongoDB** (single shared `MungoDBAI` cluster per memory). Access via `motor` (async driver). No ORM. Connection helper at `api/app/db/helpers.py:get_db()` creates an `AsyncIOMotorClient` per call (no pooling layer above Motor's internal pool). Database name hardcoded as `apex`.

**Validation:** **Pydantic v2** (`pydantic>=2.0`). Shared domain models live in a separate workspace package: `packages/python-models/apex_models/models.py`. Routes deserialize `request.json`, instantiate Pydantic models, return HTTP 400 with `{'error', 'details'}` on `ValidationError`. No FastAPI-style automatic body validation; it's explicit per-route.

**Blueprints (route modules):** `/api/applications`, `/api/companies`, `/api/contacts`, `/api/recruiters`, `/api/sessions`, `/api/identity_context`, `/api/interviews`, `/api/events`, `/api/notes`, `/api/pipeline`, `/api/uploads`.

---

## 3. Container Orchestration

**Compose files (all under `infra/`):**
- `docker-compose.yml` — production reference (pulls images from Docker Hub)
- `docker-compose.dev.yml` — local dev (build contexts + hot reload)
- `docker-compose.production.yml` — prod-specific overrides

**Production services (from `docker-compose.yml`):**

| Service | Image | Ports | Volumes | Network |
|---|---|---|---|---|
| `apex-api` | `${DOCKERHUB_USER}/apex-api:latest` | internal only | `${NAS_FILES_PATH}:/mnt/apex-files` | `kairos-net` |
| `apex-mcp` | `${DOCKERHUB_USER}/apex-mcp:latest` | `5051:8000` | `${NAS_FILES_PATH}:/mnt/apex-files` | `kairos-net` |
| `apex-web` | `${DOCKERHUB_USER}/apex-web:latest` | `5050:80` | — | `kairos-net` |
| `apex-worker` | `${DOCKERHUB_USER}/apex-worker:latest` | — | files + backups | `kairos-net` |

Network `kairos-net` is **external** (defined elsewhere on the TrueNAS host). Mongo is **not** in the production compose — it lives in a separate cluster at `192.168.x.x:27017` (per memory).

**Dev compose adds:** `apex-mongo` (mongo:7.0) at `27017:27017`, all service builds from local Dockerfiles, web hot-reload at `5173:5173`, healthcheck-gated startup.

**Env handling:** Inline `environment:` entries with `${VAR}` interpolation from the repo-root `.env`. Per Doc 14 §3 (authoritative env list): `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD`, `MONGO_HOST`, optional `MONGO_URI`, `NAS_FILES_PATH`, `MCP_BEARER_TOKEN`, **`MCP_BASE_URL`**, `MCP_OAUTH_CLIENT_ID`, `MCP_OAUTH_CLIENT_SECRET`, `MCP_EXTERNAL_HOST` (logging only), `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`. Container fails fast (exits) if any of the **mandatory** ones is missing: `DASHBOARD_USERNAME` / `_PASSWORD` on web; `MCP_BEARER_TOKEN` / `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` on mcp (v0.17.5+). Per memory, real values live in Dockge's per-stack env editor on TrueNAS — **NOT** committed to source.

**Public port map (from Doc 14 §2; URSA-OSCAR should pick non-conflicting host ports):**

| Port | Service | Auth |
|---|---|---|
| `5050` host → `apex-web:80` | UI behind nginx HTTP Basic | Yes |
| `5051` host → `apex-mcp:8000` | MCP SSE | Bearer / OAuth |
| `5055` host → `apex-web:8080` | LAN dev-bypass, unauth, **not Cloudflare-routed** | None — LAN trust only |

**Volume strategy:** NAS-mounted host paths bind-mounted into each service. No named Docker volumes for app data in production (Mongo data is on the external cluster).

---

## 4. API Patterns

**URL shape:** Plural-noun resources (`/api/applications`, `/api/contacts`), ID-in-path for detail/update/delete, query params for filter/sort/pagination. Sub-resource actions are POSTs to nested paths (e.g., `POST /api/applications/<ref>/status`).

**Methods:** GET (list/detail), POST (create + actions), PATCH (update; PUT accepted for legacy compat), DELETE (soft by default, `?hard=true` for cascade delete).

**Request validation:** Manual Pydantic instantiation inside handlers. Pattern (from `api/app/api/contacts.py`):

```python
try:
    model = Contact(**data)
except ValidationError as e:
    return jsonify({'error': 'Validation failed', 'details': e.errors()}), 400
```

**Response shape:**
- Success: bare object or array (no `{data, error}` envelope), HTTP 200/201
- Error: `{'error': 'description'}` (sometimes `{'ok': False, 'error': ..., 'code': ...}` from the global handler), HTTP 4xx/5xx
- **Inconsistency:** `docs/04-rest-api-spec.md` documents an envelope `{ok, data}` shape, but the running code returns bare bodies. The audit memory (Docs/12) likely captures this.

**Error handling:** Global Flask exception handler at `api/app/__init__.py` lines ~44-53 catches unhandled `Exception` and returns `{'ok': False, 'error', 'code'}`, HTTP 500. Per-handler try/except for validation.

**OpenAPI:** **Not auto-generated.** No Flask-OpenAPI lib, no Swagger UI. The spec is hand-written markdown at `docs/04-rest-api-spec.md`.

**Auth:** **None in v1** for the REST API (per `docs/04-rest-api-spec.md`). Network isolation (kairos-net + homelab firewall) is the trust boundary. There is also a LAN dev-bypass on port 5055 (per memory) for unauthenticated curl validation.

**Notable patterns:**
- Domain events emitted into a `/api/events` audit log on every state transition (`application_created`, `application_status_changed`, etc.)
- Soft-delete by default; hard-delete is opt-in via query param
- External slug (`_id_external`) accepted alongside ObjectId hex in `<ref>` path params

---

## 5. Configuration Management

**Where settings live:**
- `.env` at repo root for Docker Compose interpolation
- `pyproject.toml` per service for dependencies
- Hardcoded defaults in Python (`get_db()` defaults `MONGO_HOST=apex-mongo`, db name `apex`)

**Secrets:**
- Real credentials in committed `.env` (relies on private GitHub repo + network isolation)
- No Docker secrets, no mounted secret files, no Vault integration
- Pre-push scrubbing required (per memory) for the Mongo password

**Pydantic BaseSettings:** **Not used.** Config is plain `os.environ.get(...)` calls scattered in module init. No central settings object.

**Feature flags / runtime knobs:** `APEX_SKIP_INDEX_INIT` (skip Mongo index creation on startup), `NAS_FILES_PATH` (overridable mount root).

---

## 6. Deployment Workflow

**Makefile targets (`apex-system/Makefile`):**
- `make dev` → `docker-compose -f infra/docker-compose.dev.yml up --build`
- `make build` → `powershell -File infra/build_and_push.ps1 -Version 0.1.0`
- `make push` — handled inside `build_and_push.ps1`
- `make deploy` — **TODO stub**, currently manual
- `make test` — **TODO stub**
- `make migrate` — **TODO stub**

**Build + push (`infra/build_and_push.ps1`, PowerShell):**
1. For each of `apex-api`, `apex-mcp`, `apex-web`, `apex-worker`:
   - `docker build -t ${DOCKERHUB_USER}/${service}:${Version} -t ${service}:latest -f ./${context}/Dockerfile .`
   - `docker push` both tags
2. Fails fast on non-zero `$LASTEXITCODE`.

**Ship-a-change loop (reconstructed from Makefile + scripts):**
1. Edit code locally; verify via `make dev`.
2. `make build` → tags + pushes 4 images to Docker Hub (`brain40/apex-*`).
3. SSH to TrueNAS (Dockge UI at `192.168.x.x:5001`, per memory) → pull + restart the stack manually.

**CI:** None. No `.github/workflows/`, no GitLab CI, no Gitea Actions. All build/push is local-driven.

**Implication for URSA-OSCAR:** Inherit the Makefile-front + PowerShell-builder pattern verbatim. Kevin's machine is Windows; build_and_push.ps1 is the established convention.

---

## 7. MCP Server (Brief)

`apex-system/mcp/server.py` (~2600 LOC). Stack:
- Python 3.11+
- **FastMCP** (not the raw `mcp` SDK) — `fastmcp` in `mcp/pyproject.toml`
- Transport: **SSE over HTTP**, port 8000 internal
- Auth: **OAuth 2.1 with PKCE** via a custom `ApexOAuthProvider` (extends FastMCP's `InMemoryOAuthProvider`), plus a static bearer-token fallback for curl/Desktop/Code clients
- `claude.ai` callback hardcoded: `https://claude.ai/api/mcp/auth_callback`
- Same Motor-based DB access as the REST API (read+write)

> Deeper MCP comparison lives in `phase-0-fitbitkb-findings.md` and the synthesis doc. The headline is: **APEX uses FastMCP, not the raw `mcp` SDK referenced in URSA-OSCAR Design § Tech Stack.**

---

## 8. Visual Design System

**Tokens (excerpt from `web/src/index.css`):**

```css
/* Backgrounds */
--bg-primary:   #f7f8fa;   /* page */
--bg-secondary: #ffffff;   /* sidebar / cards */
--bg-elevated:  #ffffff;   /* modals */
--bg-hover:     #f0f2f6;
--bg-subtle:    #f5f6f9;   /* table headers */

/* Text */
--text-primary:   #1a1d23;
--text-secondary: #6b7280;
--text-muted:     #9aa0a6;

/* Accent */
--accent-primary: #2563eb;
--accent-hover:   #1d4ed8;
--accent-soft:    #eef2ff;

/* Status */
--status-good:    #16a34a;
--status-warn:    #d97706;
--status-bad:     #dc2626;
--status-neutral: #6b7280;

/* Shadow */
--shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
--shadow-md: 0 2px 8px rgba(15, 23, 42, 0.08);
--shadow-lg: 0 8px 24px rgba(15, 23, 42, 0.12);
```

**Typography:** Inter (Google Fonts, 400/500/600/700). Fallback `-apple-system, BlinkMacSystemFont, sans-serif`. Page title 1.75rem / 600. Body 0.9375rem.

**Layout grammar:**
- App shell: `.app-container` (flex row) → 240px fixed `.sidebar` + flex-1 `.main-content` (padding `2rem 2.5rem`, overflow-y auto)
- Sidebar nav: `.nav-link` items, 0.625rem padding, 6px border-radius, hover/active state via bg + accent color
- Logo: `.logo`, 1.25rem / 700, letter-spacing 1px
- Card grammar: `.glass-card` (despite the name, it's a flat white card — bg-secondary, 1px border, 10px radius, 1.5rem padding, shadow-sm)

**Component patterns:**
- Buttons: `.btn-primary` (accent bg + white text), `.btn-secondary` (white bg + border), `.btn-danger` (status-bad text). 0.625rem padding, 6px radius.
- Tables: `.data-table` full-width, th uses `--bg-subtle`, row hover `--bg-hover`. No striping.
- Badges: `.badge` and `.status-pill` (pill-shaped 999px radius, semantic color variants: good / warn / bad / neutral; tiered variants for company tiers: primary/secondary/tertiary/watch/rejected).
- Forms: native inputs styled globally, focus → accent border + soft accent box-shadow ring.
- Modals: `.modal-overlay` fixed full-screen, `rgba(15, 23, 42, 0.4)` scrim; `.modal-card` elevated white card max 560px wide, 12px radius, shadow-lg.

**Reference screenshots:** Kevin uploaded 4 APEX screenshots in the architect chat. Per memory `feedback_apex_theme.md`, the running UI matches the light Jobscan-style spec in `docs/05-frontend-spec.md` — not the dark glass theme that an earlier build attempt had introduced. The CSS above confirms light + flat + Jobscan-adjacent.

---

## 9. Existing `docs/` Folder (priority reference)

Concise inventory:

| File | Purpose |
|---|---|
| `00-executive-summary.md` | What APEX is / why it exists |
| `01-architecture.md` | System architecture, data flow |
| `02-database-schema.md` | Mongo collection shapes, indexes |
| `03-mcp-tool-contract.md` | MCP tool surface contracts |
| `04-rest-api-spec.md` | REST endpoint contracts |
| `05-frontend-spec.md` | Frontend layout + (documented) Tailwind tokens — diverges from actual implementation |
| `06-nas-file-layout.md` | NAS mount structure + path safety |
| `07-migration-plan.md` | Sheet → Mongo migration plan |
| `08-build-sequence.md` | Build order, image dependency graph |
| `09-antigravity-build-instructions.md` | Legacy build doc (Antigravity-authored) |
| `12-audit-report.md` | Kevin's own audit of the Antigravity build |
| `13-application-detail-spec.md` | Refined Application Detail spec (authoritative per memory) |
| `14-current-architecture-and-filelist.md` | Current state + file tree |
| `15-build-handover.md` | Handover guide |
| `16-claude-mcp-usage-guide.md` | How Claude consumes APEX MCP |
| `17-oauth-setup.md` | OAuth 2.1 config for claude.ai |
| `18-...` / `19-...` | Sprint notes |
| `mcp-server-architecture-template.md` | **Canonical reusable MCP server template** — explicit playbook for new Claude Code projects standing up an MCP server. §10 "Onboarding a new project — checklist" applies directly to URSA-OSCAR Phase 1. |

Trust ranking (refined after reading Docs 14 + 17):
- **Doc 14** (`14-current-architecture-and-filelist.md`) is the **authoritative current-state document**, dated 2026-05-04, explicitly supersedes 10/11 and reflects what is actually deployed. URSA-OSCAR should read Doc 14 before any spec doc.
- **Doc 17** (`17-oauth-setup.md`) is the operational runbook for claude.ai connector setup. Directly applicable to URSA-OSCAR's Phase 4 work.
- **`mcp-server-architecture-template.md`** is a reusable boilerplate explicitly designed for cross-project reuse (see §10 "Onboarding a new project — checklist").
- Docs 00-09 + 13 = spec (authoritative for intent); 10/11 = Antigravity-era, factual errors; 12 = audit; 14 + 17 = current reality. When spec and reality diverge (frontend stack is the cleanest example), **reality wins** for "what URSA-OSCAR inherits."

---

## Notes for URSA-OSCAR

Where URSA-OSCAR can directly inherit:

- **Repo-root `Makefile` + PowerShell `build_and_push.ps1`** — operational consistency on Kevin's Windows workstation.
- **`docker-compose.yml` shape** — pull-from-Docker-Hub for prod, build-from-source for dev. External shared network for homelab cross-service comms.
- **`.env`-driven config** with `${VAR}` interpolation. URSA-OSCAR Design's plan to use `pydantic.BaseSettings` is **stricter** than APEX's `os.environ.get` style; this is a conscious upgrade, not a conflict — flag for synthesis.
- **CSS-custom-property design system** — adopt APEX's `index.css` tokens verbatim (Inter, light theme, accent #2563eb, semantic status colors, glass-card grammar) so the two systems look unified in Kevin's homelab.
- **Soft-delete-by-default + event-log pattern** — URSA-OSCAR's `import_log` already maps to this; manual logs should probably also use soft-delete to preserve longitudinal continuity.
- **FastMCP + SSE + OAuth 2.1 + bearer-fallback** for the MCP server.

Where URSA-OSCAR should **not** inherit:

- **MongoDB.** URSA-OSCAR Decision 2 already locks DuckDB.
- **Flask.** URSA-OSCAR Design locks FastAPI — see synthesis Conflict 2.
- **Manual `request.json` + Pydantic-instantiate pattern.** FastAPI's automatic body validation is materially better; URSA-OSCAR should use it.
- **Hand-written OpenAPI markdown.** FastAPI auto-generates `/openapi.json` and Swagger UI; URSA-OSCAR gets this for free.
- **`make test` / `make deploy` / `make migrate` as TODO stubs.** URSA-OSCAR's Design § Phase 1 acceptance criteria require these to be real from day one.

---

## Unresolved (Phase 0)

None blocking. The Tailwind hypothesis falsification is the only escalation item, and it's a frontend-stack choice that can be locked in Phase 0 synthesis without further code reading.
