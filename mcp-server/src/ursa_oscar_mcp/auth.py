"""URSA-OSCAR OAuth provider — lifted from APEX template §5, then extended
for RFC 7591 Dynamic Client Registration in 1.1.5.

FOUR parallel paths reach the same protected surface:

1. OAuth 2.1 + PKCE with a pre-registered single client. claude.ai's
   custom-connector dialog uses this when the operator types the
   pinned client_id + secret from env. The pre-registered client
   survives container restart because it's reconstructed from env
   vars on every boot.
2. OAuth 2.1 + PKCE with a dynamically-registered client (RFC 7591).
   Any MCP client (KAIROS, third-party MCP clients, etc.) can POST
   to ``/register`` with its own ``redirect_uris`` and receive a
   fresh ``client_id`` + ``client_secret``. The registration persists
   to ``/data/mcp_oauth_clients.json`` so it survives container
   restart. 1.1.5 fix — prior versions had DCR disabled which broke
   every non-claude.ai MCP client.
3. Static bearer for curl / Claude Desktop / Claude Code. The static
   bearer is opaque to the OAuth state machine; accepted only as a
   Bearer header.
4. Operator JWT bearer (Phase 6.4): a 24h session OR 90d API token
   issued by the API container's ``/auth/generate-api-token`` endpoint,
   signed with the shared ``URSA_OSCAR_JWT_SECRET``. Lets a script
   with a 90d JWT call MCP tools directly without the OAuth dance.

Persistence model for DCR-registered clients (1.1.5):

  - Storage: ``/data/mcp_oauth_clients.json``, mode 0600, atomic writes
    via tmpfile + rename. The MCP container needs ``/data:rw`` (not
    ``:ro`` as previously) because of this. The pre-registered
    claude.ai client is NOT written to disk (it's reconstructed from
    env vars on every boot; persisting would risk a stale entry
    masking an env-var change).
  - Concurrency: ``threading.RLock`` around the write. FastMCP runs
    handlers on the asyncio loop; register_client may fire from
    multiple in-flight requests but the rename is the atomic boundary.
  - Failure mode: if the disk write fails, the registration still
    lives in the in-memory ``self.clients`` dict, so the immediate
    caller gets a working client_id. The next container restart will
    lose that registration. The operator sees the warning in logs and
    can fix the volume.

Per Doc 17 / ADR-002, the container exits fast at startup if any of the
four required env vars is missing. The JWT secret is optional — when
absent, the JWT path simply isn't activated (backward-compat with
0.10.x deployments).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import stat
import sys
import threading
import time
from pathlib import Path

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


# Persistent location for DCR-registered clients. Lives on the shared
# /data volume so registrations survive container restarts. 1.1.5 fix.
DEFAULT_CLIENT_STORE_PATH = Path("/data/mcp_oauth_clients.json")


# 1.1.9 SECURITY — DCR is now off by default.
#
# The upstream fastmcp.InMemoryOAuthProvider's authorize() method
# auto-approves with no human-consent step (it says so in the docstring:
# "Simulates user authorization and generates an authorization code").
# We extend that class but never override authorize(), so URSA inherits
# the auto-approve behavior.
#
# Combined with DCR enabled, this means any caller who can reach the
# public MCP URL can POST /register, then immediately complete the
# OAuth dance with no real authentication, and pull data via the
# resulting bearer token. The client_secret is the only effective
# gate; DCR removes it because attackers self-register their own
# client and secret.
#
# Fix: DCR is opt-in via URSA_OSCAR_MCP_DCR. Default is OFF — the
# pre-registered claude.ai client is the only registered client, and
# its redirect_uri allowlist (CLAUDE_AI_CALLBACK + any extras the
# operator added via URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS) controls who
# can complete the flow even if the client_secret leaks.
#
# To re-enable DCR (only safe behind Cloudflare Access / similar):
#   URSA_OSCAR_MCP_DCR=true
#
# To allow non-claude.ai clients (KAIROS etc.) to connect via the
# pre-registered client + shared secret, allowlist their redirect:
#   URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS=https://kairos.example.com/callback
# (comma-separate multiple).
DCR_ENABLED = os.environ.get("URSA_OSCAR_MCP_DCR", "").strip().lower() in (
    "1", "true", "yes", "on",
)
EXTRA_REDIRECT_URIS = [
    u.strip()
    for u in os.environ.get("URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS", "").split(",")
    if u.strip()
]


class UrsaOscarOAuthProvider(InMemoryOAuthProvider):
    """InMemoryOAuthProvider + static-bearer + operator-JWT fallbacks in
    verify_token, plus RFC 7591 dynamic client registration with disk
    persistence.

    Three accepted bearer kinds in verify_token, evaluated in this order:

      1. Static bearer (constant-time compare against URSA_OSCAR_MCP_BEARER_TOKEN)
      2. Operator JWT (HS256-verified against URSA_OSCAR_JWT_SECRET, accepts
         both "session" and "api" token_kind claims — both are operator-issued)
      3. OAuth access token (delegates to InMemoryOAuthProvider's table)

    None of these paths leak each other's tokens: a static bearer never
    becomes an OAuth state-machine entry, and a JWT never appears in
    the OAuth client table.

    DCR (1.1.5):

      - ``register_client(client_info)`` overrides the parent to persist
        the new client to ``/data/mcp_oauth_clients.json``.
      - ``__init__`` loads any persisted clients into ``self.clients``
        immediately after super().__init__() so prior DCR registrations
        survive container restarts.
      - The pre-registered claude.ai client (added by build_auth_provider
        AFTER this class constructs) is tagged via ``preregistered_client_id``
        so it can be excluded from disk persistence. Persisting it would
        risk a stale entry masking an env-var change.
    """

    def __init__(
        self,
        *,
        static_bearer_token: str | None = None,
        jwt_secret: str | None = None,
        client_store_path: Path | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._static_bearer = static_bearer_token
        self._jwt_secret = jwt_secret
        self._client_store_path = client_store_path or DEFAULT_CLIENT_STORE_PATH
        # Set by build_auth_provider after construction. The pre-registered
        # client_id is excluded from persistence (always reconstructed from
        # env vars at startup).
        self.preregistered_client_id: str | None = None
        # Guards the JSON file write. RLock so the same thread can nest
        # save → load if we ever need that.
        self._store_lock = threading.RLock()
        # 1.1.9 SECURITY — only load persisted DCR clients when DCR is
        # actually enabled. When operators upgrade with DCR turned off
        # (the new default), any client that self-registered during the
        # 1.1.5-through-1.1.8 open window stays dead — its persisted
        # entry is ignored on boot. Operators can also `rm` the JSON
        # store on the volume to remove the file entirely.
        if DCR_ENABLED:
            self._load_persisted_clients()
        else:
            logger.info(
                "DCR is disabled (URSA_OSCAR_MCP_DCR unset/false). "
                "Skipping persisted-client reload; only the env-driven "
                "pre-registered client will be active."
            )

    # ----- DCR persistence -----

    def _load_persisted_clients(self) -> None:
        """Populate self.clients from the on-disk JSON. Missing or
        corrupt entries are skipped with a warning. Missing file is
        normal on first run."""
        path = self._client_store_path
        if not path.exists():
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Persisted client store at %s is unreadable; starting "
                "with no DCR clients loaded: %s", path, e,
            )
            return
        loaded = 0
        for cid, entry in data.items():
            try:
                info = OAuthClientInformationFull(**entry)
                self.clients[cid] = info
                loaded += 1
            except Exception as e:
                logger.warning(
                    "Skipping corrupt persisted client entry %s: %s",
                    cid, e,
                )
        if loaded:
            logger.info(
                "Loaded %d DCR-registered client(s) from %s", loaded, path,
            )

    def _save_persisted_clients(self) -> None:
        """Atomically write the DCR-registered clients to disk.

        Excludes the pre-registered claude.ai client (reconstructed from
        env vars). Uses tmpfile + rename so a partial write cannot leave
        the JSON corrupt. Best-effort 0600 on POSIX.

        1.1.9 SECURITY — short-circuits when DCR is disabled so even an
        in-process register_client call (which shouldn't fire when DCR
        is off, but belt-and-suspenders) can't seed the disk store.
        """
        if not DCR_ENABLED:
            return
        # Snapshot under the lock so a concurrent register_client doesn't
        # race the dict iteration.
        with self._store_lock:
            to_persist: dict[str, dict] = {}
            for cid, info in self.clients.items():
                if cid == self.preregistered_client_id:
                    continue
                try:
                    to_persist[cid] = info.model_dump(mode="json", exclude_none=False)
                except Exception as e:
                    logger.warning(
                        "Skipping client %s in persistence (serialize failed): %s",
                        cid, e,
                    )

            path = self._client_store_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(to_persist, indent=2), encoding="utf-8",
            )
            tmp.replace(path)
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                # Windows / non-POSIX filesystem — best-effort, the data
                # dir is operator-only on those platforms anyway.
                pass

    async def register_client(
        self, client_info: OAuthClientInformationFull,
    ) -> None:
        """RFC 7591 dynamic client registration.

        Delegates to the parent for in-memory storage + validation, then
        persists to disk so the registration survives container restart.
        Disk-write failures are logged and swallowed: the in-memory
        registration still works for the immediate caller, and the
        operator gets a warning in logs.
        """
        await super().register_client(client_info)
        try:
            # Run the synchronous file write off the event loop so a
            # slow disk doesn't stall the async handler.
            await asyncio.to_thread(self._save_persisted_clients)
            logger.info(
                "DCR-registered client client_id=%s redirect_uris=%s",
                client_info.client_id,
                [str(u) for u in (client_info.redirect_uris or [])],
            )
        except Exception:
            logger.exception(
                "Failed to persist DCR-registered client %s; the "
                "registration is in-memory only and will be lost on "
                "container restart. Check /data is writable.",
                client_info.client_id,
            )

    # ----- access-token expiry cleanup (1.1.6) -----

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Return the AccessToken for a valid bearer, or None if absent
        or expired.

        Overrides the upstream ``InMemoryOAuthProvider.load_access_token``
        to fix a refresh-token cascade-delete bug. The upstream
        implementation calls ``_revoke_internal(access_token_str=...)``
        on natural expiry, which cascades through ``_access_to_refresh_map``
        and deletes the associated refresh token from ``self.refresh_tokens``.

        Per RFC 6749 §6, refresh tokens are designed to OUTLIVE their
        associated access tokens — they exist precisely so a client can
        obtain a new access token after the short-lived one expires.
        Cascading deletion breaks that contract. KAIROS hit this in
        production: client got 401 on its 1-hour-old access token,
        attempted to refresh, URSA returned ``invalid_grant`` because
        the refresh token had been auto-deleted by the prior
        ``verify_token`` call that detected the expiry.

        Fixed behavior: on natural expiry, drop the access token from
        ``self.access_tokens`` and clean up the now-dangling map entries
        (the ``_access_to_refresh_map`` entry for this access token, and
        the ``_refresh_to_access_map`` entry pointing back to it). The
        refresh token stays in ``self.refresh_tokens`` so a subsequent
        ``grant_type=refresh_token`` exchange succeeds. Explicit
        revocation paths (``revoke_token``, the ``/revoke`` endpoint,
        and ``exchange_refresh_token``'s rotation) all still flow
        through ``_revoke_internal`` and still cascade as the spec
        permits for explicit revocation.
        """
        token_obj = self.access_tokens.get(token)
        if token_obj is None:
            return None
        if (
            token_obj.expires_at is not None
            and token_obj.expires_at < time.time()
        ):
            # Drop just this expired access token + the maps that point
            # at it. The refresh token stays alive for /token to honor.
            self.access_tokens.pop(token, None)
            associated_refresh = self._access_to_refresh_map.pop(token, None)
            if associated_refresh is not None:
                # Refresh token outlives the access; clear only the
                # back-reference pointing to the now-dead access token.
                self._refresh_to_access_map.pop(associated_refresh, None)
            return None
        return token_obj

    # ----- bearer verification (three kinds) -----

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


_MCP_NOT_CONFIGURED_BANNER = (
    "\n"
    "============================================================\n"
    "MCP CONTAINER IS NOT CONFIGURED\n"
    "============================================================\n"
    "\n"
    "If you DON'T want the external AI connector (claude.ai Custom\n"
    "Connector, Claude Code, etc.), this container shouldn't be\n"
    "running at all. To stop the restart loop:\n"
    "\n"
    "  1. Open your docker-compose.yml\n"
    "  2. Find the 'ursa-oscar-mcp:' service block\n"
    "  3. Either DELETE it, or comment out every line in it\n"
    "  4. Run: docker compose up -d --remove-orphans\n"
    "\n"
    "The web container, api container, and watcher container do\n"
    "NOT need MCP to function. The in-app AI assistant on the web\n"
    "UI works without it. Most operators don't need this container.\n"
    "\n"
    "If you DO want the external AI connector, the setup guide is:\n"
    "  https://github.com/burrellka/URSA-OSCAR/blob/main/Docs/install/mcp-optional-addon.md\n"
    "\n"
    "Missing env var: {missing}\n"
    "============================================================\n"
    "\n"
)


def _require_static_bearer() -> str:
    token = os.environ.get("URSA_OSCAR_MCP_BEARER_TOKEN", "").strip()
    if not token:
        sys.stderr.write(_MCP_NOT_CONFIGURED_BANNER.format(missing="URSA_OSCAR_MCP_BEARER_TOKEN"))
        sys.exit(1)
    return token


def _require_oauth_client_credentials() -> tuple[str, str]:
    cid = os.environ.get("URSA_OSCAR_MCP_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        sys.stderr.write(_MCP_NOT_CONFIGURED_BANNER.format(
            missing="URSA_OSCAR_MCP_OAUTH_CLIENT_ID and URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET",
        ))
        sys.exit(1)
    return cid, csec


def build_auth_provider() -> UrsaOscarOAuthProvider:
    """Construct the OAuth provider from env. Exits fast on misconfiguration.

    1.1.8 — friendlier failure mode. When any required env var is
    missing, we print a clear banner that tells the operator whether
    they should configure the secrets OR just remove the MCP service
    from their compose file (the common case for analytics-only users).
    Prior to 1.1.8 the error message walked them through generating
    secrets, which is correct for operators who actually want the
    external AI connector but confusing for the bigger pool of users
    who don't want it. Most upgrade-from-1.1.5-or-earlier users carry
    forward a compose file that had MCP active by default; without the
    new banner they hit a confusing restart loop and reach for the
    forum to debug. The banner now puts the cheapest fix (comment it
    out) first.
    """
    base_url = os.environ.get("URSA_OSCAR_MCP_BASE_URL", "").rstrip("/")
    if not base_url:
        sys.stderr.write(_MCP_NOT_CONFIGURED_BANNER.format(missing="URSA_OSCAR_MCP_BASE_URL"))
        sys.exit(1)
    static_bearer = _require_static_bearer()
    pre_id, pre_secret = _require_oauth_client_credentials()
    # JWT secret is optional — when present, MCP accepts operator JWTs
    # as a third bearer kind. When absent, only OAuth + static bearer
    # work (backward-compat with 0.10.x).
    jwt_secret = resolve_jwt_secret()

    # 1.1.9 SECURITY — DCR is now off by default. See the module-level
    # DCR_ENABLED comment for the rationale. With DCR off:
    #   - /register returns 404
    #   - OAuth discovery does NOT advertise registration_endpoint
    #   - The pre-registered claude.ai client is the only registered
    #     client; its redirect allowlist (CLAUDE_AI_CALLBACK +
    #     EXTRA_REDIRECT_URIS) gates which callbacks can complete the
    #     flow even if client_secret leaks
    # Operators opt in via URSA_OSCAR_MCP_DCR=true (only safe behind
    # Cloudflare Access or equivalent edge auth).
    provider = UrsaOscarOAuthProvider(
        base_url=base_url,
        static_bearer_token=static_bearer,
        jwt_secret=jwt_secret,
        client_registration_options=ClientRegistrationOptions(
            enabled=DCR_ENABLED,
            valid_scopes=None,
            default_scopes=None,
        ),
    )

    # Pre-register the claude.ai client. Reconstructed from env vars
    # on every boot. 1.1.9 — the redirect_uris allowlist now includes
    # any extras the operator added via URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS
    # so non-claude.ai MCP clients (KAIROS, etc.) can connect via the
    # shared client_secret + their own callback, instead of needing
    # DCR to self-register.
    allowed_redirects: list[AnyUrl] = [AnyUrl(CLAUDE_AI_CALLBACK)]
    for u in EXTRA_REDIRECT_URIS:
        try:
            allowed_redirects.append(AnyUrl(u))
        except Exception as e:
            logger.warning(
                "URSA_OSCAR_MCP_EXTRA_REDIRECT_URIS: skipping invalid "
                "URL %r (%s)", u, e,
            )
    provider.clients[pre_id] = OAuthClientInformationFull(
        client_id=pre_id,
        client_secret=pre_secret,
        client_id_issued_at=int(time.time()),
        redirect_uris=allowed_redirects,
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=None,
    )
    provider.preregistered_client_id = pre_id
    logger.info(
        "Pre-registered claude.ai client client_id=%s allowed_redirects=%s "
        "(DCR=%s; %s)",
        pre_id,
        [str(u) for u in allowed_redirects],
        "ENABLED" if DCR_ENABLED else "DISABLED",
        "self-registration via /register is OPEN — only safe behind edge auth"
        if DCR_ENABLED else
        "only pre-registered client + allowlisted redirects can complete flow",
    )
    return provider
