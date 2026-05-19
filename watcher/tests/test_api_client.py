"""Phase 6.4 — ApiClient bearer-attaching regression.

These tests use respx to mock httpx so we can inspect the actual
outgoing headers without spinning up a real API. The watcher loop
tests use a FakeApi and never go through httpx; this file covers the
HTTP-layer behavior the watcher loop tests skip.

Coverage:
  - Token configured → Authorization: Bearer <token> on enqueue + get_job
  - Token unset      → no Authorization header
  - Token whitespace → treated as unset (defensive against pasted env)
  - Webhook calls    → DO NOT carry the bearer (external endpoint)
  - 401 from API     → caller (the watcher) sees HTTPStatusError so it
                       can log + retry on the next tick

Also covers WatcherConfig.from_env reading URSA_OSCAR_WATCHER_TOKEN.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from ursa_oscar_watcher.api_client import ApiClient
from ursa_oscar_watcher.config import WatcherConfig


# ---------------------------------------------------------------------------
# Bearer-header attachment
# ---------------------------------------------------------------------------


@respx.mock
def test_enqueue_attaches_bearer_when_token_set():
    route = respx.post("http://api.test/api/v1/imports").mock(
        return_value=httpx.Response(200, json={"id": 1, "status": "queued"}),
    )
    client = ApiClient("http://api.test", api_token="my-90d-jwt")
    client.enqueue_import("/cpap-import")

    assert route.called
    req = route.calls.last.request
    assert req.headers.get("Authorization") == "Bearer my-90d-jwt"


@respx.mock
def test_get_job_attaches_bearer_when_token_set():
    route = respx.get("http://api.test/api/v1/imports/jobs/42").mock(
        return_value=httpx.Response(200, json={"id": 42, "status": "completed"}),
    )
    client = ApiClient("http://api.test", api_token="my-90d-jwt")
    client.get_job(42)

    assert route.called
    assert route.calls.last.request.headers.get("Authorization") == "Bearer my-90d-jwt"


@respx.mock
def test_no_bearer_when_token_unset():
    route = respx.post("http://api.test/api/v1/imports").mock(
        return_value=httpx.Response(200, json={"id": 1, "status": "queued"}),
    )
    client = ApiClient("http://api.test", api_token=None)
    client.enqueue_import("/cpap-import")

    assert route.called
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_whitespace_token_treated_as_unset():
    """Defensive: pasted env values sometimes carry stray whitespace.
    Empty-after-strip should not produce ``Authorization: Bearer ``."""
    route = respx.post("http://api.test/api/v1/imports").mock(
        return_value=httpx.Response(200, json={"id": 1, "status": "queued"}),
    )
    client = ApiClient("http://api.test", api_token="   \t  ")
    assert client.api_token is None
    client.enqueue_import("/cpap-import")
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_webhook_does_not_carry_bearer():
    """Webhooks are operator-configured external URLs (Home Assistant,
    n8n, etc.). The internal API bearer must not be forwarded there."""
    route = respx.post("http://hook.example/notify").mock(
        return_value=httpx.Response(200),
    )
    client = ApiClient("http://api.test", api_token="my-90d-jwt")
    client.fire_webhook("http://hook.example/notify", {"event": "test"})

    assert route.called
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_401_from_api_raises_for_caller():
    """When the API rejects the bearer (expired, wrong secret, etc.),
    the watcher's retry loop must see an HTTPStatusError it can log
    and retry next tick."""
    respx.post("http://api.test/api/v1/imports").mock(
        return_value=httpx.Response(401, json={"detail": "Not authenticated"}),
    )
    client = ApiClient("http://api.test", api_token="stale-jwt")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        client.enqueue_import("/cpap-import")
    assert exc.value.response.status_code == 401


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


def test_config_reads_watcher_token_from_env(monkeypatch):
    monkeypatch.setenv("URSA_OSCAR_WATCHER_TOKEN", "env-supplied-jwt")
    monkeypatch.setenv("URSA_OSCAR_WATCH_PATH", "/tmp/watch")
    cfg = WatcherConfig.from_env()
    assert cfg.api_token == "env-supplied-jwt"


def test_config_token_missing_is_none(monkeypatch):
    monkeypatch.delenv("URSA_OSCAR_WATCHER_TOKEN", raising=False)
    monkeypatch.setenv("URSA_OSCAR_WATCH_PATH", "/tmp/watch")
    cfg = WatcherConfig.from_env()
    assert cfg.api_token is None


def test_config_token_whitespace_is_none(monkeypatch):
    monkeypatch.setenv("URSA_OSCAR_WATCHER_TOKEN", "  ")
    monkeypatch.setenv("URSA_OSCAR_WATCH_PATH", "/tmp/watch")
    cfg = WatcherConfig.from_env()
    assert cfg.api_token is None
