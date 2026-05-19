# Single-tenant trust boundary

URSA-OSCAR is single-tenant by architecture, not just by convention. This page describes what that means concretely, what it isn't, and the implications.

## What "single-tenant" means here

One operator per URSA-OSCAR deployment. One DuckDB file per deployment. One set of secrets per deployment. One auth.json with one password hash. The literal string `"operator"` appears throughout the auth code as the only subject identity that exists.

There is no concept of a second user. There is no `users` table. There is no role-based access control. The auth surface answers exactly one question: "is the caller the operator or not?"

## The trust boundary

The boundary is **host file access to `/data`**. Anything inside that directory is, from URSA-OSCAR's perspective, "trusted." Anything outside, "untrusted."

What's trusted because it's in `/data`:

- The operator's password hash (auth.json)
- The Fernet master key (master.key) that decrypts AI provider API keys
- The JWT signing secret (jwt_secret) that mints and verifies all session/service tokens
- The MCP and watcher service tokens (service_tokens/*.jwt)
- The encrypted AI provider keys (secrets.enc)
- The operator's CPAP data (DuckDB), profile, vocabulary, etc.

What's untrusted:

- Network callers without a valid bearer/cookie
- Other containers on the docker host that don't share the `/data` mount
- Other tenants on a multi-tenant host (URSA-OSCAR doesn't isolate)

## What this means for deployment

**Appropriate** deployment scenarios:

- A personal homelab / NAS where you are the only operator
- A dedicated VPS or cloud instance you own where you are the only operator
- A friends-and-family setup where each person runs their **own** URSA-OSCAR instance on their own hardware

**Not appropriate**:

- A shared server with non-trusted users
- A multi-tenant SaaS platform
- Any scenario where multiple people need separate "accounts"
- Any scenario where you want fine-grained access (e.g., "my partner can see summaries but not raw waveforms")

## What if your household has multiple CPAP users?

Run two URSA-OSCAR deployments. Different ports, different `/data` volumes, different operator passwords. They're independent — different DuckDBs, different AI provider keys, different everything.

This is the **Multi-instance** topic — see that page for the operational details.

## Why not multi-tenant?

Multi-tenant would require:

- A `users` table with per-user row-level security
- Per-user DuckDB isolation (or a single DB with `user_id` columns plumbed through every query)
- A new auth model that scopes JWTs to a user and a tenant
- UI work to surface multiple users' views
- Operational work to provision and de-provision users
- Backup/restore work to handle per-user data export
- A different trust boundary (anyone-with-host-access becomes one of many users, role separation becomes meaningful)

That's a fundamentally different product. Building it would re-architect Phases 1-6 from the ground up. The maintainer made a deliberate decision not to go there — see the **Future direction** page.

## The implications for auth

Because URSA-OSCAR is single-tenant:

- **No email recovery.** There's no email system to recover into, no second account to validate against. If you lose your password, you delete `/data/auth.json` and re-bootstrap.
- **No 2FA.** Single-factor password auth + JWT sessions. Adding 2FA without changing the trust model would mostly add ceremony without adding security (anyone with host access bypasses 2FA by just reading auth.json).
- **No active token revocation.** Tokens are JWTs verified by signature. Rotating the JWT signing secret revokes ALL outstanding tokens at once (operator + MCP + watcher); there's no per-token blocklist.
- **No audit log persistence.** Login attempts go to stdout. Persisting them in DuckDB would enable a per-operator audit view, but since there's only one operator, the audit log would only ever show "the operator logged in / failed to log in." Limited value.

These aren't bugs. They're consequences of the chosen trust boundary.

## What the auth still protects against

Even within the single-tenant model, the auth surface protects against:

- **Anyone on the LAN who doesn't have the password.** URSA-OSCAR isn't a tool you put on a public IP and hope nobody finds it; password auth prevents drive-by access from anyone on your home network.
- **Browser-based attacks from third-party sites.** httpOnly cookies + samesite=strict (when accessed over HTTPS) prevent CSRF.
- **Mid-session token theft over insecure transport.** The cookie's `Secure` flag (when applicable) prevents the session cookie from being sent over plain HTTP.
- **Brute-force password guessing.** 5 failures per IP per 15 minutes = 429 rate limit.

The auth doesn't protect against the operator themselves, the host root user, or anyone with read access to the underlying `/data` directory. That's by design.

## Threat model summary

Capability assumed for operator: full administrative access to the docker host, ability to read/write any file in `/data`, ability to restart any container.

Capability protected against: anyone who doesn't have either the operator password OR a valid JWT signed by `URSA_OSCAR_JWT_SECRET`.

Capability deliberately NOT protected: backups of `/data` being stored insecurely (your responsibility), the host being compromised at the OS level (not URSA-OSCAR's threat model), the AI provider being subpoenaed for the queries you send them (out of scope).
