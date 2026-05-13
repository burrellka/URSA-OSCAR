#!/usr/bin/env bash
# Per-deploy MCP curl verification — APEX template §7.4 adapted for URSA-OSCAR.
#
# Usage:
#   HOST=http://localhost:8082 URSA_OSCAR_MCP_BEARER_TOKEN=... ./infra/verify-mcp-live.sh
#
# Exits non-zero on any failure. The Makefile `make verify-mcp-live HOST=...`
# target wraps this.

set -euo pipefail

HOST="${HOST:?HOST env var or arg required, e.g. HOST=http://localhost:8082}"
BEARER="${URSA_OSCAR_MCP_BEARER_TOKEN:?URSA_OSCAR_MCP_BEARER_TOKEN must be set}"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK:   $*"; }

# 1. Discovery metadata reachable, no registration_endpoint (DCR off)
discovery=$(curl -fsS "${HOST}/.well-known/oauth-authorization-server")
echo "$discovery" | grep -q '"authorization_endpoint"' \
  || fail "discovery missing authorization_endpoint"
if echo "$discovery" | grep -q '"registration_endpoint"'; then
  fail "discovery exposes registration_endpoint — DCR should be disabled"
fi
ok "/.well-known/oauth-authorization-server reachable (DCR off confirmed)"

# 2. DCR truly off — POST /register returns ≠ 200/201
register_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${HOST}/register" \
  -H "Content-Type: application/json" -d '{}')
if [[ "$register_code" == "200" || "$register_code" == "201" ]]; then
  fail "POST /register returned $register_code — DCR should reject"
fi
ok "POST /register returned $register_code (DCR confirmed off)"

# 3. Bogus bearer rejected (401)
bogus_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${HOST}/messages/" \
  -H "Authorization: Bearer absolutely-not-a-real-token")
if [[ "$bogus_code" != "401" ]]; then
  fail "Bogus bearer returned $bogus_code, expected 401"
fi
ok "Bogus bearer returns 401"

# 4. Static bearer works — anything that is NOT 401 is acceptable
#    (a real /messages POST without an SSE session yields 4xx from the
#    handler, but the auth gate has been passed)
real_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${HOST}/messages/" \
  -H "Authorization: Bearer ${BEARER}")
if [[ "$real_code" == "401" ]]; then
  fail "Static bearer failed auth — got 401"
fi
ok "Static bearer passes auth (response code $real_code, ≠ 401)"

echo
echo "MCP live verification PASSED against ${HOST}"
