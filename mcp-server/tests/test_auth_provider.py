"""Phase 6.4 — UrsaOscarOAuthProvider bearer-token acceptance tests.

These are pure unit tests against the auth provider's verify_token
method; they do NOT need the live API fixture from conftest.py.
The autouse api_server fixture still spins up (session-scoped) but
nothing here calls into it.

The three accepted bearer kinds and the rejection cases:

  - Static bearer (constant-time compare against env-supplied secret)
  - Operator JWT signed with URSA_OSCAR_JWT_SECRET (kind=session OR api)
  - Pre-registered OAuth access token (covered by InMemoryOAuthProvider's
    own tests; we don't re-test the OAuth state machine here)

  Rejection: malformed JWT, signature mismatch, expired, wrong subject,
  unknown kind, empty token.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from ursa_oscar_mcp.auth import UrsaOscarOAuthProvider
from ursa_oscar_mcp.jwt_tokens import TokenError, decode_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SECRET = "test-jwt-secret-not-the-real-one"
STATIC_BEARER = "static-bearer-token-for-tests"
SUBJECT = "operator"


def _mint(
    secret: str = TEST_SECRET,
    kind: str = "api",
    sub: str = SUBJECT,
    issued: datetime | None = None,
    lifetime: timedelta = timedelta(days=90),
) -> str:
    """Mint an HS256 JWT mirroring the backend's encode_token shape.
    Kept local so tests don't depend on the backend's tokens module."""
    iat = issued or datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "kind": kind,
        "iat": int(iat.timestamp()),
        "exp": int((iat + lifetime).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _provider(
    *,
    static_bearer: str | None = STATIC_BEARER,
    jwt_secret: str | None = TEST_SECRET,
) -> UrsaOscarOAuthProvider:
    """Build a provider with no OAuth state — we only exercise the
    bearer fallbacks. base_url is a stub; InMemoryOAuthProvider
    requires it for URL composition during OAuth flows we don't run."""
    from mcp.server.auth.settings import ClientRegistrationOptions

    return UrsaOscarOAuthProvider(
        base_url="https://test.invalid",
        static_bearer_token=static_bearer,
        jwt_secret=jwt_secret,
        client_registration_options=ClientRegistrationOptions(
            enabled=False, valid_scopes=None, default_scopes=None,
        ),
    )


# ---------------------------------------------------------------------------
# decode_token unit tests (jwt_tokens module)
# ---------------------------------------------------------------------------


def test_decode_token_accepts_valid_api_token():
    token = _mint(kind="api")
    claims = decode_token(TEST_SECRET, token)
    assert claims["sub"] == SUBJECT
    assert claims["kind"] == "api"


def test_decode_token_accepts_valid_session_token():
    token = _mint(kind="session", lifetime=timedelta(hours=24))
    claims = decode_token(TEST_SECRET, token)
    assert claims["kind"] == "session"


def test_decode_token_rejects_wrong_secret():
    token = _mint(secret="some-other-secret")
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, token)


def test_decode_token_rejects_expired():
    token = _mint(
        issued=datetime.now(timezone.utc) - timedelta(days=100),
        lifetime=timedelta(days=90),
    )
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, token)


def test_decode_token_rejects_wrong_subject():
    token = _mint(sub="attacker")
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, token)


def test_decode_token_rejects_unknown_kind():
    # Hand-craft a payload with kind="admin" — _mint() always sets
    # session/api so we go through jose directly here.
    payload = {
        "sub": SUBJECT, "kind": "admin",
        "iat": int(time.time()), "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, token)


def test_decode_token_rejects_malformed():
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, "not.a.jwt")


def test_decode_token_rejects_empty():
    with pytest.raises(TokenError):
        decode_token(TEST_SECRET, "")


# ---------------------------------------------------------------------------
# UrsaOscarOAuthProvider.verify_token integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_token_static_bearer_accepted():
    """The original static-bearer path still works post-Phase 6.4."""
    provider = _provider()
    result = await provider.verify_token(STATIC_BEARER)
    assert result is not None
    assert result.client_id == "static-bearer"
    assert result.expires_at is None  # static bearer never expires


@pytest.mark.asyncio
async def test_verify_token_jwt_api_token_accepted():
    """A 90d operator-issued API JWT is accepted as a bearer."""
    provider = _provider()
    token = _mint(kind="api")
    result = await provider.verify_token(token)
    assert result is not None
    assert result.client_id == "operator-jwt:api"
    assert result.expires_at is not None
    assert result.expires_at > int(time.time())


@pytest.mark.asyncio
async def test_verify_token_jwt_session_token_accepted():
    """A 24h session JWT is also accepted — useful for browser-tab
    scripts that grab the session cookie value and call MCP directly."""
    provider = _provider()
    token = _mint(kind="session", lifetime=timedelta(hours=24))
    result = await provider.verify_token(token)
    assert result is not None
    assert result.client_id == "operator-jwt:session"


@pytest.mark.asyncio
async def test_verify_token_jwt_with_wrong_secret_rejected():
    """A JWT signed with the wrong secret falls through to OAuth, which
    has no matching token, so the final result is None."""
    provider = _provider()
    bad_token = _mint(secret="attacker-secret")
    result = await provider.verify_token(bad_token)
    assert result is None


@pytest.mark.asyncio
async def test_verify_token_expired_jwt_rejected():
    provider = _provider()
    expired = _mint(
        issued=datetime.now(timezone.utc) - timedelta(days=100),
        lifetime=timedelta(days=90),
    )
    result = await provider.verify_token(expired)
    assert result is None


@pytest.mark.asyncio
async def test_verify_token_random_string_rejected():
    """Not a JWT, not the static bearer, not an OAuth token — rejected."""
    provider = _provider()
    result = await provider.verify_token("definitely-not-a-real-token")
    assert result is None


@pytest.mark.asyncio
async def test_verify_token_jwt_disabled_when_no_secret():
    """If URSA_OSCAR_JWT_SECRET isn't configured, the JWT path is
    skipped entirely — only OAuth + static bearer work, preserving
    0.10.x backward-compat."""
    provider = _provider(jwt_secret=None)
    # A valid JWT is now just an opaque string the OAuth state machine
    # doesn't recognize.
    token = _mint(kind="api")
    result = await provider.verify_token(token)
    assert result is None
    # Static bearer still works.
    result_static = await provider.verify_token(STATIC_BEARER)
    assert result_static is not None
    assert result_static.client_id == "static-bearer"


@pytest.mark.asyncio
async def test_verify_token_static_bearer_disabled_when_unset():
    """Symmetric check: with no static bearer configured, only JWT +
    OAuth work."""
    provider = _provider(static_bearer=None)
    # Random string used to "accidentally match" if static_bearer
    # wasn't gated — confirm it doesn't.
    result = await provider.verify_token("")
    assert result is None
    # JWT still works.
    token = _mint(kind="api")
    result_jwt = await provider.verify_token(token)
    assert result_jwt is not None
    assert result_jwt.client_id == "operator-jwt:api"
