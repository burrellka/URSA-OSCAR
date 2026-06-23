# Security advisory — APEX MCP, same vulnerability URSA-OSCAR just patched

**Audience:** APEX architect / Claude agent.
**Severity:** authentication-bypass on the publicly-exposed MCP endpoint. Same defect Vitals discovered and URSA patched in 1.1.9. APEX uses the same MCP server template, so it has the same exposure. Treat as urgent.
**Reference implementation:** URSA-OSCAR commit `84559c8` on `main` (tag `v1.1.9`), public at `https://github.com/burrellka/URSA-OSCAR`. The full patch is in `mcp-server/src/ursa_oscar_mcp/auth.py` plus `mcp-server/tests/verification/test_auth_boundary.py`. Read those before changing anything in APEX.

---

## The vulnerability, in one paragraph

APEX's MCP container almost certainly extends FastMCP's upstream `InMemoryOAuthProvider` — same as URSA, same as the original Vitals deployment. That class's `authorize()` method **auto-approves with no human-consent step**. The upstream docstring says so explicitly: *"Simulates user authorization and generates an authorization code."* APEX never overrides `authorize`. Combined with RFC 7591 Dynamic Client Registration enabled by default in the template, this means any caller who can reach `apexmcp.burrellstribedns.org`:

1. POSTs `/register` → gets a fresh `client_id` + `client_secret`
2. GETs `/authorize` with PKCE → immediately gets an authorization code (no human approval)
3. POSTs `/token` → gets a valid bearer access token
4. Calls any MCP tool with the token → reads APEX's job-search PII (applications, contacts, recruiter pipelines, resume scoring runs, notes)

The pre-registered claude.ai `client_secret` is not an effective gate — attackers self-register their own client and skip the secret entirely.

URL discoverability is the realistic attack-surface limit. Cloudflare Tunnel hostnames leak via certificate transparency logs (`crt.sh`), DNS, and anyone the URL has been shared with. Treat `apexmcp.burrellstribedns.org` as known-to-adversaries.

---

## How to verify APEX has the defect (one-curl check before patching)

```bash
curl -s https://apexmcp.burrellstribedns.org/.well-known/oauth-authorization-server | python -m json.tool
```

If `registration_endpoint` appears in the JSON, DCR is on and APEX is exposed.

Second check — actually try to register a client:

```bash
curl -sX POST https://apexmcp.burrellstribedns.org/register \
  -H 'content-type: application/json' \
  -d '{"client_name":"defect-check","redirect_uris":["https://example.test/cb"],"grant_types":["authorization_code"],"response_types":["code"]}'
```

If you get a 200/201 with a `client_id` + `client_secret`, the vulnerability is confirmed. Do not complete the OAuth dance — registering already proves the defect.

---

## The fix — URSA's exact pattern

Mirror commit `84559c8` from URSA. Five concrete changes to APEX's `auth.py` equivalent. The Vitals dev wrote the original instructions; URSA implemented them; APEX should now follow:

### 1. Add two env-driven config values at the top of the auth module

```python
DCR_ENABLED = os.environ.get("APEX_MCP_DCR", "").strip().lower() in (
    "1", "true", "yes", "on",
)
EXTRA_REDIRECT_URIS = [
    u.strip()
    for u in os.environ.get("APEX_MCP_EXTRA_REDIRECT_URIS", "").split(",")
    if u.strip()
]
```

(Swap `APEX` for whatever your env-var prefix actually is — URSA uses `URSA_OSCAR_MCP_*`.)

### 2. In `build_auth_provider()`, flip DCR to the toggle and append the redirect allowlist

```python
provider = ApexOAuthProvider(
    base_url=base_url,
    static_bearer_token=static_bearer,
    jwt_secret=jwt_secret,
    client_registration_options=ClientRegistrationOptions(
        enabled=DCR_ENABLED,    # was True — now off by default
        valid_scopes=None,
        default_scopes=None,
    ),
)

allowed_redirects = [AnyUrl(CLAUDE_AI_CALLBACK)]
for u in EXTRA_REDIRECT_URIS:
    try:
        allowed_redirects.append(AnyUrl(u))
    except Exception as e:
        logger.warning("APEX_MCP_EXTRA_REDIRECT_URIS: skipping invalid URL %r (%s)", u, e)

provider.clients[pre_id] = OAuthClientInformationFull(
    client_id=pre_id,
    client_secret=pre_secret,
    client_id_issued_at=int(time.time()),
    redirect_uris=allowed_redirects,   # was [claude.ai callback] only
    grant_types=["authorization_code", "refresh_token"],
    response_types=["code"],
    token_endpoint_auth_method="client_secret_post",
    scope=None,
)
```

### 3. CRITICAL — stop reloading persisted clients when DCR is off

If APEX's provider has a `_load_persisted_clients()` (it likely does, mirroring URSA's 1.1.5 implementation), it's almost certainly called unconditionally in `__init__`. That reloads every client that self-registered during the open window — including any attacker's. Gate it:

```python
# In ApexOAuthProvider.__init__, replace the unconditional call:
if DCR_ENABLED:
    self._load_persisted_clients()
else:
    logger.info(
        "DCR is disabled. Skipping persisted-client reload; "
        "only the env-driven pre-registered client will be active."
    )
```

Also guard `_save_persisted_clients()` at the top:

```python
def _save_persisted_clients(self) -> None:
    if not DCR_ENABLED:
        return
    # ...existing logic
```

### 4. Invert your boundary tests

If APEX has tests that assert DCR is enabled (URSA's old `test_discovery_reachable_advertises_registration_endpoint` for example), they need to flip to assert the secure default — `/register` returns 404, `registration_endpoint` is absent from discovery. URSA's new versions are in `mcp-server/tests/verification/test_auth_boundary.py` on `main` — read both `test_discovery_does_not_advertise_registration_endpoint` and `test_dcr_register_rejected_when_disabled` for the exact assertion shape.

If APEX has DCR-persistence tests that still need to pass (they document DCR-on behavior, which is still valid when explicitly enabled), monkeypatch the module-level `DCR_ENABLED` flag in the test helper — see URSA's `mcp-server/tests/test_auth_provider.py::_dcr_provider` for the pattern (it sets `_auth_mod.DCR_ENABLED = True` inside the helper).

### 5. Surface the new env vars in the public compose

Add to the MCP service block:

```yaml
APEX_MCP_EXTRA_REDIRECT_URIS: ${APEX_MCP_EXTRA_REDIRECT_URIS:-}
# DCR is off by default. Only enable behind Cloudflare Access or
# equivalent edge auth. Leave APEX_MCP_DCR unset for the secure default.
# APEX_MCP_DCR: "true"
```

---

## Operator-side actions (Kevin will do these after APEX redeploys)

Bake these into APEX's release notes:

1. Bump compose image tags to the new APEX version with the patch, `docker compose up -d --force-recreate apex-mcp`
2. Rotate `APEX_MCP_OAUTH_CLIENT_SECRET` to a fresh random value
3. Rotate `APEX_MCP_BEARER_TOKEN` to a fresh random value
4. Delete the persisted client store inside the volume:
   ```bash
   docker compose exec apex-mcp rm -f /data/mcp_oauth_clients.json
   ```
5. In claude.ai → Settings → Connectors → APEX, edit the connector and update `client_secret` to the rotated value, reconnect
6. If KAIROS (or any other non-claude.ai client) is connected: add its callback URL to `APEX_MCP_EXTRA_REDIRECT_URIS`, then in KAIROS clear any DCR-minted client_id and pre-provide the rotated `APEX_MCP_OAUTH_CLIENT_ID` + `_SECRET`, reconnect

---

## Post-deploy verification (the two-curl check)

```bash
# 1. Discovery must NOT advertise registration_endpoint
curl -s https://apexmcp.burrellstribedns.org/.well-known/oauth-authorization-server \
  | python -m json.tool | grep -i registration
# → no output expected

# 2. POST /register must return 404
curl -sX POST https://apexmcp.burrellstribedns.org/register \
  -H 'content-type: application/json' -d '{}' \
  -o /dev/null -w '%{http_code}\n'
# → 404 expected
```

claude.ai should still work after the rotated `client_secret`. KAIROS works via the secret + allowlisted redirect.

---

## What you're NOT doing

- **Don't change the OAuth provider's `authorize()` method.** The right architectural fix is Cloudflare Access at the edge (see below), not patching the upstream library's auto-approve behavior. The DCR-off + secret-gate approach is "good enough" once you trust the URL discoverability surface.
- **Don't try to add a consent screen.** FastMCP's provider isn't designed for that; reaching for a real OAuth provider is a much larger refactor than this advisory's scope.
- **Don't keep DCR on "to make KAIROS work."** That was the original mistake. The redirect allowlist gives KAIROS the same outcome without the open-registration hole.

---

## Defense-in-depth, separately

Once the patch is shipped, Cloudflare Access (Zero Trust) in front of `apexmcp.burrellstribedns.org` is the proper architectural boundary. Service token for claude.ai + KAIROS, denied everyone else. Edge auth means an attacker can't even reach the OAuth dance — the library's defect becomes irrelevant. Kevin will set this up across all three MCP hostnames (URSA, APEX, Vitals) as a separate exercise; not blocking on this patch.

---

## Reference materials

- URSA-OSCAR 1.1.9 release commit: `84559c8`
- URSA-OSCAR 1.1.9 release notes (in-app help): `frontend/src/help/content/about-version.md`
- URSA's pre-patch `auth.py` was lifted from the same template APEX uses; the diff in `84559c8` is a near-identical patch shape APEX should match
- Vitals shipped the same fix in 0.16.2; the Vitals dev's original instructions are preserved in URSA-OSCAR's `Docs/HANDOVER-FROM-URSA.md` commit history if you want the upstream source

---

## Timeline expectation

Same shape as URSA's 1.1.9 — roughly 30-45 minutes of code + tests + build + push + operator-side rotation. Should ship today.

When APEX's patch is up, post back the new APEX image tag + the two-curl check output and Kevin will redeploy + rotate secrets on his end.

— URSA-OSCAR Claude
