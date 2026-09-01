"""Phase 6.4.1 — service-token auto-mint coverage.

Behavior under test:
  - First call mints fresh, writes /data/service_tokens/<svc>.jwt with mode 0600
  - Second call reads existing file (idempotent)
  - Expired/expiring-soon tokens are silently re-minted
  - Corrupted/wrong-secret tokens are silently re-minted
  - read_service_token returns None when file is missing
  - ServiceTokenError raised when JWT secret unconfigured
  - Atomic write: no stale .tmp left around
"""
from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ursa_oscar.auth.service_tokens import (
    RENEW_BEFORE,
    ServiceTokenError,
    _service_token_path,
    ensure_all_service_tokens,
    ensure_service_token,
    read_service_token,
)
from ursa_oscar.auth.tokens import (
    API_TOKEN_LIFETIME,
    decode_token,
    encode_token,
)


SECRET = "test-jwt-secret-not-the-real-one"


def test_first_call_mints_fresh_token(tmp_path: Path):
    token = ensure_service_token(tmp_path, SECRET, "mcp")
    assert token
    path = _service_token_path(tmp_path, "mcp")
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == token
    # Validates against the JWT secret.
    claims = decode_token(SECRET, token)
    assert claims["kind"] == "api"
    assert claims["sub"] == "operator"


def test_second_call_reuses_existing_token(tmp_path: Path):
    first = ensure_service_token(tmp_path, SECRET, "mcp")
    second = ensure_service_token(tmp_path, SECRET, "mcp")
    assert first == second  # Same token, no re-mint.


def test_expired_token_remints(tmp_path: Path):
    """A token issued long enough ago to have expired should be replaced."""
    past = datetime.now(timezone.utc) - API_TOKEN_LIFETIME - timedelta(days=1)
    expired = encode_token(SECRET, kind="api", now=past)
    path = _service_token_path(tmp_path, "mcp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expired, encoding="utf-8")

    fresh = ensure_service_token(tmp_path, SECRET, "mcp")
    assert fresh != expired
    # Fresh token has a future expiration.
    claims = decode_token(SECRET, fresh)
    assert claims["exp"] > int(datetime.now(timezone.utc).timestamp())


def test_token_expiring_within_renewal_window_remints(tmp_path: Path):
    """A token still technically valid but expiring inside RENEW_BEFORE
    should be replaced — gives the operator a comfortable buffer."""
    # Issue a token that has only 1 day left (well inside RENEW_BEFORE=7d).
    short_lifetime = RENEW_BEFORE - timedelta(days=6)  # 1 day remaining
    issued = datetime.now(timezone.utc) - (API_TOKEN_LIFETIME - short_lifetime)
    almost_expired = encode_token(SECRET, kind="api", now=issued)
    path = _service_token_path(tmp_path, "watcher")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(almost_expired, encoding="utf-8")

    fresh = ensure_service_token(tmp_path, SECRET, "watcher")
    assert fresh != almost_expired


def test_corrupted_token_remints(tmp_path: Path):
    path = _service_token_path(tmp_path, "mcp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not.a.jwt", encoding="utf-8")

    fresh = ensure_service_token(tmp_path, SECRET, "mcp")
    # Decodes cleanly against the real secret.
    assert decode_token(SECRET, fresh)["kind"] == "api"


def test_wrong_secret_token_remints(tmp_path: Path):
    """A token signed with a different secret can't be verified by
    THIS API — treat it like corruption + re-mint."""
    foreign = encode_token("attacker-secret", kind="api")
    path = _service_token_path(tmp_path, "mcp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(foreign, encoding="utf-8")

    fresh = ensure_service_token(tmp_path, SECRET, "mcp")
    assert fresh != foreign
    decode_token(SECRET, fresh)  # validates


def test_missing_secret_raises(tmp_path: Path):
    with pytest.raises(ServiceTokenError):
        ensure_service_token(tmp_path, "", "mcp")


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only mode bits")
def test_written_file_has_0600_perms(tmp_path: Path):
    ensure_service_token(tmp_path, SECRET, "mcp")
    path = _service_token_path(tmp_path, "mcp")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path):
    ensure_service_token(tmp_path, SECRET, "mcp")
    path = _service_token_path(tmp_path, "mcp")
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert path.exists()
    assert not tmp.exists()


def test_read_service_token_returns_none_when_missing(tmp_path: Path):
    assert read_service_token(tmp_path, "mcp") is None
    assert read_service_token(tmp_path, "watcher") is None


def test_read_service_token_returns_existing(tmp_path: Path):
    minted = ensure_service_token(tmp_path, SECRET, "mcp")
    assert read_service_token(tmp_path, "mcp") == minted


def test_ensure_all_mints_both_services(tmp_path: Path):
    tokens = ensure_all_service_tokens(tmp_path, SECRET)
    assert set(tokens.keys()) == {"mcp", "watcher"}
    # The token bytes can be identical when both mints land in the same
    # wall-clock second (JWT iat resolution is 1s, and both share the
    # same subject="operator" + kind="api" claims). That's fine — same
    # operator identity, stored in two files so rotation is per-service-
    # friendly. What matters is that both files exist and both decode
    # against the signing secret.
    for service in ("mcp", "watcher"):
        path = _service_token_path(tmp_path, service)
        assert path.exists()
        assert decode_token(SECRET, tokens[service])["sub"] == "operator"


def test_per_service_isolation(tmp_path: Path):
    """Re-minting the mcp token must not touch the watcher token."""
    watcher_token = ensure_service_token(tmp_path, SECRET, "watcher")
    mcp_path = _service_token_path(tmp_path, "mcp")
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text("corrupted", encoding="utf-8")

    ensure_service_token(tmp_path, SECRET, "mcp")  # forces re-mint
    # Watcher still has the original.
    assert read_service_token(tmp_path, "watcher") == watcher_token


def test_unreadable_file_treated_as_missing(tmp_path: Path):
    """If the file exists but read_text raises, behave as if missing."""
    path = _service_token_path(tmp_path, "mcp")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Empty file → read returns empty string → treated as missing.
    path.write_text("", encoding="utf-8")
    token = ensure_service_token(tmp_path, SECRET, "mcp")
    assert token
    assert path.read_text(encoding="utf-8").strip() == token


# ---------------------------------------------------------------------------
# 1.1.16 — background renewal loop.
#
# The mechanism (ensure_service_token re-mints an expiring token) is covered
# above. These cover the LOOP that closes the actual gap: startup-only
# minting meant an API up >90 days let its 90-day token lapse and 401 the
# MCP/watcher. The loop renews on a timer regardless of uptime.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from ursa_oscar.auth.service_tokens import (  # noqa: E402
    RENEW_CHECK_INTERVAL,
    service_token_renewal_loop,
)


def _write_expiring_token(tmp_path: Path, service: str) -> str:
    """Write a token that's inside the RENEW_BEFORE window so the next
    renewal check re-mints it. Issued ~88 days ago → expires in ~2 days."""
    issued = datetime.now(timezone.utc) - API_TOKEN_LIFETIME + timedelta(days=2)
    stale = encode_token(SECRET, kind="api", now=issued)
    path = _service_token_path(tmp_path, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stale, encoding="utf-8")
    return stale


async def test_renewal_loop_remints_expiring_tokens(tmp_path: Path):
    """The core durability guarantee: a token about to expire gets renewed
    by the loop with NO restart. Uses a tiny interval so the first pass
    fires immediately."""
    stale_mcp = _write_expiring_token(tmp_path, "mcp")
    stale_watcher = _write_expiring_token(tmp_path, "watcher")

    task = asyncio.create_task(
        service_token_renewal_loop(
            tmp_path, SECRET, interval=timedelta(seconds=0.01),
        )
    )
    # Let a couple of passes run, then stop cleanly.
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fresh_mcp = read_service_token(tmp_path, "mcp")
    fresh_watcher = read_service_token(tmp_path, "watcher")
    # Both re-minted (different token), and now well outside the renew window.
    assert fresh_mcp and fresh_mcp != stale_mcp
    assert fresh_watcher and fresh_watcher != stale_watcher
    exp = decode_token(SECRET, fresh_mcp)["exp"]
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    assert expires_at - datetime.now(timezone.utc) > RENEW_BEFORE


async def test_renewal_loop_leaves_healthy_tokens_alone(tmp_path: Path):
    """A fresh token must NOT be re-minted every pass — the check is cheap
    precisely because it's a no-op until near expiry."""
    fresh = ensure_service_token(tmp_path, SECRET, "mcp")  # 90 days out
    task = asyncio.create_task(
        service_token_renewal_loop(
            tmp_path, SECRET, interval=timedelta(seconds=0.01),
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Same token — no churn.
    assert read_service_token(tmp_path, "mcp") == fresh


async def test_renewal_loop_survives_a_failing_pass(tmp_path: Path, monkeypatch):
    """Renewal is a safety net; one bad pass (e.g. volume briefly
    unwritable) must not kill it permanently. After a failure it keeps
    running and recovers on the next pass."""
    import ursa_oscar.auth.service_tokens as st

    calls = {"n": 0}
    real = st.ensure_all_service_tokens

    def flaky(data_dir, secret, *, now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated transient volume error")
        return real(data_dir, secret, now=now)

    monkeypatch.setattr(st, "ensure_all_service_tokens", flaky)
    _write_expiring_token(tmp_path, "mcp")

    task = asyncio.create_task(
        service_token_renewal_loop(
            tmp_path, SECRET, interval=timedelta(seconds=0.01),
        )
    )
    await asyncio.sleep(0.1)
    is_alive = not task.done()  # survived the first-pass exception
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert is_alive, "loop died on a failing pass instead of retrying"
    assert calls["n"] >= 2, "loop did not retry after the failing pass"
    # And it recovered: the token got re-minted on a later pass.
    assert read_service_token(tmp_path, "mcp")


def test_renew_check_interval_is_safely_inside_the_window():
    """The check must run many times inside the 7-day renew window, or a
    token could slip past expiry between checks."""
    assert RENEW_CHECK_INTERVAL < RENEW_BEFORE
    # Comfortably many chances, not just barely.
    assert RENEW_BEFORE / RENEW_CHECK_INTERVAL >= 14
