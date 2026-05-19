# 35 — Migration guide: URSA-OSCAR 0.12.x → 0.13.x

**TL;DR:** Phase 6.4 adds password-protected authentication to URSA-OSCAR. Phase 6.4.1 makes service-to-service tokens fully auto-managed — operator only picks a password, the rest happens server-side.

**Recommended image targets (Phase 6.4.1, zero copy-paste UX):**

| Container | 0.12.x → | Target |
|---|---|---|
| `ursa-oscar-api` | 0.12.0 | **0.13.1** |
| `ursa-oscar-web` | 0.12.0 | **0.13.0** |
| `ursa-oscar-mcp` | 0.10.0 | **0.11.1** |
| `ursa-oscar-watcher` | 0.9.0 | **0.10.1** |

The 0.13.0 / 0.11.0 / 0.10.0 set (Phase 6.4) is still published and supported, but requires manual token paste-and-restart cycles for MCP and watcher. The 0.13.1 / 0.11.1 / 0.10.1 set (Phase 6.4.1) auto-manages those service tokens and is the recommended path.

All four images must be updated together. A partial upgrade where the API jumps but MCP / watcher stay on the old image will leave you with a stack that boots but cannot import data or serve MCP tools — every internal call returns 401.

---

## What changes in 0.13.x

1. **Single-user authentication.** First visit to the web UI lands on `/setup`. You set an operator password (≥12 chars, no recovery). Subsequent visits land on `/login` until the 24h session cookie is set.
2. **All API endpoints now require auth.** Cookie-based for browsers, `Authorization: Bearer <jwt>` for services.
3. **MCP server accepts three bearer kinds**: OAuth (claude.ai), static bearer (curl/Desktop/Code), and **operator JWT (new)** — issued via Settings → Account.
4. **Service tokens are auto-managed (Phase 6.4.1).** API container mints `/data/service_tokens/mcp.jwt` and `/data/service_tokens/watcher.jwt` on startup; the MCP and watcher containers pick them up automatically via the shared `/data` mount. Operator never sees these tokens. Re-minted when expired or expiring within 7 days.
5. **JWT signing secret** lives at `/data/jwt_secret` (auto-generated on first boot of API 0.13.x, mode 0600). MCP container reads the same file from its read-only `/data` mount. Or set `URSA_OSCAR_JWT_SECRET` explicitly in both containers.
6. **`URSA_OSCAR_MCP_API_TOKEN` and `URSA_OSCAR_WATCHER_TOKEN`** env vars are optional overrides. Set them only if you want manual control over service credentials; leaving them unset engages the auto-managed file flow.
7. **New CLI break**: scripts that called the API anonymously now need to send a bearer header. The dev-bypass on the LAN port (5055) is unchanged — that endpoint is still anonymous, deliberately.

What does NOT change:

- DuckDB schema (no migrations run for auth — auth state lives in `/data/auth.json`).
- The Phase 5 Fernet master key (`URSA_OSCAR_SECRET_KEY`).
- OAuth setup for claude.ai (unchanged; JWT is a third path, not a replacement).
- Existing static bearer (`URSA_OSCAR_MCP_BEARER_TOKEN`).

---

## Pre-flight checklist

- Confirm `URSA_OSCAR_SECRET_KEY` is stable in your env (no surprise rotation in this upgrade).
- Confirm you have a backup of `/data` (DuckDB + secrets.enc). The upgrade doesn't touch them but rolling back is much easier with a backup.
- Decide whether you want auto-managed JWT signing secret (default, recommended) or manually set `URSA_OSCAR_JWT_SECRET`. Recommendation: leave it auto, change later if you want.
- Pick a strong operator password and store it in your password manager. **There is no recovery.**

---

## Step-by-step upgrade

### Step 1 — bump all four image tags

In your compose env block (Dockge or `/opt/ursa-oscar/docker-compose.yml`):

```yaml
ursa-oscar-api:
  image: brain40/ursa-oscar-api:0.13.1

ursa-oscar-mcp:
  image: brain40/ursa-oscar-mcp:0.11.1

ursa-oscar-web:
  image: brain40/ursa-oscar-web:0.13.0

ursa-oscar-watcher:
  image: brain40/ursa-oscar-watcher:0.10.1
```

Also update the version chips (purely cosmetic, surfaced on Settings → Configuration):

```yaml
ursa-oscar-api:
  environment:
    URSA_OSCAR_IMAGE_VERSION: 0.13.1
    URSA_OSCAR_MCP_IMAGE_VERSION: 0.11.1
    URSA_OSCAR_WEB_IMAGE_VERSION: 0.13.0
    URSA_OSCAR_WATCHER_IMAGE_VERSION: 0.10.1
```

### Step 2 — add the MCP `/data` volume mount

The MCP container needs read access to `/data/jwt_secret` (the API auto-shares its signing key via this file). Add the mount to the MCP service block:

```yaml
ursa-oscar-mcp:
  # ...existing config...
  volumes:
    - /opt/ursa-oscar/data:/data:ro
```

If you'd rather set the secret explicitly and skip this mount, set `URSA_OSCAR_JWT_SECRET` on both `ursa-oscar-api` and `ursa-oscar-mcp` — must be identical:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Step 3 — pull and recreate everything

```bash
docker compose pull
docker compose up -d --force-recreate
```

All four containers come up. The API container, on first boot:

1. Auto-generates `/data/jwt_secret` (mode 0600) if not already present.
2. Auto-mints `/data/service_tokens/mcp.jwt` and `/data/service_tokens/watcher.jwt` (90-day operator JWTs, mode 0600).
3. The MCP and watcher containers read their respective files at request time via the shared `/data` mount.

Verify the API set everything up:

```bash
docker logs ursa-oscar-api 2>&1 | grep -E "(service token|jwt_secret)"
```

Expected:

```
Generated initial JWT signing secret at /data/jwt_secret (mode 0600). ...
service token mcp: minted fresh (reason=missing); valid until 2026-08-XX...
service token watcher: minted fresh (reason=missing); valid until 2026-08-XX...
```

Confirm both service-token files exist:

```bash
ls -la /opt/ursa-oscar/data/service_tokens/
# -rw------- ... mcp.jwt
# -rw------- ... watcher.jwt
```

### Step 4 — first-run bootstrap in the web UI

1. Hit `http://<host>:5063` in a browser.
2. You should land on `/setup`. Pick an operator password (≥12 chars). Click **Create operator account**.
3. You land on the Overview. Your previous CPAP data is all still there — auth doesn't touch the data model.
4. The sidebar footer now shows `operator | sign out`.

### Step 5 — confirm MCP and watcher picked up their tokens

```bash
docker logs ursa-oscar-watcher 2>&1 | tail -3
# Expected: api_client: operator JWT configured; auth header active

docker logs ursa-oscar-mcp 2>&1 | grep "JWT signing secret"
# Expected: JWT signing secret loaded from /data/jwt_secret (shared with API)
```

If the watcher logs `URSA_OSCAR_WATCHER_TOKEN is unset` instead of the success line, either the `/data` mount isn't there or `/data/service_tokens/watcher.jwt` wasn't minted — see Pitfalls below.

### Step 6 — end-to-end smoke

- **Web UI**: hard-refresh (Ctrl+Shift+R). You should remain signed in (24h cookie). Pages render normally.
- **Watcher**: drop a small change in the CPAP bind-mount. After quiescence (default 30s), check the Imports page or `docker logs ursa-oscar-watcher`. The import should succeed with no 401 errors.
- **MCP**: if you have the claude.ai connector wired up, open a new chat with URSA-OSCAR enabled and ask for `list_available_nights`. Should return data, no auth errors.
- **CLI smoke** (optional):
  ```bash
  # /healthz is unauthenticated and should still respond.
  curl http://<host>:5063/healthz
  # /api/v1/nights now requires auth. With no header → 401.
  curl http://<host>:5063/api/v1/nights
  # With a token → 200 + JSON.
  curl -H "Authorization: Bearer <a JWT from step 5>" \
       http://<host>:5063/api/v1/nights
  ```

---

## Rollback procedure

If something's wrong and you need to roll back to 0.12.x:

1. Revert all four image tags in compose to their 0.12.x values.
2. Revert env: remove `URSA_OSCAR_JWT_SECRET`, `URSA_OSCAR_MCP_API_TOKEN`, `URSA_OSCAR_WATCHER_TOKEN`, and revert the chip versions.
3. Remove the MCP `/data:ro` volume mount (it wasn't there pre-Phase 6.4).
4. `docker compose up -d --force-recreate`.

The 0.12.x stack reads neither auth state nor JWT secret — they're inert files on the data volume. You can leave `/data/auth.json` and `/data/jwt_secret` in place; they'll be re-used if you upgrade again later (so the operator password persists across rollback/re-upgrade).

If you want a clean reset on re-upgrade:

```bash
docker compose stop
rm /opt/ursa-oscar/data/auth.json /opt/ursa-oscar/data/jwt_secret
docker compose up -d
# Then re-run Steps 4-7 above with a fresh password + new tokens.
```

---

## Common upgrade pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Web UI loops to `/login` after sign-in | Cookie not being set — check that `URSA_OSCAR_DEV_MODE` is **not** set in production (forces `samesite=lax`, `secure=False`) | Drop `URSA_OSCAR_DEV_MODE` from compose env if present |
| Watcher logs `401 Unauthorized` | `URSA_OSCAR_WATCHER_TOKEN` is unset or stale | Regenerate via Settings → Account, paste, recreate |
| MCP tools fail with 401 | `URSA_OSCAR_MCP_API_TOKEN` is unset or stale | Regenerate via Settings → Account, paste, recreate |
| MCP container won't start, logs "JWT signing secret not configured" | The `/data:ro` mount is missing AND `URSA_OSCAR_JWT_SECRET` not set | Add the mount or the env var (see Step 2) |
| Operator JWT rejected with "Signature verification failed" | API and MCP have different `URSA_OSCAR_JWT_SECRET` values | Either remove both env vars (lets them auto-share `/data/jwt_secret`) or make sure both have the same value |
| 5 failed login attempts → 429 | Rate-limit per IP (15-min window) | Wait it out; doesn't lock you out of the system |
| Forgot the operator password | No recovery by design | SSH to host, `rm /data/auth.json`, restart API, set fresh password via `/setup` |

---

## What "done" looks like

- All four containers running on the new image tags.
- Web UI requires sign-in; sidebar shows `operator | sign out`.
- Two operator JWTs generated and pasted into compose env (one for MCP, one for watcher).
- Watcher and MCP both start without 401 errors in logs.
- A test import via the watcher succeeds end-to-end.
- (If you use claude.ai connector) a fresh chat lists nights without auth errors.
- Backup the operator password + the contents of `/data/jwt_secret` + the two pasted JWTs in your password manager — anything that's not in compose env is irrecoverable.
