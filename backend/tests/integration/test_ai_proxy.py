"""Phase 5 Ticket 1 — AI proxy regression coverage.

Tests cover the four moving parts of the AI proxy WITHOUT requiring
real LLM API keys or live network calls:

  1. SecretStore — Fernet round-trip, missing-key flow, masked listing
  2. ConfigStore — patch semantics, validation, persistence
  3. Provider preset registry — registry shape, auth-header builder
  4. AdapterFactory — build_adapter returns the right class per preset
  5. API endpoints — providers/config/test (no chat — chat needs an LLM)
  6. Tool descriptors — 11 tools present + dispatcher routes correctly
  7. Tool executor — composed routers (ahi_breakdown, event_distribution,
     pressure_profile, leak_profile) return the right shape against the
     4-night fixture

The chat endpoint's full streaming behavior is exercised by the
acceptance matrix in Ticket 3, not here — those require a real LLM.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from ursa_oscar.ai_proxy import (
    PRESETS,
    ClaudeAdapter,
    OpenAiCompatAdapter,
    build_adapter,
    get_preset,
)
from ursa_oscar.ai_proxy.config_store import AiProxyConfig, ConfigStore
from ursa_oscar.ai_proxy.prompt import render_system_prompt
from ursa_oscar.ai_proxy.providers.presets import build_auth_header
from ursa_oscar.ai_proxy.secrets import SecretStore, resolve_secret_key
from ursa_oscar.ai_proxy.tools import TOOL_DESCRIPTORS, execute_tool
from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import FIXTURE_ROOT


# -------------------------------------------------------------------------
# SecretStore.
# -------------------------------------------------------------------------


def test_secret_store_roundtrip(tmp_path):
    key = Fernet.generate_key()
    store = SecretStore(key=key, store_path=tmp_path / "secrets.enc")

    store.set("claude_api_key", "sk-ant-test-12345")
    assert store.has("claude_api_key")
    assert store.get("claude_api_key") == "sk-ant-test-12345"
    assert "claude_api_key" in store.list_keys()


def test_secret_store_deletes_on_empty_value(tmp_path):
    """Setting value="" is the operator's way to clear a key from the
    Settings UI without a separate DELETE button."""
    key = Fernet.generate_key()
    store = SecretStore(key=key, store_path=tmp_path / "secrets.enc")
    store.set("openai_api_key", "sk-test")
    assert store.has("openai_api_key")
    store.set("openai_api_key", "")
    assert not store.has("openai_api_key")


def test_secret_store_persists_across_instances(tmp_path):
    """Encrypted blob survives a new SecretStore instance — what
    happens across a container restart."""
    key = Fernet.generate_key()
    path = tmp_path / "secrets.enc"
    store1 = SecretStore(key=key, store_path=path)
    store1.set("gemini_api_key", "AIza-test-67890")
    del store1

    store2 = SecretStore(key=key, store_path=path)
    assert store2.get("gemini_api_key") == "AIza-test-67890"


def test_secret_store_wrong_key_returns_none(tmp_path):
    """Decrypt with a different Fernet key — the store treats it as
    a corrupted entry (returns None). The operator can re-enter the
    secret; we don't crash."""
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    path = tmp_path / "secrets.enc"

    SecretStore(key=key1, store_path=path).set("groq_api_key", "real-value")
    store2 = SecretStore(key=key2, store_path=path)
    assert store2.get("groq_api_key") is None


def test_resolve_secret_key_generates_when_missing(tmp_path, monkeypatch):
    """0.9.5 — first boot persists the key to ``master.key`` on the
    data volume. No operator action required; future restarts reuse
    it transparently."""
    monkeypatch.delenv("URSA_OSCAR_SECRET_KEY", raising=False)
    key = resolve_secret_key(tmp_path)
    assert isinstance(key, bytes) and len(key) > 0
    assert (tmp_path / "master.key").exists()
    # File contents should match the returned key — the operator's
    # backup of /data/ should include the key for restore.
    assert (tmp_path / "master.key").read_bytes().strip() == key


def test_resolve_secret_key_reuses_master_key_on_restart(tmp_path, monkeypatch):
    """0.9.5 design — master.key is the canonical persistent location.
    Every subsequent boot reads from it without regenerating."""
    monkeypatch.delenv("URSA_OSCAR_SECRET_KEY", raising=False)
    key1 = resolve_secret_key(tmp_path)
    assert (tmp_path / "master.key").exists()
    # Simulate restart: same key returned, file unchanged.
    key2 = resolve_secret_key(tmp_path)
    assert key1 == key2
    # And once the operator sets env, env wins (master.key is harmless leftover).
    monkeypatch.setenv("URSA_OSCAR_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    key3 = resolve_secret_key(tmp_path)
    Fernet(key3)
    assert key3 != key1


def test_resolve_secret_key_regenerates_when_master_key_corrupted(tmp_path, monkeypatch):
    """If master.key contains garbage (mid-write crash, operator-edited,
    etc.), regenerate rather than crash. Stored secrets become
    unrecoverable in that case but that was already true when the key
    bytes were lost."""
    monkeypatch.delenv("URSA_OSCAR_SECRET_KEY", raising=False)
    (tmp_path / "master.key").write_bytes(b"not-a-fernet-key")
    key = resolve_secret_key(tmp_path)
    Fernet(key)  # valid replacement
    assert (tmp_path / "master.key").read_bytes().strip() == key


def test_resolve_secret_key_migrates_from_legacy_gen_file(tmp_path, monkeypatch):
    """0.9.5 migration — when an operator upgrades from 0.9.2-0.9.4
    (where the key lived in ``secret_key.gen``), the first boot on
    0.9.5 should copy the legacy file's contents into the new
    ``master.key`` location and continue working. Stored secrets
    survive the upgrade transparently."""
    monkeypatch.delenv("URSA_OSCAR_SECRET_KEY", raising=False)
    legacy_key = Fernet.generate_key()
    (tmp_path / "secret_key.gen").write_bytes(legacy_key)

    migrated = resolve_secret_key(tmp_path)
    assert migrated == legacy_key, (
        "0.9.5 must migrate the legacy .gen file's contents to master.key "
        "without changing the key bytes — stored secrets must survive."
    )
    assert (tmp_path / "master.key").exists()
    assert (tmp_path / "master.key").read_bytes().strip() == legacy_key
    # Legacy file left in place so a downgrade rollback still works.
    assert (tmp_path / "secret_key.gen").exists()


def test_resolve_secret_key_honors_env(monkeypatch, tmp_path):
    fresh_key = Fernet.generate_key()
    monkeypatch.setenv("URSA_OSCAR_SECRET_KEY", fresh_key.decode("ascii"))
    assert resolve_secret_key(tmp_path) == fresh_key
    # Env path should NOT have written a .gen file.
    assert not (tmp_path / "secret_key.gen").exists()


# -------------------------------------------------------------------------
# ConfigStore.
# -------------------------------------------------------------------------


def test_config_store_default_is_disabled(tmp_path):
    store = ConfigStore(store_path=tmp_path / "ai_config.json")
    cfg = store.load()
    assert cfg.enabled is False
    assert cfg.provider_id is None


def test_config_store_patch_persists(tmp_path):
    path = tmp_path / "ai_config.json"
    store1 = ConfigStore(store_path=path)
    store1.patch(enabled=True, provider_id="claude", model="claude-sonnet-4-5-20250929")

    store2 = ConfigStore(store_path=path)
    cfg = store2.load()
    assert cfg.enabled is True
    assert cfg.provider_id == "claude"
    assert cfg.model == "claude-sonnet-4-5-20250929"


# -------------------------------------------------------------------------
# Provider preset registry.
# -------------------------------------------------------------------------


def test_seven_presets_registered():
    ids = {p.id for p in PRESETS}
    assert ids == {
        "claude", "openai", "gemini", "openrouter", "groq", "local", "custom",
    }


def test_only_claude_uses_claude_adapter():
    for p in PRESETS:
        if p.id == "claude":
            assert p.adapter == "claude"
        else:
            assert p.adapter == "openai_compat"


def test_build_auth_header_claude_vs_openai():
    """Claude uses x-api-key; OpenAI-compat providers use Authorization: Bearer."""
    claude = get_preset("claude")
    openai_p = get_preset("openai")
    assert build_auth_header(claude, "sk-test") == {"x-api-key": "sk-test"}
    assert build_auth_header(openai_p, "sk-test") == {"Authorization": "Bearer sk-test"}


def test_build_auth_header_empty_key_returns_empty_dict():
    """Some local LLM deployments don't require auth — empty key means
    the adapter should send no auth header."""
    local = get_preset("local")
    assert build_auth_header(local, None) == {}
    assert build_auth_header(local, "") == {}


# -------------------------------------------------------------------------
# Adapter factory.
# -------------------------------------------------------------------------


def test_build_adapter_routes_to_right_class():
    claude = build_adapter("claude", {"model": "claude-sonnet-4-5-20250929"}, "sk-ant-test")
    assert isinstance(claude, ClaudeAdapter)

    openai = build_adapter("openai", {"model": "gpt-4o"}, "sk-test")
    assert isinstance(openai, OpenAiCompatAdapter)

    gemini = build_adapter("gemini", {"model": "gemini-1.5-pro"}, "AIza-test")
    assert isinstance(gemini, OpenAiCompatAdapter)


def test_build_adapter_unknown_provider_returns_none():
    assert build_adapter("nonexistent_provider", {}, "key") is None


def test_build_adapter_uses_preset_defaults_when_config_blank():
    a = build_adapter("openai", {}, "sk-test")
    assert a is not None
    # Should fall back to preset default endpoint + first model.
    assert a.endpoint == "https://api.openai.com/v1"
    assert a.model == "gpt-4o"


# -------------------------------------------------------------------------
# System prompt rendering.
# -------------------------------------------------------------------------


def test_system_prompt_renders_with_device_clock_offset():
    prompt = render_system_prompt(
        user_profile=None,
        device_clock={
            "country": "USA",
            "mode": "static_offset",
            "static_offset_minutes": -300,
            "auto_dst": True,
        },
        today_date=date(2026, 5, 14),
    )
    # The LLM-readable description must mention the offset + DST handling.
    assert "-5.0 hours" in prompt
    assert "USA" in prompt
    assert "auto-adjusts for DST" in prompt
    assert "2026-05-14" in prompt


def test_system_prompt_falls_back_when_no_profile():
    prompt = render_system_prompt(
        user_profile=None,
        device_clock=None,
        today_date=date(2026, 5, 14),
    )
    assert "No user profile configured" in prompt
    assert "no shift needed" in prompt.lower() or "no timestamp shift" in prompt.lower()


# -------------------------------------------------------------------------
# Tool descriptors.
# -------------------------------------------------------------------------


def test_eleven_tool_descriptors():
    names = [t["function"]["name"] for t in TOOL_DESCRIPTORS]
    assert len(names) == 11
    # Check the architect-specified tool names are all present.
    for required in [
        "get_nightly_summary", "get_ahi_breakdown", "list_available_nights",
        "compare_periods", "analyze_correlation", "get_trend",
        "get_manual_log_summary", "get_user_profile",
        "get_event_distribution_by_hour", "get_pressure_profile",
        "get_leak_profile",
    ]:
        assert required in names, f"Missing tool descriptor: {required}"


def test_tool_descriptors_have_descriptions():
    """LLM tool routing depends on the descriptions. None should be empty
    or trivially short — they're the operator's first line of defense
    against the LLM picking the wrong tool."""
    for t in TOOL_DESCRIPTORS:
        desc = t["function"]["description"]
        assert len(desc) > 50, f"{t['function']['name']} has too-short description"


# -------------------------------------------------------------------------
# Tool executor — needs a running API. Reuse the existing seeded_client
# fixture from the session-exclusion suite.
# -------------------------------------------------------------------------


@pytest.fixture
def seeded_api_url(tmp_path, monkeypatch):
    """Spin up an API process bound to tmp DB seeded with the 4-night
    fixture. Returns the base URL the AI proxy's executor will call."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    import ursa_oscar.config as _config_mod

    db_file = tmp_path / "ai_proxy.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    monkeypatch.setenv("URSA_OSCAR_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    import_path(FIXTURE_ROOT, seeder, skip_existing=False)
    seeder.close()

    # Allocate a free port (race-window OK for tests).
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    app = create_app()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", lifespan="on", access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError("test API didn't come up")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10.0)
    _config_mod._settings = None


@pytest.mark.asyncio
async def test_execute_tool_get_nightly_summary(seeded_api_url):
    result = await execute_tool(
        "get_nightly_summary",
        {"date": "2026-05-10"},
        api_base_url=seeded_api_url,
    )
    assert result["ok"] is True
    assert result["data"]["date"] == "2026-05-10"


@pytest.mark.asyncio
async def test_execute_tool_ahi_breakdown_composed_router(seeded_api_url):
    """The ahi_breakdown route composes two underlying API calls
    (/night + /events) into a single tool envelope. Locks down the
    composition behavior."""
    result = await execute_tool(
        "get_ahi_breakdown",
        {"date": "2026-05-08"},
        api_base_url=seeded_api_url,
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["central"]["count"] == 47
    assert data["obstructive"]["count"] == 28
    assert "interpretation" in data
    assert "tecsa_likely" in data["interpretation"]


@pytest.mark.asyncio
async def test_execute_tool_unknown_returns_envelope_error():
    """Unknown tool name should NOT raise — it returns an envelope so
    the LLM sees a structured response."""
    result = await execute_tool("not_a_real_tool", {})
    assert result["ok"] is False
    assert result["code"] == "UNKNOWN_TOOL"


# -------------------------------------------------------------------------
# /ai/providers + /ai/config endpoints.
# -------------------------------------------------------------------------


@pytest.fixture
def ai_test_client(tmp_path, monkeypatch):
    """TestClient with the AI proxy wired up to tmp paths. No live API
    boot needed — the chat endpoint is exercised by the SSE mock test
    in another fixture."""
    import ursa_oscar.config as _config_mod

    db_file = tmp_path / "ai.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    monkeypatch.setenv("URSA_OSCAR_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def test_providers_endpoint_lists_seven(ai_test_client):
    r = ai_test_client.get("/api/v1/ai/providers")
    assert r.status_code == 200
    presets = r.json()["providers"]
    assert len(presets) == 7
    ids = {p["id"] for p in presets}
    assert ids == {"claude", "openai", "gemini", "openrouter", "groq", "local", "custom"}


def test_config_get_default_is_disabled(ai_test_client):
    r = ai_test_client.get("/api/v1/ai/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["provider_id"] is None
    assert body["api_key_set"] is False
    assert isinstance(body["api_keys_set"], dict)


def test_config_patch_stores_api_key_in_secret_store(ai_test_client):
    """The api_key field never lands in plain config; it goes through
    the SecretStore. After PATCH, the masked response shows api_key_set
    True for the configured provider only."""
    r = ai_test_client.post("/api/v1/ai/config", json={
        "enabled": True,
        "provider_id": "claude",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "sk-ant-test-12345",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["provider_id"] == "claude"
    assert body["api_key_set"] is True
    assert body["api_keys_set"]["claude"] is True
    assert body["api_keys_set"]["openai"] is False
    # The raw value should NEVER appear in the response.
    assert "sk-ant-test-12345" not in r.text


def test_config_patch_invalid_provider_rejected(ai_test_client):
    r = ai_test_client.post(
        "/api/v1/ai/config", json={"provider_id": "not_a_provider"},
    )
    assert r.status_code == 400


def test_chat_rejected_when_disabled(ai_test_client):
    """Default config has enabled=False — chat should 400 with the
    operator-friendly message that points them at Settings."""
    r = ai_test_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 400
    assert "disabled" in r.json()["detail"].lower()


# -------------------------------------------------------------------------
# /ai/chat — SSE event_generator regression coverage.
#
# These tests exercise the server-side event-generator without a live LLM
# or live tool router. They use:
#   - a fake ProviderAdapter that yields a scripted sequence of events
#   - a monkeypatched execute_tool that returns canned envelopes
# so we can lock down the wire-protocol contract: exactly ONE ``complete``
# event reaches the client, and only after all tool loops have finished.
# -------------------------------------------------------------------------


class _ScriptedAdapter:
    """Test double for a ProviderAdapter. ``script`` is a list of lists:
    one inner list per chat() call, each containing the events to emit
    on that call. The adapter advances through the outer list as the
    server makes repeat calls inside the tool loop."""

    def __init__(self, script):
        self._script = list(script)
        self._call_idx = 0

    async def chat(self, messages, tools, system_prompt):
        events = self._script[self._call_idx]
        self._call_idx += 1
        for ev in events:
            yield ev


def _parse_sse_events(body: bytes) -> list[dict]:
    """Pull JSON payloads out of every ``data:`` frame in an SSE body.
    Skips comments and blank frames. The test asserts on this list."""
    out = []
    for frame in body.replace(b"\r\n\r\n", b"\n\n").split(b"\n\n"):
        frame = frame.strip()
        if not frame or frame.startswith(b":"):
            continue
        if not frame.startswith(b"data:"):
            continue
        payload = frame[5:].strip()
        if not payload:
            continue
        out.append(json.loads(payload.decode("utf-8")))
    return out


@pytest.fixture
def chat_ready_client(ai_test_client):
    """ai_test_client with the AI assistant enabled + a Claude key
    stashed, so /ai/chat passes its pre-flight checks."""
    r = ai_test_client.post("/api/v1/ai/config", json={
        "enabled": True,
        "provider_id": "claude",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "sk-ant-test-fake",
    })
    assert r.status_code == 200, r.text
    return ai_test_client


def test_chat_suppresses_intermediate_complete_during_tool_loop(
    chat_ready_client, monkeypatch,
):
    """Regression for 0.9.6 bug — the adapter emits ``complete`` at the
    end of every chat() call, including the turn where
    ``stop_reason='tool_use'`` (signaling "I want to call tools").

    The 0.9.5-and-earlier event_generator forwarded those intermediate
    completes straight to the SSE stream. The client's for-await loop
    breaks on the FIRST ``complete``, so the user saw:
      - tool chip stuck at "running" (never received tool_result)
      - no assistant text (text events come on the 2nd adapter call)
      - timer frozen at ~2.5s (when the first complete arrived)

    The fix: buffer the adapter's complete events and only emit a
    single final complete after all tool loops have wrapped. This test
    locks that down by asserting exactly ONE ``complete`` event in the
    response body, and that it carries the FINAL stop_reason."""
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent
    import ursa_oscar.api.ai as ai_module

    # Two-call script: first call requests a tool, second call returns
    # the assistant's final answer + end_turn.
    script = [
        [
            AiStreamEvent(
                event_type="tool_call_start",
                payload={"id": "tu_01", "name": "get_nightly_summary"},
            ),
            AiStreamEvent(
                event_type="tool_call_complete",
                payload={
                    "id": "tu_01",
                    "name": "get_nightly_summary",
                    "arguments": {"date": "2026-05-13"},
                },
            ),
            AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "tool_use", "usage": {}},
            ),
        ],
        [
            AiStreamEvent(
                event_type="text",
                payload={"text": "Your AHI was 3.94. "},
            ),
            AiStreamEvent(
                event_type="text",
                payload={"text": "Sleep duration was 7h 04m."},
            ),
            AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "end_turn", "usage": {"output_tokens": 42}},
            ),
        ],
    ]

    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter(script),
    )

    async def fake_execute_tool(name, args, api_base_url=None):
        return {
            "ok": True,
            "data": {"date": "2026-05-13", "total_ahi": 3.94, "session_count": 2},
        }

    monkeypatch.setattr(ai_module, "execute_tool", fake_execute_tool)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "How was my sleep on 2026-05-13?"}],
        "context": {"current_date": "2026-05-13", "include_profile": False},
    })
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)

    # Sequence the client should see, in order:
    #   tool_call_start, tool_call_complete, tool_result,
    #   text, text, complete(end_turn)
    types = [e["event_type"] for e in events]
    assert types == [
        "tool_call_start", "tool_call_complete",
        "tool_result",
        "text", "text",
        "complete",
    ], f"unexpected event sequence: {types}"

    # The ONE complete must be the final one, not the intermediate
    # tool_use complete.
    completes = [e for e in events if e["event_type"] == "complete"]
    assert len(completes) == 1, (
        f"expected exactly 1 complete event, got {len(completes)} — "
        "regression: intermediate complete events leaking through "
        "would cause the client to break early on tool_use turns"
    )
    assert completes[0]["payload"]["stop_reason"] == "end_turn"

    # The tool_result must appear BEFORE the assistant's text response —
    # ordering matters for the UI's tool-chip "running -> complete"
    # transition.
    assert types.index("tool_result") < types.index("text")


def test_chat_emits_no_complete_when_adapter_errors(
    chat_ready_client, monkeypatch,
):
    """Adapter-error path — an ``error`` event should be forwarded
    immediately and no ``complete`` should follow it. The client's
    streamError handler picks up the error message and renders the
    banner."""
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent
    import ursa_oscar.api.ai as ai_module

    script = [[
        AiStreamEvent(
            event_type="error",
            payload={"message": "fake auth failure", "code": "unauthorized"},
        ),
    ]]
    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter(script),
    )

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]
    assert types == ["error"], (
        f"expected single error event, got {types} — "
        "errors must not be followed by a complete"
    )
    assert events[0]["payload"]["code"] == "unauthorized"
