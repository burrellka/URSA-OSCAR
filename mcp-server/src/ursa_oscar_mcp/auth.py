"""URSA-OSCAR OAuth provider — lifted from APEX template §5.

Same structure as `ApexOAuthProvider`. THREE parallel auth paths land at the
same protected surface (third added in Phase 6.4):

1. OAuth 2.1 + PKCE for claude.ai's custom-connector dialog. Pre-registered
   single client; DCR disabled. ClientRegistrationOptions(enabled=False)
   means `/register` is never mounted.
2. Static bearer for curl / Claude Desktop / Claude Code. The static bearer
   is opaque to the OAuth state machine — accepted only as a Bearer header.
3. Operator JWT bearer (Phase 6.4): a 24h session OR 90d API token issued
   by the API container's ``/auth/generate-api-token`` endpoint, signed
   with the shared ``URSA_OSCAR_JWT_SECRET``. Lets a script with a
   90d JWT call MCP tools directly without the OAuth dance.

Per Doc 17 / ADR-002, the container exits fast at startup if any of the
four required env vars is missing. The JWT secret is optional — when
absent, the JWT path simply isn't activated (backward-compat with
0.10.x deployments).
"""
from __future__ import annotations

import hmac
import logging
import os
import sys
import time

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from .jwt_tokens import TokenError, decode_token, resolve_jwt_secret


logger = logging.getLogger("ursa-oscar-mcp.auth")


# claude.ai's MCP custom-connector callback URL — observed in production
# from the connector dialog handshake. Stable across deployments. Per
# template §5, hardcoded here.
CLAUDE_AI_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


class UrsaOscarOAuthProvider(InMemoryOAuthProvider):
    """InMemoryOAuthProvider + static-bearer + operator-JWT fallbacks in
    verify_token. Three accepted bearer kinds, evaluated in this order:

      1. Static bearer (constant-time compare against URSA_OSCAR_MCP_BEARER_TOKEN)
      2. Operator JWT (HS256-verified against URSA_OSCAR_JWT_SECRET, accepts
         both "session" and "api" token_kind claims — both are operator-issued)
      3. OAuth access token (delegates to InMemoryOAuthProvider's table)

    None of these paths leak each other's tokens: a static bearer never
    becomes an OAuth state-machine entry, and a JWT never appears in
    the OAuth client table.
    """

    def __init__(
        self,
        *,
        static_bearer_token: str | None = None,
        jwt_secret: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._static_bearer = static_bearer_token
        self._jwt_secret = jwt_secret

    async def verify_token(self, token: str) -> AccessToken | None:
        # 1. Static bearer — constant-time compare.
        if self._static_bearer and hmac.compare_digest(token, self._static_bearer):
            return AccessToken(
                token=token,
                client_id="static-bearer",
                scopes=[],
                expires_at=None,
            )

        # 2. Operator JWT bearer (Phase 6.4). The same signing key that
        # the API container uses; JWTs may be either the operator's
        # 24h session cookie value (rare in MCP) or a 90d API token
        # generated via the web UI.
        if self._jwt_secret:
            try:
                claims = decode_token(self._jwt_secret, token)
                return AccessToken(
                    token=token,
                    client_id=f"operator-jwt:{claims.get('kind', 'unknown')}",
                    scopes=[],
                    expires_at=int(claims["exp"]),
                )
            except TokenError:
                # Not a valid operator JWT — fall through to OAuth.
                pass

        # 3. OAuth access token — InMemoryOAuthProvider's table lookup.
        return await super().verify_token(token)


def _require_static_bearer() -> str:
    token = os.environ.get("URSA_OSCAR_MCP_BEARER_TOKEN", "").strip()
    if not token:
        sys.stderr.write(
            "ERROR: URSA_OSCAR_MCP_BEARER_TOKEN must be set.\n"
            'Generate one with: python -c "import secrets; '
            "print(secrets.token_urlsafe(32))\"\n"
        )
        sys.exit(1)
    return token


def _require_oauth_client_credentials() -> tuple[str, str]:
    cid = os.environ.get("URSA_OSCAR_MCP_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        sys.stderr.write(
            "ERROR: URSA_OSCAR_MCP_OAUTH_CLIENT_ID and "
            "URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET must both be set.\n"
            "DCR is disabled; the only OAuth client that can authenticate "
            "is the one pre-registered from these env vars.\n"
            "Generate them with:\n"
            '  python -c "import secrets; print(\'ID=\' + secrets.token_urlsafe(16))"\n'
            '  python -c "import secrets; print(\'SECRET=\' + secrets.token_urlsafe(32))"\n'
        )
        sys.exit(1)
    return cid, csec


def build_auth_provider() -> UrsaOscarOAuthProvider:
    """Construct the OAuth provider from env. Exits fast on misconfiguration."""
    base_url = os.environ.get("URSA_OSCAR_MCP_BASE_URL", "").rstrip("/")
    if not base_url:
        sys.stderr.write(
            "ERROR: URSA_OSCAR_MCP_BASE_URL must be set "
            "(e.g. http://localhost:8082 for dev, "
            "https://your-public-host.example.com for prod).\n"
        )
        sys.exit(1)
    static_bearer = _require_static_bearer()
    pre_id, pre_secret = _require_oauth_client_credentials()
    # JWT secret is optional — when present, MCP accepts operator JWTs
    # as a third bearer kind. When absent, only OAuth + static bearer
    # work (backward-compat with 0.10.x).
    jwt_secret = resolve_jwt_secret()

    provider = UrsaOscarOAuthProvider(
        base_url=base_url,
        static_bearer_token=static_bearer,
        jwt_secret=jwt_secret,
        client_registration_options=ClientRegistrationOptions(
            enabled=False,
            valid_scopes=None,
            default_scopes=None,
        ),
    )

    # Mandatory pre-registration. Without DCR, this is the only client that
    # can ever authenticate via OAuth. claude.ai must enter the same id and
    # secret in the connector dialog's "Client ID / Client Secret" fields.
    provider.clients[pre_id] = OAuthClientInformationFull(
        client_id=pre_id,
        client_secret=pre_secret,
        client_id_issued_at=int(time.time()),
        redirect_uris=[AnyUrl(CLAUDE_AI_CALLBACK)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    )
    logger.info(
        "Pre-registered OAuth client client_id=%s redirect_uri=%s (DCR disabled)",
        pre_id, CLAUDE_AI_CALLBACK,
    )
    return provider
