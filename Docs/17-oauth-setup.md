# 17 — OAuth Setup for claude.ai Custom Connector

**Audience:** Kevin, configuring the URSA-OSCAR MCP server as a custom connector inside claude.ai.
**One-time setup.** After this is done, every new chat that includes URSA-OSCAR sees the 8-tool surface automatically. Rotation procedure at the bottom.

Mirrors APEX's `docs/17-oauth-setup.md`; same security posture (DCR off + pre-registered client) per ADR-002.

---

## What's happening

claude.ai's "Add custom connector" dialog runs a full OAuth 2.1 authorization-code flow with PKCE on top of MCP's discovery spec. **Dynamic Client Registration is disabled** — the only OAuth client that can authenticate is the one pre-registered from `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` and `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` env vars on the `ursa-oscar-mcp` container.

This is a deliberate security posture: with DCR enabled, anyone who reached `https://your-public-host.example.com` could self-register a client and connect. With DCR off and a pre-registered client, you (and only you) hold the secret needed to complete the OAuth flow.

The legacy static `URSA_OSCAR_MCP_BEARER_TOKEN` path is unchanged. curl, Claude Desktop, and Claude Code keep working with the same token.

---

## Prerequisites

- `ursa-oscar-mcp` on **0.1.3 or newer**.
- `URSA_OSCAR_MCP_BASE_URL` set to the public URL (default: `https://your-public-host.example.com`).
- `URSA_OSCAR_MCP_BEARER_TOKEN` set (curl / Desktop / Code path).
- `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` and `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` set. **All four required** — `ursa-oscar-mcp` refuses to start without them.

---

## Step 1 — generate the OAuth client credentials

On any machine with Python:

```
python -c "import secrets; print('URSA_OSCAR_MCP_OAUTH_CLIENT_ID=' + secrets.token_urlsafe(16))"
python -c "import secrets; print('URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET=' + secrets.token_urlsafe(32))"
```

Two random URL-safe strings. The client_id can be shorter (16 bytes); the secret should be 32+ bytes. Treat both like passwords — don't paste them in chat, Slack, screenshots, or commit them to source.

If you already used `infra/.env.example` as a template, you have the commands inline there too.

---

## Step 2 — set the ursa-oscar-mcp env

In Dockge, confirm the `ursa-oscar-mcp` service has all of:

```
URSA_OSCAR_MCP_BEARER_TOKEN=<your 32-byte static bearer>
URSA_OSCAR_MCP_BASE_URL=https://your-public-host.example.com
URSA_OSCAR_MCP_OAUTH_CLIENT_ID=<id from step 1>
URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET=<secret from step 1>
URSA_OSCAR_API_URL=http://ursa-oscar-api:8000
```

Pin the image to `brain40/ursa-oscar-mcp:0.1.3` (or later). Recreate the container.

If any of the four mandatory vars is missing, the container will exit immediately with a clear stderr message. Check the Dockge logs if it won't stay up.

---

## Step 3 — verify the discovery endpoints work

```
curl.exe -s https://your-public-host.example.com/.well-known/oauth-authorization-server
```

Expected JSON includes `authorization_endpoint`, `token_endpoint`, and **no `registration_endpoint`** (DCR off — endpoint isn't mounted).

```
curl.exe -s -X POST https://your-public-host.example.com/register -d '{}'
```

Expected: 404 or method-not-allowed. **A 200 here means DCR is somehow still on — stop and check the image version.**

For a fuller end-to-end check, run:
```bash
HOST=https://your-public-host.example.com \
  URSA_OSCAR_MCP_BEARER_TOKEN=<the static bearer> \
  bash infra/verify-mcp-live.sh
```

Expect 4/4 OK lines.

---

## Step 4 — add the connector in claude.ai

1. Open **claude.ai** in a browser. Sign in.
2. Settings → Connectors (Beta) → **Add custom connector**.
3. Fill in:
   - **Name:** `ursa-oscar`
   - **Remote MCP server URL:** `https://your-public-host.example.com/sse`
   - **OAuth Client ID:** *paste the value of `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`*
   - **OAuth Client Secret:** *paste the value of `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`*
4. Click **Add**.

The dialog labels Client ID and Client Secret as "(optional)", but they are **required** here — without them claude.ai can't authenticate (DCR is disabled).

claude.ai will:
1. Hit `/sse` and observe the 401 with the discovery hint.
2. Fetch `/.well-known/oauth-authorization-server` for endpoint URLs.
3. Use the Client ID and Secret you entered to call `/authorize` (PKCE).
4. Exchange the auth code at `/token` for an access_token.
5. Use that token on `/sse`.

If the wrong values are entered, the OAuth handshake fails and the connector won't connect. `ursa-oscar-mcp`'s logs will show `/authorize` with an unknown client_id (rejected) or `/token` with bad credentials (401).

---

## Step 5 — smoke test

Start a new chat with the URSA-OSCAR connector enabled. Confirm the tools panel shows the URSA-OSCAR tool surface (8 tools as of v0.1.3). Ask Claude:

> *Run `list_available_nights`.*

Expected: a list of nights currently in the DuckDB. If DuckDB is empty, the response is `{"ok": true, "data": {"nights": []}}` — that's fine, it means the connector works; just no data yet.

Follow up with:

> *Import any nights in `/cpap-import` then summarize 2026-05-08.*

Claude will route to `trigger_import` then `get_nightly_summary` — and you'll see the full end-to-end MCP tool chain in action.

---

## Operational notes

- **Token TTL is 1 hour.** claude.ai handles refresh transparently using the refresh_token issued alongside the access_token.
- **Token cache lives in process memory.** When `ursa-oscar-mcp` restarts (Dockge "Recreate", redeploy, etc.), all OAuth tokens are invalidated; claude.ai re-runs the auth-code flow on next request — no user action needed.
- **Audit log:** `/authorize` and `/token` calls are logged to `ursa-oscar-mcp`'s stdout. Tokens themselves are never logged.
- **Static bearer still works.** curl, Claude Desktop, and Claude Code keep using `URSA_OSCAR_MCP_BEARER_TOKEN` exactly as before.

---

## Rotation procedure

If you suspect any of the secrets have leaked (e.g., pasted in chat, committed to git, screenshotted):

1. Generate new values for `URSA_OSCAR_MCP_OAUTH_CLIENT_ID`, `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET`, and `URSA_OSCAR_MCP_BEARER_TOKEN` per Step 1.
2. Update Dockge env, recreate `ursa-oscar-mcp`.
3. Update the claude.ai connector dialog with the new id/secret (delete the old connector or edit the existing one — depends on claude.ai's UI for that day).
4. Update any curl scripts / Claude Desktop / Claude Code configs that use the old `URSA_OSCAR_MCP_BEARER_TOKEN`.

There's no warning period — old credentials stop working as soon as the container restarts.

---

## Threat model (single-user homelab)

What stops a randomly-arrived attacker who knows the URL:

| Layer | Effect |
| --- | --- |
| Cloudflare tunnel | Public passthrough — does not authenticate |
| `/.well-known/oauth-*` | World-readable per RFC 8414 / 9728. Leaks endpoint URLs (intended). |
| `/register` | **404 — DCR disabled.** Attackers can't self-mint clients. |
| `/authorize` | Rejects unknown `client_id`. Only the env-pinned client can pass. |
| `/token` | Rejects mismatched `client_secret`. PKCE prevents code interception. |
| `/sse`, `/messages/` | Bearer-gated. The only ways to get a valid bearer are (a) the OAuth dance with the right client_id+secret or (b) the static `URSA_OSCAR_MCP_BEARER_TOKEN`. |

The effective auth surface is: *holds at least one of `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` or `URSA_OSCAR_MCP_BEARER_TOKEN`*. Appropriate for a single-user homelab system.

If URSA-OSCAR ever goes multi-tenant (commercial offering, friends-sharing-one-instance), this model needs replacement — see APEX's `docs/17-oauth-setup.md` for the same caveat. Single-deploy-per-user homelab installs reuse this same setup procedure.

---

## What "done" looks like

- `/.well-known/oauth-authorization-server` returns JSON metadata (Step 3 verified).
- `POST /register` returns 404 — DCR is disabled (Step 3 verified).
- The connector dialog in claude.ai accepts the URL + Client ID + Client Secret and shows the connector as connected (Step 4 verified).
- A fresh chat can call at least one MCP tool end-to-end without auth errors (Step 5 verified).
- `bash infra/verify-mcp-live.sh` against the public URL passes 4/4.
