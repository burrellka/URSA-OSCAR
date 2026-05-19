# MCP connector issues

When claude.ai's Custom Connector or another MCP client can't connect to URSA-OSCAR's MCP server. The OAuth handshake has several failure modes, each with a distinct diagnostic.

## Symptom 1 — claude.ai dialog refuses to add the connector

You're in claude.ai → Settings → Connectors → Add custom connector. You enter the URL, client ID, client secret, and click Add. The dialog rejects the entry.

**Cause:** the URL or one of the credentials is wrong.

**Fix:** double-check each field:

- **URL** must end with `/sse` (e.g., `https://your-host.example.com/sse`)
- **Client ID** must match the value in `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` env var
- **Client Secret** must match `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`

claude.ai's dialog labels these as "(optional)" but they're required for URSA-OSCAR because DCR is disabled.

## Symptom 2 — claude.ai accepts the connector but tools don't appear

You added the connector successfully but starting a chat doesn't show URSA-OSCAR tools.

**Cause:** the OAuth flow completed but the tool discovery is failing.

**Fix:**

```bash
docker logs ursa-oscar-mcp 2>&1 | grep -E "tools|sse|MCP" | tail -20
```

If you see `Pre-registered OAuth client client_id=...` on startup, OAuth is configured. If you see no tool registration logs, something's wrong with how the server boots.

Restart the MCP container: `docker compose restart ursa-oscar-mcp`. The logs on next startup should include lines like:

```
[ursa-oscar-mcp] Pre-registered OAuth client client_id=xxxxx ...
[ursa-oscar-mcp] Server listening on port 8000
```

If those don't appear, the env var validation might be failing — check that all four required vars are set:

- `URSA_OSCAR_MCP_BEARER_TOKEN`
- `URSA_OSCAR_MCP_BASE_URL`
- `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`
- `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`

Missing any of these and the container exits at startup with a stderr message.

## Symptom 3 — Tools appear but every tool call fails

Tools are visible in claude.ai's tool palette but calling one returns an error.

**Most common cause:** the MCP container can't reach the api container, OR the MCP container's outgoing auth token is invalid.

**Fix:**

```bash
# Confirm MCP can reach the api
docker exec ursa-oscar-mcp curl -s http://ursa-oscar-api:8000/healthz
# Expected: {"ok":true,"service":"ursa-oscar-api"}

# Confirm MCP's outgoing auth token is set
docker exec ursa-oscar-mcp ls /data/service_tokens/mcp.jwt
# Expected: the file exists

# Confirm the token is valid
docker exec ursa-oscar-mcp env | grep MCP_API_TOKEN
# If set: explicit override is in use
# If empty: falls back to /data/service_tokens/mcp.jwt
```

If `/data/service_tokens/mcp.jwt` doesn't exist, restart the api container (it mints service tokens on startup):

```bash
docker compose restart ursa-oscar-api
```

If the token exists but the api still 401s, the JWT signing secret may have rotated since the token was minted. Force a re-mint:

```bash
rm /opt/ursa-oscar/data/service_tokens/mcp.jwt
docker compose restart ursa-oscar-api
```

After both containers are up, retry the tool call from claude.ai.

## Symptom 4 — Some tools work, others fail

A subset of tools return data, the rest fail.

**Most common cause:** specific tools depend on specific data being present. `get_nightly_summary` fails if you haven't imported any nights; `analyze_correlation` fails if you don't have 30+ paired nights; `analyze_prediction` requires 30+ training nights.

**Fix:** check the tool's response envelope. URSA-OSCAR's tools return structured errors:

```json
{
  "ok": false,
  "code": "INSUFFICIENT_DATA",
  "error": "Need at least 30 nights for predictive modeling; got 12."
}
```

If `code` is `INSUFFICIENT_DATA`, the issue is your dataset, not the connector.

If `code` is `INTERNAL_ERROR` or similar, that's a real failure — check `docker logs ursa-oscar-mcp` and `docker logs ursa-oscar-api` for exceptions.

## Symptom 5 — OAuth handshake gets stuck in a redirect loop

claude.ai shows the auth flow happening but redirects you back to the connector setup repeatedly.

**Cause:** `URSA_OSCAR_MCP_BASE_URL` doesn't match the URL claude.ai is actually hitting.

**Fix:** the `URSA_OSCAR_MCP_BASE_URL` env var is what URSA-OSCAR puts into the OAuth discovery JSON. If it says `https://mcp.example.com` but claude.ai is actually calling `https://mcp-public.example.com/sse`, the redirect URIs won't match.

Confirm:

```bash
docker exec ursa-oscar-mcp curl -s http://localhost:8000/.well-known/oauth-authorization-server | python -m json.tool
```

The returned URLs should match what claude.ai is hitting. If not, fix `URSA_OSCAR_MCP_BASE_URL` in the compose env and recreate the MCP container.

## Symptom 6 — DCR (Dynamic Client Registration) request appears

claude.ai's flow asks URSA-OSCAR to register a new client, and the request 404s.

**Cause:** claude.ai's flow expects to register dynamically, but URSA-OSCAR has DCR disabled. The /register endpoint isn't mounted.

**This is the expected behavior.** With DCR off, only the pre-registered client (your URSA_OSCAR_MCP_OAUTH_CLIENT_ID + SECRET) can authenticate. claude.ai's UI sometimes attempts DCR first and falls back to pre-registered when DCR fails.

If you're stuck on the DCR step and claude.ai isn't trying the pre-registered path:

- Confirm the client_id and client_secret you entered in claude.ai's dialog match the env vars exactly (no whitespace, no quotation marks)
- Try deleting the connector in claude.ai and re-adding it from scratch

## Symptom 7 — MCP works for curl/Claude Desktop but fails for claude.ai

You can use the MCP server via curl or Claude Desktop just fine, but claude.ai's Custom Connector specifically doesn't work.

**Cause:** curl/Desktop use the **static bearer token** path; claude.ai uses the **OAuth** path. They're independent.

**Fix:** the OAuth setup is its own thing. See `Docs/17-oauth-setup.md` in the repository for the full walkthrough. The key checks:

- `/.well-known/oauth-authorization-server` returns valid JSON (you can curl it without auth)
- `POST /register` returns 404 (DCR disabled, expected)
- The client_id and client_secret in claude.ai's dialog match the env vars

## Symptom 8 — Verify MCP Connectivity check fails

Settings → MCP Health Check → "Verify MCP Connectivity" button shows one or more red X marks.

**Cause:** one of the four standard checks is failing. The detail column shows which:

1. **OAuth discovery** — `/.well-known/oauth-authorization-server` is reachable and returns valid JSON
2. **No DCR** — `/register` returns 404 (DCR confirmed disabled)
3. **Static bearer** — bearer auth on `/sse` works with the configured static token
4. **OAuth client metadata** — the client ID + secret are registered

The detail message names the specific failure. Most fixes are env var corrections or container restarts.

## Recovery checklist for a stuck MCP

If you've tried everything above and MCP still doesn't work:

```bash
# 1. Confirm env vars are set
docker exec ursa-oscar-mcp env | grep URSA_OSCAR

# 2. Restart in order
docker compose restart ursa-oscar-api
sleep 5
docker compose restart ursa-oscar-mcp

# 3. Re-check
docker logs ursa-oscar-mcp 2>&1 | tail -20
```

If the env vars are right and the logs are clean and it still doesn't work, file an issue with:

- The output of `docker logs ursa-oscar-mcp 2>&1 | tail -100`
- The output of `curl -s https://your-host.example.com/.well-known/oauth-authorization-server`
- The output of the verify-mcp-live.sh script (`bash infra/verify-mcp-live.sh`)
