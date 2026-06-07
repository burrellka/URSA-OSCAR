# Optional add-on — external AI connector (MCP)

**Skip this entirely if you only want:**

- The web UI
- The Daily View, Trends, Reports
- The **in-app AI assistant** (chat panel inside URSA-OSCAR's web UI, where you bring your own API key)
- Automatic CPAP imports

**Read this only if you want:**

- An external AI client like **claude.ai** (with a Custom Connector) or **Claude Code** to reach your URSA-OSCAR data
- Your conversation history to live in claude.ai (project knowledge, multi-day threads) instead of inside URSA-OSCAR

The MCP container is the most complicated piece to set up because it requires:

- A **publicly-reachable URL** (claude.ai's servers will connect from the internet)
- **OAuth credentials** you generate yourself
- A **reverse proxy with TLS** between the internet and your MCP container

If you're not ready for that, come back later. The analytics layer is fully usable without this.

---

## What you're about to set up

```
[claude.ai]
    |
    | (HTTPS)
    v
[Cloudflare Tunnel or nginx]      <-- terminates TLS, public URL
    |
    | (HTTP, internal)
    v
[ursa-oscar-mcp container]        <-- OAuth + 17 tools
    |
    | (HTTP, docker network)
    v
[ursa-oscar-api container]        <-- the database + analytics
```

When you finish, you'll have:

- A public URL like `https://ursa-oscar-mcp.yourdomain.com`
- A Custom Connector configured in claude.ai that uses that URL
- Conversations in claude.ai that can call `get_nightly_summary`, `analyze_correlation`, `generate_report`, and 14 other tools against your URSA-OSCAR data

---

## Prerequisite — analytics stack already running

Don't start this guide unless [the main install](../../INSTALL.md) is complete and you can reach the web UI at `http://localhost:5063` (or your LAN equivalent). The MCP container reads from the api container, so the api has to exist first.

---

## Step 1 — Pick how you'll expose the public URL

Choose one of these. Each has its own subdomain story.

| Option | When to pick | Setup |
|---|---|---|
| **Cloudflare Tunnel** | You don't want to open ports on your router. Free for non-commercial use. Most common URSA-OSCAR pattern. | Cloudflare Tunnel docs at [developers.cloudflare.com/cloudflare-one/connections/connect-networks](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) |
| **nginx + Let's Encrypt** | You already run a reverse proxy and have a domain. | Standard `proxy_pass http://localhost:8085;` config plus `certbot --nginx -d ursa-oscar-mcp.yourdomain.com` |
| **Caddy** | You want auto-TLS with minimal config. | One line in your Caddyfile: `ursa-oscar-mcp.yourdomain.com { reverse_proxy localhost:8085 }` |
| **Tailscale Funnel** | You're in the Tailscale ecosystem and want a public URL without exposing your home IP. | `tailscale funnel 8085` |

Whichever you pick, you need:

- A subdomain pointing at your URSA-OSCAR host (e.g., `ursa-oscar-mcp.yourdomain.com`)
- TLS termination at the proxy (HTTPS on the public side)
- The proxy forwarding `X-Forwarded-Proto: https` to the container

URSA-OSCAR's MCP container handles `X-Forwarded-Proto` correctly out of the box as of 1.1.7 — but the proxy still has to send it.

---

## Step 2 — Generate the three secrets

You need three random strings:

```bash
# Bearer token (also usable for curl-style auth):
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OAuth client ID:
python -c "import secrets; print(secrets.token_urlsafe(16))"

# OAuth client secret:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

If you don't have Python, use any random-string generator that produces base64-url-safe output. Save all three — you'll need them in step 4.

---

## Step 3 — Create a .env file next to your compose

In the same directory as `docker-compose.yml`, create a file called `.env`:

```bash
# URSA-OSCAR MCP add-on secrets
URSA_OSCAR_MCP_BEARER_TOKEN=<paste the bearer token from step 2>
URSA_OSCAR_MCP_OAUTH_CLIENT_ID=<paste the OAuth client ID>
URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET=<paste the OAuth client secret>
URSA_OSCAR_MCP_BASE_URL=https://ursa-oscar-mcp.yourdomain.com
```

The `URSA_OSCAR_MCP_BASE_URL` must be the **exact public URL** including `https://` — the MCP container uses this to construct the OAuth metadata that claude.ai discovers. A wrong value breaks discovery silently.

**Important:** add `.env` to your `.gitignore` if this directory is in git. The bearer token + OAuth secret are sensitive.

---

## Step 4 — Uncomment the MCP service in your compose

Open `docker-compose.yml` and find the commented-out `ursa-oscar-mcp:` block near the bottom. Remove the leading `#` from every line. The block should look like this:

```yaml
  ursa-oscar-mcp:
    image: brain40/ursa-oscar-mcp:1.1.7
    container_name: ursa-oscar-mcp
    environment:
      URSA_OSCAR_API_URL: http://ursa-oscar-api:8000
      URSA_OSCAR_MCP_BEARER_TOKEN: ${URSA_OSCAR_MCP_BEARER_TOKEN}
      URSA_OSCAR_MCP_BASE_URL: ${URSA_OSCAR_MCP_BASE_URL}
      URSA_OSCAR_MCP_OAUTH_CLIENT_ID: ${URSA_OSCAR_MCP_OAUTH_CLIENT_ID}
      URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET: ${URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET}
      URSA_OSCAR_JWT_SECRET: ${URSA_OSCAR_JWT_SECRET:-}
      URSA_OSCAR_MCP_API_TOKEN: ${URSA_OSCAR_MCP_API_TOKEN:-}
    ports:
      - "8085:8000"
    volumes:
      - /srv/ursa-oscar/data:/data    # adjust to your data path
    restart: unless-stopped
    depends_on:
      - ursa-oscar-api
```

Edit the `volumes:` line to use the same data path as your api container (e.g., `C:\URSA-OSCAR\data:/data` on Windows).

Validate:

```bash
docker compose config --quiet && echo OK
```

---

## Step 5 — Start the MCP container

```bash
docker compose pull
docker compose up -d ursa-oscar-mcp
```

Check it came up:

```bash
docker compose logs --tail=20 ursa-oscar-mcp
```

You should see:

```
ursa-oscar-mcp: SSE listening on :8000 (oauth=ready, dcr=ENABLED, ...)
```

If the container exits immediately with an error about missing env vars, your `.env` file isn't being read — make sure it's in the same directory as `docker-compose.yml`.

---

## Step 6 — Route your public URL to the MCP container

This step depends on which proxy you picked in step 1. The goal: your subdomain (`https://ursa-oscar-mcp.yourdomain.com`) routes to `http://localhost:8085` on the URSA-OSCAR host, with `X-Forwarded-Proto: https` set.

**Cloudflare Tunnel:**

In the Cloudflare Tunnel dashboard, add a public hostname:
- Subdomain: `ursa-oscar-mcp`
- Domain: `yourdomain.com`
- Service: `http://localhost:8085`

Cloudflare sets `X-Forwarded-Proto` automatically.

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name ursa-oscar-mcp.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/ursa-oscar-mcp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ursa-oscar-mcp.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8085;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE needs these:
        proxy_buffering off;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

**Caddy:**

```
ursa-oscar-mcp.yourdomain.com {
    reverse_proxy localhost:8085
}
```

Caddy handles TLS and forwarded headers automatically.

---

## Step 7 — Verify the public URL is reachable

```bash
curl -s -i https://ursa-oscar-mcp.yourdomain.com/.well-known/oauth-protected-resource
```

You should get a 200 with JSON describing your MCP's OAuth setup — `resource_server`, `authorization_servers`, etc.

If you get 502 / 504 / connection refused, the proxy isn't routing correctly. If you get a 401 on this specific endpoint, that's a misconfiguration — this endpoint is supposed to be public.

---

## Step 8 — Register URSA-OSCAR as a Custom Connector in claude.ai

In claude.ai:

1. **Settings → Connectors → Add custom connector** (or **Custom integrations**, depending on UI version)
2. **MCP server URL:** `https://ursa-oscar-mcp.yourdomain.com/sse`
3. **Authentication:** OAuth 2.1 with PKCE
4. Click **Connect**

claude.ai will redirect you to URSA-OSCAR's authorize endpoint, you'll authenticate (your operator password), and claude.ai will receive tokens that let it call URSA-OSCAR's 17 tools.

Once connected:

- Start a new claude.ai conversation
- Enable the URSA-OSCAR connector for that conversation
- Ask "What were last night's stats?" — Claude calls `get_nightly_summary` and tells you

The full set of tools is documented in the in-app help under the AI Assistant section.

---

## Troubleshooting MCP setup

| Symptom | Likely cause | Fix |
|---|---|---|
| `oauth=ready` log line never appears | Env vars not loaded | Check `.env` is in same dir as compose; restart MCP container |
| claude.ai discovery fails | `URSA_OSCAR_MCP_BASE_URL` mismatch | Must match your actual public URL exactly (including `https://`) |
| 502 from your subdomain | Proxy not routing to 8085 | Test with `curl http://localhost:8085/version` on the host — should return JSON |
| 401 on `/authorize` | Pre-registered client mismatch | Confirm `OAUTH_CLIENT_ID` + `OAUTH_CLIENT_SECRET` in `.env` match what claude.ai is being given |
| Tools list returns empty | Tools registered against wrong module | Should be impossible with 1.1.7 — file a bug if it happens |

---

## What you can do with it

Once connected, in claude.ai:

- "Summarize this past week"
- "What's my AHI trend been since I changed pressure on May 15?"
- "Generate a provider PDF for the last 30 nights"
- "Was last night unusual compared to my baseline?"
- "What should I bring to my next sleep clinic appointment?"

Plus 12 more tools the AI can chain together for its own analysis.

The full primary-use-case write-up is in the [README](../../README.md#the-primary-use-case-ai-as-informed-thinking-partner).
