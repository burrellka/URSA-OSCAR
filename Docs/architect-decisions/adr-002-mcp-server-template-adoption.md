# ADR-002 — Adopt APEX MCP Server Architecture Template Wholesale

**Status:** Accepted
**Date:** 2026-05-11
**Extends:** URSA-OSCAR Design v1.0 § Architect Decision 5 (MCP transport — SSE over HTTP)
**Inputs:** APEX `docs/mcp-server-architecture-template.md`, `docs/14-current-architecture-and-filelist.md` §4, `docs/17-oauth-setup.md`

---

## Context

URSA-OSCAR Design Decision 5 committed to "SSE transport over HTTP, matching fitbitkb's pattern." Phase 0 inspection of APEX surfaced a stronger option: APEX has `docs/mcp-server-architecture-template.md`, a battle-tested reusable boilerplate extracted from production `apex-mcp v0.17.5`. Its §1 audience: *"A Claude Code session standing up a new MCP server for any project."* Its §10 ("Onboarding a new project — checklist") is literally written for URSA-OSCAR Phase 1.

Without this template, URSA-OSCAR Phase 1 would re-derive: FastMCP version selection, OAuth 2.1 provider integration, DCR-disabled posture, static-bearer fallback, fail-fast env-var validation, response envelope conventions, defensive datetime serialization, path-traversal helpers, in-process auth-boundary tests, per-deploy curl checks, Cloudflare Tunnel deployment shape. Days of work, with non-trivial security risk in any rederiving.

## Decision

URSA-OSCAR Phase 1 adopts `apex-system/docs/mcp-server-architecture-template.md` wholesale for the MCP server (`mcp-server/` container per Design § Repository Structure). Specifically:

### What URSA-OSCAR adopts verbatim

1. **`server.py` skeleton** from template §5 (~150 LOC). Rename `ApexOAuthProvider` → `UrsaOscarOAuthProvider`. Replace `motor.AsyncIOMotorClient` data layer with DuckDB connection helper. Keep everything else.

2. **Pinned dependency versions** (template §2):
   - `fastmcp==3.2.4`
   - `mcp==1.27.0`
   - `pydantic>=2.13`
   - `starlette==1.0.0`
   - `uvicorn==0.46.0`
   - `python-multipart>=0.0.27`
   - `duckdb` (replaces `motor==3.7.1` from the template)
   - Python 3.11+

3. **Response envelope** (template §6.1):
   ```python
   {"ok": True, "data": {...}}                               # success
   {"ok": False, "error": "...", "code": "ERROR_CODE"}       # failure
   ```
   Standard codes: `NOT_FOUND`, `INVALID_INPUT`, `INVALID_OPERATION`, `ERROR`. Helpers `_ok()` / `_err()` lifted verbatim.

4. **Authentication model** (template §4 + Doc 17):
   - Static bearer (`URSA_OSCAR_MCP_BEARER_TOKEN`) for curl / Claude Desktop / Claude Code
   - OAuth 2.1 + PKCE for claude.ai web (pre-registered single client)
   - `ClientRegistrationOptions(enabled=False)` — DCR off, `/register` not mounted
   - `hmac.compare_digest()` for static-bearer comparison (constant-time)
   - Container exits fast at startup if any required env var is missing

5. **Required env vars** (container exits with stderr if any is missing):
   - `URSA_OSCAR_MCP_BEARER_TOKEN`
   - `URSA_OSCAR_MCP_BASE_URL`
   - `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`
   - `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`

6. **Helper functions** (template §6) lifted verbatim:
   - `_iso(v)` — defensive datetime → ISO string (handles datetime, str pass-through, None, unknown types return None instead of raising)
   - `_coerce_datetime_fields_in_patch(patch)` — symmetric on write side
   - `_safe_path(*parts)` — path-traversal defense for export tool

7. **Audit event emission** (template §6.7). URSA-OSCAR's analog is INSERTs against `import_log` and any future event table in DuckDB. Pattern same; backend differs.

8. **Verification harness** (template §8). In-process Starlette `TestClient` covers:
   - `/.well-known/oauth-authorization-server` reachable; no `registration_endpoint`
   - `POST /register` returns ≠ 200/201 (DCR off)
   - `POST /messages/` without bearer returns 401 with `resource_metadata=...` in `WWW-Authenticate`
   - Full PKCE auth-code dance with pre-registered client yields access token
   - Issued token unblocks `/messages/`
   - Static bearer also unblocks `/messages/`

   **Added to Phase 1 deliverables** as `mcp-server/tests/verification/test_auth_boundary.py`. Runs in CI / via `make verify-mcp`.

9. **Per-deploy curl verification** (template §7.4) — four shell one-liners packaged as a `make verify-mcp-live HOST=...` target.

10. **Logging discipline** (template §9.1): never log bearer tokens, client_secrets, or access tokens. Issuance logged with `client_id` + `expires_at` only. Post-deploy verification via `grep -i token` on stderr.

### What URSA-OSCAR diverges from the template on

- **Database layer.** Template is Mongo / Motor / ObjectId. URSA-OSCAR is DuckDB. PKs are dates (`'YYYY-MM-DD'`) and integer IDs, not ObjectId hex. The `_resolve_thing(db, ref)` slug-or-ObjectId helper is not adopted — URSA-OSCAR's identifiers are unambiguous.

- **Tool return shapes.** Template's "single entity by ref" pattern maps to URSA-OSCAR's `get_nightly_summary(date)`, but URSA-OSCAR's analytical tools return richer payloads with `interpretation` blocks (per Design § MCP Tool Surface). Envelope is the same; data payload is domain-specific.

- **File attachments.** Template §6.9 (base64-over-MCP for file uploads) doesn't apply — URSA-OSCAR ingests via filesystem watcher, not MCP. `_safe_path()` is still useful for the `export_data` tool.

### What URSA-OSCAR adds beyond the template

- **DuckDB connection management.** Single-writer pattern: only the API container writes; the MCP container is read-only. Each MCP request opens a fresh read-only DuckDB connection (DuckDB supports concurrent readers).
- **Domain-specific Pydantic models** for tool return types: `NightlySummary`, `AHIBreakdown`, `PressureProfile`, etc. Returned inside the `data` field of the response envelope.

## Consequences

**Positive:**
- Phase 1 MCP server work goes from "design + implement + audit" to "lift + adapt." Days of work → hours.
- Security posture is already audited against a production deployment. DCR-off is correct; fail-fast env-var checks are correct; constant-time bearer comparison is correct.
- Operational consistency across Kevin's homelab: APEX MCP and URSA-OSCAR MCP have identical auth setup procedures.
- `docs/17-oauth-setup.md` becomes URSA-OSCAR's claude.ai connector runbook with minimal adaptation (hostname + env-var names).

**Negative:**
- URSA-OSCAR is now coupled to FastMCP's specific version pinning. When APEX upgrades, URSA-OSCAR should follow within a reasonable window to keep operational patterns aligned.
- DCR-off means manual OAuth client registration; if URSA-OSCAR ever needs multiple OAuth clients (Kevin's laptop + Kevin's phone + a second user), the template's single-pre-registered-client pattern needs replacement. Acceptable at v1 scope.

**Revisit triggers:**
- APEX MCP template version bump beyond `fastmcp 3.x` — pin upgrade needed.
- URSA-OSCAR goes multi-tenant (will not happen at single-user homelab scope, but flagged here for the multi-tenant note in §9 of the template).

## Implementation note for Phase 1

Phase 1 MCP server work order:

1. Run template §10 onboarding checklist top-to-bottom.
2. Copy template §5 `server.py` skeleton into `mcp-server/src/ursa_oscar_mcp/server.py`. Rename auth provider class.
3. Write `mcp-server/src/ursa_oscar_mcp/client.py` — DuckDB connection helper (read-only mode, opens per-request, closes deterministically).
4. Implement Tier 1 tools per Design § MCP Tool Surface, one per file under `mcp-server/src/ursa_oscar_mcp/tools/`. Each tool returns the template envelope wrapping a Pydantic-modeled data payload.
5. Write the verification harness per template §8, against the URSA-OSCAR-specific env-var names.
6. Pin versions in `mcp-server/pyproject.toml`.
7. Build the container; deploy to homelab; run template §7.4 curl checks.

Phase 4 (claude.ai connector enablement) adapts `docs/17-oauth-setup.md` into `Docs/ursa-oscar-oauth-setup.md` with URSA-OSCAR hostname + env-var names.

## References

- APEX `docs/mcp-server-architecture-template.md` (template source)
- APEX `docs/14-current-architecture-and-filelist.md` §4 (auth surface as deployed)
- APEX `docs/17-oauth-setup.md` (claude.ai connector runbook)
- URSA-OSCAR `Docs/URSA-OSCAR_Design.md` Decision 5
- URSA-OSCAR `Docs/architect-decisions/phase-0-synthesis.md` § 2a, Conflict 3
