"""Single-user authentication — Phase 6.4.

URSA-OSCAR 1.0 ships as a single-tenant homelab application. To make
the single-tenant model deployable outside homelab-perfect-trust
networks (cloud VPS, shared-LAN, etc.), 1.0 includes single-user
password authentication on top of the existing transport-level
controls (MCP OAuth, etc.).

Module layout:
  hashing.py     — Argon2id wrap (passlib)
  tokens.py      — JWT encode/decode (HS256, two lifetimes)
  store.py       — /data/auth.json reader/writer with 600 perms
  rate_limit.py  — in-memory brute-force tracker
  middleware.py  — FastAPI dependency for protected endpoints
  routes.py      — /api/v1/auth/* endpoints

Design points (from the work order):
  - One user named "operator" — no user registry
  - Password hashed with Argon2id; stored at /data/auth.json (600)
  - JWT signed with URSA_OSCAR_JWT_SECRET (env, auto-generated on first
    start if absent — same operator-action-free pattern as
    URSA_OSCAR_SECRET_KEY from Phase 5)
  - Two token lifetimes:
      - Session token (24h) — browser cookie
      - API token (90d) — MCP server + watcher + future services
  - Brute force: 5 failures per IP per 15min, in-memory only
  - Recovery: delete /data/auth.json, redeploy → bootstrap flow
  - No password recovery via email; intentional for the threat model
"""

from .middleware import require_auth  # noqa: F401  re-export for endpoint use
