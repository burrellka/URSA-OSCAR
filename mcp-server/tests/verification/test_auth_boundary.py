"""In-process auth-boundary harness — APEX template §8.

Six assertions per ADR-002 / Decision 10:
1. /.well-known/oauth-authorization-server reachable; no registration_endpoint
2. POST /register returns ≠ 200/201 (DCR off)
3. POST /messages/ without bearer returns 401 with resource_metadata=...
4. Full PKCE auth-code flow with the pre-registered client yields a token
5. Issued token unblocks /messages/
6. Static bearer also unblocks /messages/

Runs entirely in-process via Starlette's TestClient — no Docker, no real
network. Catches OAuth misconfiguration bugs before any deploy.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
from pathlib import Path

import pytest

# Test env must be set BEFORE importing the server module — auth provider
# build is a side effect of import.
os.environ.setdefault("URSA_OSCAR_MCP_BEARER_TOKEN", "test-static-bearer-value")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("URSA_OSCAR_MCP_BASE_URL", "https://test.local")
os.environ.setdefault(
    "URSA_OSCAR_DB_PATH",
    str(Path(__file__).resolve().parents[1] / "_test_skipped.duckdb"),
)


from starlette.testclient import TestClient  # noqa: E402

from ursa_oscar_mcp import server  # noqa: E402 — import side-effect builds auth


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636 PKCE verifier + S256 challenge."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


@pytest.fixture(scope="module")
def app():
    return server.mcp.http_app(transport="sse")


def test_discovery_reachable_no_registration_endpoint(app):
    with TestClient(app) as c:
        r = c.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        body = r.json()
        assert "authorization_endpoint" in body
        assert "token_endpoint" in body
        assert "registration_endpoint" not in body, (
            "registration_endpoint should be absent — DCR is disabled per ADR-002"
        )


def test_dcr_disabled_register_rejected(app):
    with TestClient(app) as c:
        r = c.post("/register", json={"redirect_uris": ["https://x/cb"]})
        assert r.status_code not in (200, 201), (
            f"POST /register returned {r.status_code} — DCR should reject"
        )


def test_messages_requires_bearer_with_resource_metadata_hint(app):
    with TestClient(app) as c:
        r = c.post("/messages/")
        assert r.status_code == 401
        www_auth = r.headers.get("WWW-Authenticate", "")
        assert "resource_metadata=" in www_auth, (
            "401 WWW-Authenticate must include resource_metadata hint so "
            "claude.ai can discover OAuth endpoints"
        )


def test_full_pkce_authorization_code_flow_yields_token(app):
    """End-to-end: /authorize → /token → /messages with that token."""
    verifier, challenge = _pkce_pair()
    with TestClient(app) as c:
        r = c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": os.environ["URSA_OSCAR_MCP_OAUTH_CLIENT_ID"],
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "x",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 307), (
            f"/authorize should redirect (302/307), got {r.status_code}"
        )

        loc = r.headers["location"]
        assert "code=" in loc
        code = loc.split("code=")[1].split("&")[0]

        r = c.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": os.environ["URSA_OSCAR_MCP_OAUTH_CLIENT_ID"],
                "client_secret": os.environ["URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET"],
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 200, f"/token returned {r.status_code}: {r.text}"
        body = r.json()
        assert "access_token" in body
        access_token = body["access_token"]

        # OAuth-issued token must unblock the protected endpoint
        r = c.post(
            "/messages/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code != 401, (
            f"Issued access_token failed at /messages/ ({r.status_code})"
        )


def test_static_bearer_unblocks_messages(app):
    with TestClient(app) as c:
        r = c.post(
            "/messages/",
            headers={
                "Authorization": f"Bearer {os.environ['URSA_OSCAR_MCP_BEARER_TOKEN']}"
            },
        )
        assert r.status_code != 401, (
            f"Static bearer failed at /messages/ ({r.status_code})"
        )


def test_bogus_bearer_rejected(app):
    with TestClient(app) as c:
        r = c.post(
            "/messages/",
            headers={"Authorization": "Bearer absolutely-not-a-real-token"},
        )
        assert r.status_code == 401
