"""1.1.17 — OAuth token persistence across MCP restart.

The bug: InMemoryOAuthProvider holds all issued access + refresh tokens in
memory, so every container restart (every redeploy) wiped them. A client
still holding a pre-restart refresh token got
"invalid_grant: refresh token does not exist" and had to fully
re-authorize. UrsaOscarOAuthProvider now persists the token tables to
/data/mcp_oauth_tokens.json and restores them on boot.

These tests cover the load-time FILTERS, which are the security-sensitive
part — especially the 1.1.9 guarantee that tokens for a client that is no
longer loaded (a DCR client from the 1.1.5-1.1.8 open window, when DCR is
off) must NOT come back to life across a restart.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from mcp.server.auth.provider import AccessToken, RefreshToken
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull

from ursa_oscar_mcp.auth import UrsaOscarOAuthProvider

CLIENT_ID = "preregistered-client"


def _provider(token_store: Path) -> UrsaOscarOAuthProvider:
    p = UrsaOscarOAuthProvider(
        base_url="https://test.invalid",
        token_store_path=token_store,
        client_registration_options=ClientRegistrationOptions(
            enabled=False, valid_scopes=None, default_scopes=None,
        ),
    )
    # Simulate build_auth_provider wiring the pre-registered client.
    p.clients[CLIENT_ID] = OAuthClientInformationFull(
        client_id=CLIENT_ID,
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    p.preregistered_client_id = CLIENT_ID
    return p


def _seed_grant(p: UrsaOscarOAuthProvider, *, client_id=CLIENT_ID,
                access="acc1", refresh="ref1",
                access_exp=None, refresh_exp=None) -> None:
    """Put a minted access+refresh pair + maps into the provider, as
    exchange_authorization_code would."""
    p.access_tokens[access] = AccessToken(
        token=access, client_id=client_id, scopes=["read"],
        expires_at=access_exp if access_exp is not None else int(time.time() + 3600),
    )
    p.refresh_tokens[refresh] = RefreshToken(
        token=refresh, client_id=client_id, scopes=["read"],
        expires_at=refresh_exp,  # None = never expires (upstream default)
    )
    p._access_to_refresh_map[access] = refresh
    p._refresh_to_access_map[refresh] = access


def test_round_trip_survives_a_restart(tmp_path):
    """The core guarantee: a grant minted before a restart is present
    after it, maps intact — so a client's refresh token still works."""
    store = tmp_path / "tokens.json"
    p1 = _provider(store)
    _seed_grant(p1)
    p1._save_persisted_tokens()

    # A fresh provider == a container restart.
    p2 = _provider(store)
    p2._load_persisted_tokens()

    assert "acc1" in p2.access_tokens
    assert "ref1" in p2.refresh_tokens
    assert p2._access_to_refresh_map["acc1"] == "ref1"
    assert p2._refresh_to_access_map["ref1"] == "acc1"
    assert p2.refresh_tokens["ref1"].client_id == CLIENT_ID


def test_expired_access_token_dropped_but_refresh_kept(tmp_path):
    """Post-restart, an expired access token must not resurrect — but its
    refresh token must survive (that's the whole point; the client refreshes
    to get a new access token). Mirrors the 1.1.6 refresh-outlives-access
    contract."""
    store = tmp_path / "tokens.json"
    p1 = _provider(store)
    _seed_grant(p1, access_exp=int(time.time() - 10))  # already expired
    p1._save_persisted_tokens()

    p2 = _provider(store)
    p2._load_persisted_tokens()

    assert "acc1" not in p2.access_tokens          # expired -> dropped
    assert "ref1" in p2.refresh_tokens             # refresh outlives it
    # No dangling map entry for the dropped access token.
    assert "acc1" not in p2._access_to_refresh_map
    assert "ref1" not in p2._refresh_to_access_map
    # And the refresh token is still usable by the refresh flow, which
    # looks it up directly (not via the map).
    assert p2.refresh_tokens["ref1"].token == "ref1"


def test_expired_refresh_token_dropped(tmp_path):
    store = tmp_path / "tokens.json"
    p1 = _provider(store)
    _seed_grant(p1, refresh_exp=int(time.time() - 10))
    p1._save_persisted_tokens()

    p2 = _provider(store)
    p2._load_persisted_tokens()
    assert "ref1" not in p2.refresh_tokens


def test_tokens_for_unknown_client_are_dropped_1_1_9_guarantee(tmp_path):
    """THE security test. A token for a client that isn't loaded (e.g. a
    DCR client from the 1.1.5-1.1.8 open window, with DCR now off) must NOT
    come back across a restart. Persisting tokens must not become a
    backdoor around the 1.1.9 dead-client guarantee."""
    store = tmp_path / "tokens.json"
    p1 = _provider(store)
    _seed_grant(p1, client_id="ghost-dcr-client-from-open-window",
                access="ghost_acc", refresh="ghost_ref")
    # Also seed a legit pre-registered-client grant so we prove the filter
    # is selective, not all-or-nothing.
    _seed_grant(p1, access="acc1", refresh="ref1")
    p1._save_persisted_tokens()

    # New provider only knows the pre-registered client (DCR off).
    p2 = _provider(store)
    p2._load_persisted_tokens()

    assert "ghost_acc" not in p2.access_tokens
    assert "ghost_ref" not in p2.refresh_tokens
    assert "acc1" in p2.access_tokens      # legit client's grant survives
    assert "ref1" in p2.refresh_tokens


def test_corrupt_store_does_not_crash_boot(tmp_path):
    """A garbled token file must degrade to 'everyone re-authorizes',
    never take down the container on startup."""
    store = tmp_path / "tokens.json"
    store.write_text("{ this is not json", encoding="utf-8")
    p = _provider(store)
    p._load_persisted_tokens()  # must not raise
    assert p.access_tokens == {}
    assert p.refresh_tokens == {}


def test_missing_store_is_normal_first_run(tmp_path):
    p = _provider(tmp_path / "does-not-exist.json")
    p._load_persisted_tokens()  # no file -> silent, empty
    assert p.access_tokens == {}


def test_save_is_0600_and_atomic(tmp_path):
    """Bearer credentials on disk must be operator-only, and a crash
    mid-write must never leave a half-written store."""
    import os
    import stat as _stat
    store = tmp_path / "tokens.json"
    p = _provider(store)
    _seed_grant(p)
    p._save_persisted_tokens()

    assert store.exists()
    # No leftover tmp file.
    assert not (store.with_suffix(".tokens.tmp")).exists()
    # Round-trips as valid JSON with the three sections.
    data = json.loads(store.read_text(encoding="utf-8"))
    assert set(data) == {"access_tokens", "refresh_tokens", "access_to_refresh"}
    # 0600 where the platform supports it.
    if os.name == "posix":
        mode = _stat.S_IMODE(store.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_map_entry_dropped_when_one_end_missing(tmp_path):
    """A persisted map entry whose access OR refresh end didn't survive the
    filters must not be restored (no dangling pointers)."""
    store = tmp_path / "tokens.json"
    # Hand-craft a store: a map entry pointing at an access token that is
    # expired (so it won't load) but a refresh token that will.
    payload = {
        "access_tokens": {
            "acc_expired": {
                "token": "acc_expired", "client_id": CLIENT_ID,
                "scopes": ["read"], "expires_at": int(time.time() - 5),
                "resource": None,
            },
        },
        "refresh_tokens": {
            "ref_live": {
                "token": "ref_live", "client_id": CLIENT_ID,
                "scopes": ["read"], "expires_at": None,
            },
        },
        "access_to_refresh": {"acc_expired": "ref_live"},
    }
    store.write_text(json.dumps(payload), encoding="utf-8")

    p = _provider(store)
    p._load_persisted_tokens()

    assert "acc_expired" not in p.access_tokens
    assert "ref_live" in p.refresh_tokens
    # The map entry is gone (its access end didn't survive).
    assert "acc_expired" not in p._access_to_refresh_map
    assert "ref_live" not in p._refresh_to_access_map
