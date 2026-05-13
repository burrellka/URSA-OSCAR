# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in URSA-OSCAR, please **do not open a public issue**. Instead, report it privately:

- Email the maintainer via your GitHub-linked email (or open a private security advisory under the repo's "Security" tab on GitHub).
- Include: a description of the vulnerability, steps to reproduce, the version (image tag or commit SHA) you observed it on, and any suggested mitigation.

You should expect an acknowledgement within 7 days and a triage decision within 14 days.

## Scope

URSA-OSCAR is a self-hosted service. The security surface includes:

- **MCP server (`ursa-oscar-mcp`)** — exposed publicly (typically via a Cloudflare Tunnel or similar). OAuth 2.1 + PKCE + static bearer fallback. Auth bypass, token validation flaws, or unauthorized access to user data are in scope.
- **API server (`ursa-oscar-api`)** — internal-only by default. Privilege escalation, path traversal in import, or unauthenticated access to user data are in scope.
- **Web UI (`ursa-oscar-web`)** — typically LAN-only, may be exposed publicly. XSS, CSRF, or session-handling flaws are in scope.

## Out of scope

- Theoretical concerns without a working PoC.
- Vulnerabilities requiring host-level access already (if an attacker has shell on your NAS, they have everything).
- Denial-of-service via legitimate API use (no rate-limit yet — that's a Phase 4+ item).

## Data handling guarantees

URSA-OSCAR is single-user-per-instance. The project ships **zero personal health data**:

- The public repository contains no real EDF files, SD-card exports, or recorded therapy data.
- Per-instance runtime state (DuckDB, `vocab.json`, `profile.json`) lives in the operator's mounted volume and is never published.
- Contributors are required to keep personal data out of PRs (see [CONTRIBUTING.md](CONTRIBUTING.md) §2).

If you find a PR or commit that accidentally exposes personal data, please report it via the same private-disclosure channel above so we can scrub before it spreads.
