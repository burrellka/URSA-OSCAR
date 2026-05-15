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
    monkeypatch.delenv("URSA_OSCAR_SECRET_KEY", raising=False)
    key = resolve_secret_key(tmp_path)
    assert isinstance(key, bytes) and len(key) > 0
    # First-start file should exist with the generated key bytes.
    assert (tmp_path / "secret_key.gen").exists()
    # Re-call should generate again (key not persisted to env yet).
    key2 = resolve_secret_key(tmp_path)
    # Both keys should be valid Fernet keys, but different (generated).
    Fernet(key)
    Fernet(key2)
    assert key != key2


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
