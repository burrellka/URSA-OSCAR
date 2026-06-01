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


# ---------------------------------------------------------------------------
# RFC 7591 Dynamic Client Registration tests (1.1.5)
# ---------------------------------------------------------------------------


def _dcr_provider(client_store_path):
    """Build a provider with DCR enabled, isolated to a tmp client store.

    Mirrors the build_auth_provider() flow: enables DCR, sets up a
    pre-registered client_id so persistence-exclusion can be tested.
    """
    from mcp.server.auth.settings import ClientRegistrationOptions
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl
    from ursa_oscar_mcp.auth import CLAUDE_AI_CALLBACK

    provider = UrsaOscarOAuthProvider(
        base_url="https://test.invalid",
        static_bearer_token=None,
        jwt_secret=None,
        client_store_path=client_store_path,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=None, default_scopes=None,
        ),
    )
    # Simulate build_auth_provider's pre-registration of the claude.ai client.
    pre_id = "preregistered-test-id"
    provider.clients[pre_id] = OAuthClientInformationFull(
        client_id=pre_id,
        client_secret="preregistered-secret",
        client_id_issued_at=int(time.time()),
        redirect_uris=[AnyUrl(CLAUDE_AI_CALLBACK)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    )
    provider.preregistered_client_id = pre_id
    return provider


@pytest.mark.asyncio
async def test_dcr_registration_creates_client_with_caller_redirect_uris(tmp_path):
    """1.1.5 — A DCR registration must produce a client with the
    redirect_uris the caller specified, not a copy of the pre-registered
    claude.ai client. This is the KAIROS bug class: prior versions had
    DCR disabled, which meant every non-claude.ai MCP client was
    unauthorizable.
    """
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    store = tmp_path / "clients.json"
    provider = _dcr_provider(store)

    new_id = "kairos-test-client-id"
    new_info = OAuthClientInformationFull(
        client_id=new_id,
        client_secret="kairos-test-secret",
        client_id_issued_at=int(time.time()),
        redirect_uris=[AnyUrl("https://kairos.example.test/cb")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    )
    await provider.register_client(new_info)

    # The new client is in memory and looks up correctly.
    fetched = await provider.get_client(new_id)
    assert fetched is not None
    assert fetched.client_id == new_id
    redirect_uris = [str(u) for u in (fetched.redirect_uris or [])]
    assert redirect_uris == ["https://kairos.example.test/cb"], (
        f"DCR-registered client should carry the caller redirect_uri, "
        f"got {redirect_uris}. If this fails, the register_client override "
        f"is not honoring the request body."
    )

    # The pre-registered client is unaffected.
    pre = await provider.get_client("preregistered-test-id")
    assert pre is not None
    assert [str(u) for u in (pre.redirect_uris or [])] == [
        "https://claude.ai/api/mcp/auth_callback"
    ]


@pytest.mark.asyncio
async def test_dcr_registration_persists_to_disk(tmp_path):
    """1.1.5 — Registrations survive provider reconstruction (container
    restart simulation). Without persistence, every restart would force
    every MCP client to re-register, which violates the operator's
    expectation that connectors stay configured.
    """
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    store = tmp_path / "clients.json"

    # Round 1: register a client.
    provider_a = _dcr_provider(store)
    new_info = OAuthClientInformationFull(
        client_id="persisted-test-id",
        client_secret="persisted-secret",
        client_id_issued_at=int(time.time()),
        redirect_uris=[AnyUrl("https://example.test/cb")],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    )
    await provider_a.register_client(new_info)

    # Verify the file was written.
    assert store.exists(), "DCR registration should write the client store file"
    raw = store.read_text(encoding="utf-8")
    assert "persisted-test-id" in raw

    # Round 2: build a fresh provider with the same store path.
    provider_b = _dcr_provider(store)
    fetched = await provider_b.get_client("persisted-test-id")
    assert fetched is not None, (
        "Persisted client should be loaded into the new provider "
        "instance, simulating container restart."
    )
    assert [str(u) for u in (fetched.redirect_uris or [])] == [
        "https://example.test/cb"
    ]


@pytest.mark.asyncio
async def test_dcr_does_not_persist_preregistered_client(tmp_path):
    """1.1.5 — The pre-registered claude.ai client lives in env vars and
    is reconstructed on every boot. Persisting it to disk would risk a
    stale entry masking an env-var change (e.g. the operator rotated
    the client_secret in env but the disk file still has the old one).
    """
    import json
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    store = tmp_path / "clients.json"
    provider = _dcr_provider(store)

    # Register one DCR client so the store file gets written.
    await provider.register_client(OAuthClientInformationFull(
        client_id="dcr-client",
        client_secret="secret",
        client_id_issued_at=int(time.time()),
        redirect_uris=[AnyUrl("https://example.test/cb")],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    ))

    # The store contains only the DCR client, not the pre-registered one.
    data = json.loads(store.read_text(encoding="utf-8"))
    assert "dcr-client" in data
    assert "preregistered-test-id" not in data, (
        "Pre-registered claude.ai client must be excluded from disk "
        "persistence (env vars are the source of truth for it)."
    )


def test_dcr_load_skips_corrupt_store_gracefully(tmp_path):
    """1.1.5 — A corrupt JSON file should not block startup. The provider
    falls back to an empty client table (plus whatever the env-var
    pre-registration adds) and logs a warning.
    """
    store = tmp_path / "clients.json"
    store.write_text("{not valid json", encoding="utf-8")
    provider = _dcr_provider(store)
    # Provider constructed without raising; the corrupt store was skipped.
    # Only the pre-registered client should be present.
    assert set(provider.clients.keys()) == {"preregistered-test-id"}


@pytest.mark.asyncio
async def test_refresh_token_survives_access_token_natural_expiry(tmp_path):
    """1.1.6 regression — when an access token expires naturally,
    URSA's load_access_token must drop only the access token, NOT the
    associated refresh token. Per RFC 6749 §6, refresh tokens are
    designed to outlive their access tokens.

    KAIROS hit this in production at ~1 hour after issuance: their
    access token expired, URSA's verify_token detected the expiry and
    called the upstream _revoke_internal which cascades to delete the
    refresh token from refresh_tokens dict. KAIROS then tried to
    refresh and got invalid_grant ("refresh token does not exist").
    """
    from mcp.server.auth.provider import AccessToken, RefreshToken

    provider = _dcr_provider(tmp_path / "clients.json")

    # Inject an expired access token + a never-expiring refresh token,
    # wired up via the access↔refresh maps the upstream provider keeps.
    access_str = "expired-access-token"
    refresh_str = "still-valid-refresh-token"
    client_id = "dcr-client-for-refresh-test"

    provider.access_tokens[access_str] = AccessToken(
        token=access_str,
        client_id=client_id,
        scopes=[],
        expires_at=int(time.time()) - 60,  # expired 1 minute ago
    )
    provider.refresh_tokens[refresh_str] = RefreshToken(
        token=refresh_str,
        client_id=client_id,
        scopes=[],
        expires_at=None,  # refresh tokens never expire in this provider
    )
    provider._access_to_refresh_map[access_str] = refresh_str
    provider._refresh_to_access_map[refresh_str] = access_str

    # Call load_access_token, which is the path verify_token takes for
    # OAuth bearers. Returns None because the access is expired.
    result = await provider.load_access_token(access_str)
    assert result is None

    # The expired access token is gone from the access store.
    assert access_str not in provider.access_tokens

    # CRITICAL: the refresh token must still be present. Without this,
    # /token grant_type=refresh_token would return invalid_grant.
    assert refresh_str in provider.refresh_tokens, (
        "Refresh token must outlive its associated access token per "
        "RFC 6749 §6. If this fails, the upstream cascade-delete "
        "behavior leaked through (1.1.6 regression)."
    )

    # Map cleanup: the now-dangling access→refresh entry is gone, and
    # the refresh→access back-reference (which pointed to the dead
    # access token) is also cleared. The refresh token entry itself
    # stays alive for the /token exchange.
    assert access_str not in provider._access_to_refresh_map
    assert refresh_str not in provider._refresh_to_access_map


@pytest.mark.asyncio
async def test_refresh_token_unexpired_access_returns_normally(tmp_path):
    """1.1.6 — sanity check that the override doesn't break the
    happy path. A non-expired access token still returns the
    AccessToken object."""
    from mcp.server.auth.provider import AccessToken

    provider = _dcr_provider(tmp_path / "clients.json")
    access_str = "still-valid-access-token"
    provider.access_tokens[access_str] = AccessToken(
        token=access_str,
        client_id="some-client",
        scopes=[],
        expires_at=int(time.time()) + 3600,  # valid for 1 more hour
    )

    result = await provider.load_access_token(access_str)
    assert result is not None
    assert result.token == access_str


@pytest.mark.asyncio
async def test_explicit_revocation_still_cascades(tmp_path):
    """1.1.6 — natural expiry should NOT cascade, but explicit
    revocation (via revoke_token, the /revoke endpoint, or refresh-
    token rotation in exchange_refresh_token) still should. This test
    confirms the override is scoped narrowly: only the
    load_access_token path is changed.
    """
    from mcp.server.auth.provider import AccessToken, RefreshToken

    provider = _dcr_provider(tmp_path / "clients.json")
    access_str = "revoke-test-access"
    refresh_str = "revoke-test-refresh"
    provider.access_tokens[access_str] = AccessToken(
        token=access_str, client_id="c", scopes=[], expires_at=None,
    )
    provider.refresh_tokens[refresh_str] = RefreshToken(
        token=refresh_str, client_id="c", scopes=[], expires_at=None,
    )
    provider._access_to_refresh_map[access_str] = refresh_str
    provider._refresh_to_access_map[refresh_str] = access_str

    # Explicit revocation of the access token cascades — kills the
    # refresh too, as the upstream OAuth spec permits and most
    # implementations choose.
    provider._revoke_internal(access_token_str=access_str)
    assert access_str not in provider.access_tokens
    assert refresh_str not in provider.refresh_tokens, (
        "Explicit revocation should still cascade to the refresh "
        "token. If this fails, the override accidentally changed "
        "_revoke_internal's behavior, which would break the /revoke "
        "endpoint and refresh-token rotation."
    )


def test_dcr_load_skips_corrupt_entries_individually(tmp_path):
    """1.1.5 — A single corrupt entry should not lose every other
    persisted client. Each entry is reconstructed independently; failures
    are logged and the rest load normally.
    """
    import json

    store = tmp_path / "clients.json"
    # Valid pydantic shape for one entry, malformed for the other.
    valid_entry = {
        "client_id": "valid-id",
        "client_secret": "valid-secret",
        "client_id_issued_at": int(time.time()),
        "redirect_uris": ["https://example.test/cb"],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    store.write_text(json.dumps({
        "valid-id": valid_entry,
        "corrupt-id": {"client_id": "corrupt-id"},  # missing required fields
    }), encoding="utf-8")

    provider = _dcr_provider(store)
    assert "valid-id" in provider.clients
    assert "corrupt-id" not in provider.clients
