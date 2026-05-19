# Network security

URSA-OSCAR's network surface and the choices made to defend it. This page is operator-facing — what to do, what URSA-OSCAR does for you, where the boundaries are.

## What's exposed

By default, three host ports:

- **5063** — the web UI (nginx in the web container)
- **8085** — the MCP server (FastMCP SSE endpoint)
- **8000** is the internal-only api container port; **not** mapped to a host port in the production compose

The api container is **deliberately** not exposed to the host. The web container proxies `/api/v1/*` to the api over the internal `kairos-net` Docker network. Operators who need direct API access from the host can either `docker exec` into the api container or add an explicit port mapping.

## TLS termination

URSA-OSCAR doesn't terminate TLS itself. The operator's responsibility:

- **Cloudflare tunnel** — most common pattern. Cloudflare's edge handles TLS; the tunnel forwards plain HTTP to the docker host. URSA-OSCAR sees the `X-Forwarded-Proto: https` header.
- **nginx in front** — reverse proxy on the docker host or upstream router with Let's Encrypt or similar. Same `X-Forwarded-Proto: https` requirement.
- **Caddy** — handles ACME + reverse-proxying in one binary. Set `X-Forwarded-Proto $scheme;` in the Caddyfile.
- **LAN-only HTTP** — totally valid for homelab use where the operator only accesses the stack from inside the local network. The cookie's `Secure` flag is auto-detected (see below) so HTTP-on-LAN works without lockout.

## Scheme-aware cookie

The session cookie's `Secure` flag is set based on the actual request scheme:

1. If `request.url.scheme == 'https'` (direct HTTPS to the api) → Secure=True
2. If `X-Forwarded-Proto: https` header is present → Secure=True (proxy did it right)
3. If `Origin: https://...` header is present (browser was on HTTPS, proxy didn't forward) → Secure=True (fallback)
4. If `Referer: https://...` header is present → Secure=True (further fallback)
5. Otherwise → Secure=False, SameSite=Lax

The Origin/Referer fallback covers misconfigured reverse proxies that don't set X-Forwarded-Proto. In that case the connection diagnostic on `/login` shows a yellow banner pointing the operator at the proxy config.

For LAN-only HTTP access (`http://192.168.x.x:5063`), Secure=False is correct — the browser would refuse to send a Secure cookie back over HTTP, which is what produced the early-day "wrong password" lockout that was fixed in 0.13.2.

## JWT authentication

Every API endpoint except a small open list (`/healthz`, `/api/v1/auth/bootstrap-status`, `/api/v1/auth/bootstrap`, `/api/v1/auth/login`) requires authentication. Three bearer kinds accepted:

1. **httpOnly session cookie** — set by login, refreshed by password change. Expires after 24 hours.
2. **Authorization: Bearer <JWT>** — operator-generated 90-day tokens for scripts and the MCP/watcher service tokens.
3. **OAuth access token (MCP only)** — claude.ai's Custom Connector path.

All three resolve to the same operator identity (`"operator"`). The auth doesn't distinguish between "the operator browsing" and "the operator's script" — they're the same trust level.

## Rate limiting

The `/api/v1/auth/login` endpoint has an in-memory rate limiter: 5 failures per source IP per 15 minutes. The 6th attempt returns 429 with a `Retry-After` header. Successful logins reset the counter.

The rate limit protects against brute-force password guessing from the LAN. It does NOT protect against credential stuffing if the operator's password has been used elsewhere and leaked — defending against that is on the operator (don't reuse passwords).

The rate limit is in-memory only; restarting the api container clears it. This is by design — if you've locked yourself out, you can restart and try again immediately.

## MCP server's three accepted bearers

The MCP container's `/sse` endpoint accepts three bearer kinds:

1. **Static bearer** — `URSA_OSCAR_MCP_BEARER_TOKEN` env var. Used by curl, Claude Desktop, Claude Code MCP CLI. Constant-time compared.
2. **Operator JWT** — same JWT shape the api container issues. Signed with the same `URSA_OSCAR_JWT_SECRET`. Allows operator-issued tokens to work for MCP as well as API.
3. **OAuth access token** — claude.ai's Custom Connector path. Pre-registered single client, DCR disabled. PKCE required.

All three are bearer-checked on every request. Decline of one doesn't fall through to a less-secure alternative; each path verifies independently.

## OAuth setup is operator-managed

claude.ai's Custom Connector requires the operator to register the URL + pre-registered client_id + client_secret in claude.ai's UI. URSA-OSCAR doesn't issue these tokens dynamically; the operator generates them once with the python secrets module and pastes them into both ends (the URSA-OSCAR compose env AND claude.ai's connector dialog).

DCR (Dynamic Client Registration) is intentionally disabled. With DCR enabled, anyone reaching the OAuth endpoint could self-register a client and connect. DCR off means only the pre-registered client can ever authenticate.

The full setup walkthrough is in `Docs/17-oauth-setup.md` in the repository.

## Reverse-proxy headers

When deploying behind any reverse proxy, the operator MUST configure:

- `X-Forwarded-Proto $scheme;` (or equivalent) so URSA-OSCAR knows the original scheme
- `X-Forwarded-For $proxy_add_x_forwarded_for;` (or equivalent) so the rate limiter sees the real client IP instead of the proxy's IP

Without `X-Forwarded-Proto`, the cookie's Secure flag falls back to Origin/Referer detection (works but suboptimal — see the warning banner on `/login`).

Without `X-Forwarded-For`, the rate limiter treats every request as coming from the proxy's IP, so a single attacker can lock out everyone behind the proxy.

## What's encrypted at rest in `/data`

- `secrets.enc` — Fernet-encrypted AI provider API keys. Decrypts only with `/data/master.key`.
- `auth.json` — Argon2id-hashed password (not encrypted, but hashed irreversibly).
- `jwt_secret` and `service_tokens/*.jwt` — plain text, mode 0600. JWT signatures provide tamper resistance; the secret value protects the signing key.
- DuckDB file — plain. CPAP data, profile, manual logs are not encrypted.

If full-disk encryption is important to you, set up the host's storage with LUKS / FileVault / BitLocker / NAS-level encryption. URSA-OSCAR does not implement application-level encryption for non-secret data; the threat model assumes the host is trusted.

## Audit logging

Login attempts (successful and failed), password changes, and bootstrap events go to the api container's stdout at INFO level:

```
auth: login successful for operator (ip=192.168.13.5)
auth: incorrect password attempt from ip=192.168.13.5 (attempt 3/5)
auth: password changed for operator
auth: bootstrap completed; operator session issued
```

`docker logs ursa-oscar-api | grep 'auth:'` is the audit trail. Persisting these to DuckDB is a deferred enhancement (see Future direction).

## What URSA-OSCAR doesn't defend against

- **Operator-level account compromise.** If the operator's password is leaked or the host is compromised, attacker has full URSA-OSCAR access.
- **AI provider snooping.** The provider you configure (Claude, OpenAI, etc.) sees the conversations you send them. URSA-OSCAR cannot prevent this.
- **Side-channel timing attacks.** No constant-time padding on the analytical endpoints.
- **DDoS / volumetric attacks.** No rate limiting on read endpoints beyond the upstream proxy's protections.
- **Physical access to the docker host.** Out of scope.
