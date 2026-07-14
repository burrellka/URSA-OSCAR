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
    AiMessage,
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
from tests.conftest import FIXTURE_ROOT, bypass_auth


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
# 1.1.14 — empty-answer trap: max_tokens + include_usage.
# -------------------------------------------------------------------------


def test_effective_max_tokens_local_gets_generous_default():
    """Local family MUST get a concrete generous cap — the reasoning-
    starve fix. Cloud families get None so the provider's own (large)
    default applies and long cloud answers aren't truncated."""
    from ursa_oscar.ai_proxy import (
        DEFAULT_MAX_TOKENS_LOCAL,
        _effective_max_tokens,
    )

    assert _effective_max_tokens("local", None) == DEFAULT_MAX_TOKENS_LOCAL
    assert DEFAULT_MAX_TOKENS_LOCAL >= 3000  # headroom for reasoning + answer
    # Cloud families omit the cap (None) rather than imposing a small one.
    assert _effective_max_tokens("openai", None) is None
    assert _effective_max_tokens("gemini", None) is None
    assert _effective_max_tokens("claude", None) is None
    # Operator override wins for ANY provider.
    assert _effective_max_tokens("local", 1200) == 1200
    assert _effective_max_tokens("openai", 8000) == 8000


def test_build_adapter_sets_max_output_tokens_by_family():
    """build_adapter resolves the family default onto the adapter so the
    request builder can read self.max_output_tokens."""
    local = build_adapter("local", {"model": "gemma-4"}, None)
    assert local is not None
    assert local.max_output_tokens == 4000

    cloud = build_adapter("openai", {"model": "gpt-4o"}, "sk-test")
    assert cloud is not None
    assert cloud.max_output_tokens is None

    override = build_adapter(
        "local", {"model": "gemma-4", "max_output_tokens": 2000}, None,
    )
    assert override is not None
    assert override.max_output_tokens == 2000


def test_openai_compat_body_requests_usage_and_omits_uncapped_max_tokens():
    """The streamed body always asks for usage (so the per-turn token
    line can populate on LocalAI), and only carries max_tokens when a
    cap is set — a cloud adapter with max_output_tokens=None must NOT
    send max_tokens (that would truncate long cloud answers)."""
    cloud = OpenAiCompatAdapter(
        api_key="sk-test", endpoint="https://api.openai.com/v1",
        model="gpt-4o", max_output_tokens=None,
    )
    body = cloud._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=[], system_prompt="sys",
    )
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert "max_tokens" not in body


def test_openai_compat_body_sends_max_tokens_when_capped():
    """A local adapter carries the resolved cap as max_tokens — the
    primary empty-answer-trap fix (a local server otherwise applies its
    own possibly-tiny default and the reasoning channel starves the
    answer)."""
    local = OpenAiCompatAdapter(
        api_key=None, endpoint="http://localai:8080/v1",
        model="gemma-4", max_output_tokens=4000,
    )
    body = local._build_request(
        messages=[AiMessage(role="user", content="how did I sleep")],
        tools=[], system_prompt="sys",
    )
    assert body["max_tokens"] == 4000
    assert body["stream_options"] == {"include_usage": True}


def test_config_store_max_output_tokens_range_guarded(tmp_path):
    """The operator knob is range-guarded like timeout_seconds — a value
    below the floor or above the ceiling is rejected at the schema."""
    import pytest
    from pydantic import ValidationError
    from ursa_oscar.ai_proxy.config_store import AiProxyConfig

    # In-range values accepted.
    assert AiProxyConfig(max_output_tokens=4000).max_output_tokens == 4000
    assert AiProxyConfig(max_output_tokens=256).max_output_tokens == 256
    # None (use family default) accepted.
    assert AiProxyConfig(max_output_tokens=None).max_output_tokens is None
    # Out-of-range rejected.
    with pytest.raises(ValidationError):
        AiProxyConfig(max_output_tokens=10)
    with pytest.raises(ValidationError):
        AiProxyConfig(max_output_tokens=100_000)


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


def test_tool_descriptors_count_and_membership():
    """11 Phase 5 tools + 2 from 6.1 + 1 from 6.2 + 1 from 6.3 + 1.1.12's
    load_tools discovery tool = 16."""
    names = [t["function"]["name"] for t in TOOL_DESCRIPTORS]
    assert len(names) == 16
    for required in [
        "get_nightly_summary", "get_ahi_breakdown", "list_available_nights",
        "compare_periods", "analyze_correlation", "get_trend",
        "get_manual_log_summary", "get_user_profile",
        "get_event_distribution_by_hour", "get_pressure_profile",
        "get_leak_profile",
        # Phase 6 Ticket 6.1:
        "analyze_multivariate_correlation",   # Item 2
        "analyze_lag_correlation",             # Item 3
        # Phase 6 Ticket 6.2:
        "analyze_prediction",
        # Phase 6 Ticket 6.3:
        "generate_report",
        # 1.1.12 — progressive tool disclosure discovery tool.
        "load_tools",
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
# 1.1.12 — Progressive tool disclosure metadata + accessors.
# -------------------------------------------------------------------------


def test_progressive_disclosure_core_set_is_small():
    """1.1.12 — Guard against future PRs quietly promoting tools into the
    core set. Core rides the LLM catalog on every turn; the fixed per-turn
    tool tax scales linearly with |core|. Two tools today
    (``get_nightly_summary`` + ``get_user_profile``) is the intentional
    ceiling. Raising this cap is a real product decision that should
    require touching the test on purpose."""
    from ursa_oscar.ai_proxy.tools import core_descriptors, TOOL_META
    core = core_descriptors()
    assert len(core) <= 4, (
        f"Core tool set unexpectedly large: {len(core)} tools. "
        "Each core tool is a fixed per-turn token tax. Promoting a tool "
        "into core is a product decision; if this is intentional, raise "
        "the ceiling here and update arch-ai-context.md's token math."
    )
    core_names = [
        (d["function"]["name"]) for d in core
    ]
    # 1.1.12 slice 2 — load_tools is core so the model can always
    # activate deferred groups. get_nightly_summary + get_user_profile
    # remain core because they ground every conversation.
    assert "get_nightly_summary" in core_names
    assert "get_user_profile" in core_names
    assert "load_tools" in core_names
    for name in core_names:
        assert TOOL_META[name]["core"] is True


def test_progressive_disclosure_every_tool_is_tagged():
    """1.1.12 — Every entry in TOOL_DESCRIPTORS must have a matching
    TOOL_META row. Adding a tool without tagging it means the chat loop
    (once slice 2 lands) won't know whether to ship it on every turn or
    hold it behind the index."""
    from ursa_oscar.ai_proxy.tools import TOOL_META
    for d in TOOL_DESCRIPTORS:
        name = d["function"]["name"]
        assert name in TOOL_META, (
            f"Tool {name!r} is missing from TOOL_META. Add a row with "
            "`core: bool` and `group: str | None` — see the block at the "
            "top of ai_proxy/tools.py."
        )


def test_progressive_disclosure_groups_partition_deferred():
    """1.1.12 — Every deferred tool must land in exactly one group; the
    ``descriptors_by_group`` accessor must return the same tool exactly
    once across all groups. Catches typos where a tool's ``group`` doesn't
    match a GROUP_LABELS entry (would silently drop it into ``misc``)."""
    from ursa_oscar.ai_proxy.tools import (
        deferred_descriptors, descriptors_by_group, GROUP_LABELS,
    )
    deferred = deferred_descriptors()
    grouped = descriptors_by_group()
    all_grouped_names = [
        d["function"]["name"] for tools in grouped.values() for d in tools
    ]
    # Every deferred tool appears somewhere.
    assert set(all_grouped_names) == {
        d["function"]["name"] for d in deferred
    }
    # No dupes across groups.
    assert len(all_grouped_names) == len(set(all_grouped_names))
    # Every non-empty group key must exist in GROUP_LABELS (no drift to
    # 'misc' for a legitimate group).
    for key in grouped:
        assert key in GROUP_LABELS, (
            f"Group {key!r} isn't in GROUP_LABELS. Either add a label "
            "there or fix the group name in TOOL_META."
        )


def test_progressive_disclosure_backward_compat_accessor():
    """1.1.12 slice 1 must not change existing chat behavior. The
    ``all_descriptors()`` accessor is the compat seam: it returns exactly
    the same list callers currently get from ``TOOL_DESCRIPTORS``."""
    from ursa_oscar.ai_proxy.tools import all_descriptors
    assert all_descriptors() == list(TOOL_DESCRIPTORS)


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
    bypass_auth(app)  # Phase 6.4 — live uvicorn server bypasses auth
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
    bypass_auth(app)  # Phase 6.4
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


# -------------------------------------------------------------------------
# /ai/system-prompt/template — 0.9.10. File-backed editable template.
# -------------------------------------------------------------------------


def test_template_store_first_read_returns_default(tmp_path):
    """A fresh TemplateStore with no file on disk returns the in-code
    DEFAULT_TEMPLATE with source='default'."""
    from ursa_oscar.ai_proxy.prompt import DEFAULT_TEMPLATE, TemplateStore
    store = TemplateStore(tmp_path / "system_prompt_template.txt")
    text, source = store.get_template()
    assert source == "default"
    assert text == DEFAULT_TEMPLATE
    assert not store.path.exists(), "GET shouldn't create the file"


def test_template_store_set_then_get_returns_file(tmp_path):
    """After set_template(), get_template() returns the stored content
    with source='file'."""
    from ursa_oscar.ai_proxy.prompt import TemplateStore
    store = TemplateStore(tmp_path / "system_prompt_template.txt")
    store.set_template("custom template body\nwith newlines\n")
    text, source = store.get_template()
    assert source == "file"
    assert text == "custom template body\nwith newlines\n"
    # File written + cleaned up the .tmp sibling from the atomic write.
    assert store.path.exists()
    assert not store.path.with_suffix(".txt.tmp").exists()


def test_template_store_reset_drops_file(tmp_path):
    """reset() removes the file so future reads return the default."""
    from ursa_oscar.ai_proxy.prompt import DEFAULT_TEMPLATE, TemplateStore
    store = TemplateStore(tmp_path / "system_prompt_template.txt")
    store.set_template("temp content")
    assert store.path.exists()
    store.reset()
    assert not store.path.exists()
    text, source = store.get_template()
    assert source == "default" and text == DEFAULT_TEMPLATE


def test_template_endpoint_get_returns_default_on_fresh_install(ai_test_client):
    """GET /api/v1/ai/system-prompt/template on a fresh install (no
    file on disk yet) returns the DEFAULT_TEMPLATE with source='default'.
    The Settings UI uses 'source' to render a 'Using built-in default' badge."""
    from ursa_oscar.ai_proxy.prompt import DEFAULT_TEMPLATE
    r = ai_test_client.get("/api/v1/ai/system-prompt/template")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "default"
    assert body["template"] == DEFAULT_TEMPLATE


def test_template_endpoint_delete_resets_to_default(ai_test_client):
    """0.11.1 — DELETE drops the saved file and returns DEFAULT_TEMPLATE
    with source='default'. The UX path is: operator forked from default
    in an old image, new image ships richer template, operator clicks
    'Reset to factory default' to adopt the upstream content."""
    from ursa_oscar.ai_proxy.prompt import DEFAULT_TEMPLATE

    # 1. Save a custom template (puts the file on disk).
    custom = "Operator's custom template\nwith a marker line\n"
    r = ai_test_client.put(
        "/api/v1/ai/system-prompt/template",
        json={"template": custom},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "file"

    # 2. DELETE → returns DEFAULT_TEMPLATE with source='default'.
    r2 = ai_test_client.delete("/api/v1/ai/system-prompt/template")
    assert r2.status_code == 200
    body = r2.json()
    assert body["source"] == "default"
    assert body["template"] == DEFAULT_TEMPLATE
    assert "with a marker line" not in body["template"]

    # 3. Subsequent GETs also see source='default' (file is really gone).
    r3 = ai_test_client.get("/api/v1/ai/system-prompt/template")
    assert r3.status_code == 200
    assert r3.json()["source"] == "default"


def test_template_endpoint_delete_is_idempotent(ai_test_client):
    """DELETE on a fresh install (no file ever saved) succeeds and
    returns DEFAULT_TEMPLATE. Idempotent so the UI button can be
    clicked twice without surfacing an error."""
    from ursa_oscar.ai_proxy.prompt import DEFAULT_TEMPLATE
    r = ai_test_client.delete("/api/v1/ai/system-prompt/template")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "default"
    assert body["template"] == DEFAULT_TEMPLATE


def test_template_endpoint_put_persists_then_get_returns_file(ai_test_client):
    """PUT writes the file; the next GET returns the new content with
    source='file'. This is the round-trip the Settings UI does when the
    operator clicks 'Save to template'."""
    new_text = "Operator's edited template.\n\nUser context: {user_profile_summary}\n"
    r = ai_test_client.put(
        "/api/v1/ai/system-prompt/template",
        json={"template": new_text},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "file"
    assert body["template"] == new_text

    # GET after PUT returns the same content from the file.
    r2 = ai_test_client.get("/api/v1/ai/system-prompt/template")
    assert r2.status_code == 200
    assert r2.json()["template"] == new_text
    assert r2.json()["source"] == "file"


def test_chat_uses_template_store_when_no_per_provider_override(
    ai_test_client, monkeypatch,
):
    """0.9.10 runtime-wiring regression — when cfg.custom_system_prompt
    is unset, the chat endpoint must read its system prompt from the
    TemplateStore. The 'Save to template' operator action affects every
    subsequent chat session that doesn't have a per-provider override."""
    # Operator wrote a distinctive marker into the template file.
    sentinel = "OPERATOR-EDITED-TEMPLATE-MARKER-12345"
    r = ai_test_client.put(
        "/api/v1/ai/system-prompt/template",
        json={"template": sentinel + "\n{user_profile_summary}\n"},
    )
    assert r.status_code == 200

    # Enable AI with a Claude provider but no custom_system_prompt override.
    r = ai_test_client.post("/api/v1/ai/config", json={
        "enabled": True,
        "provider_id": "claude",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "sk-ant-test-fake",
        "custom_system_prompt": "",  # explicitly empty -> use template store
    })
    assert r.status_code == 200, r.text

    # Hijack the adapter to capture the system_prompt argument it
    # receives, so we can assert the runtime resolved it from the
    # template store.
    import ursa_oscar.api.ai as ai_module
    captured: dict = {}

    class _CapturingAdapter:
        async def chat(self, messages, tools, system_prompt):
            captured["system_prompt"] = system_prompt
            # Emit a minimal end-of-stream so the SSE loop terminates.
            from ursa_oscar.ai_proxy.providers.base import AiStreamEvent
            yield AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "end_turn", "usage": {}},
            )

    monkeypatch.setattr(
        ai_module, "build_adapter",
        lambda *_a, **_kw: _CapturingAdapter(),
    )

    r = ai_test_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200
    assert sentinel in captured.get("system_prompt", ""), (
        "Expected the runtime to render the template-store content into "
        "the system prompt when no per-provider override is set. Got: "
        f"{captured.get('system_prompt', '')[:200]!r}"
    )


def test_chat_per_provider_override_still_wins_over_template_store(
    ai_test_client, monkeypatch,
):
    """If the operator HAS set a per-provider override (cfg.custom_system_prompt),
    that still beats whatever's in the template file. The override is
    the most specific knob and stays on top."""
    template_sentinel = "TEMPLATE-FILE-CONTENT"
    override_sentinel = "PER-PROVIDER-OVERRIDE-CONTENT"

    ai_test_client.put(
        "/api/v1/ai/system-prompt/template",
        json={"template": template_sentinel + "\n"},
    )
    r = ai_test_client.post("/api/v1/ai/config", json={
        "enabled": True,
        "provider_id": "claude",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "sk-ant-test-fake",
        "custom_system_prompt": override_sentinel,
    })
    assert r.status_code == 200

    import ursa_oscar.api.ai as ai_module
    captured: dict = {}

    class _CapturingAdapter:
        async def chat(self, messages, tools, system_prompt):
            captured["system_prompt"] = system_prompt
            from ursa_oscar.ai_proxy.providers.base import AiStreamEvent
            yield AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "end_turn", "usage": {}},
            )

    monkeypatch.setattr(
        ai_module, "build_adapter",
        lambda *_a, **_kw: _CapturingAdapter(),
    )

    r = ai_test_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200
    prompt = captured.get("system_prompt", "")
    assert override_sentinel in prompt
    assert template_sentinel not in prompt, (
        "Per-provider override should suppress the template-file content "
        "entirely, not append to it."
    )


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

    async def fake_execute_tool(name, args, api_base_url=None, auth_header=None):
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


def test_chat_surfaces_diagnostic_for_malformed_tool_call_as_content(
    chat_ready_client, monkeypatch,
):
    """1.1.4 regression — when an under-capable local model emits a few
    characters of JSON as ``delta.content`` (trying to write a tool-call
    as text instead of using the OpenAI tool-call format) and then
    finishes with ``stop_reason="stop"`` and no tool_calls, the chat
    handler must emit a friendly diagnostic error rather than letting
    the user see a confusing single ``{`` in the chat panel.

    Real-world trigger: Qwen3-4b on CPU + URSA's full 18-tool surface.
    The model can't format the tool call correctly with the prompt
    complexity, so it emits ``{"`` and stops.
    """
    import ursa_oscar.api.ai as ai_module
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent

    # Scripted adapter that emits exactly the failure shape: one text
    # event with `{"` content, then complete with stop_reason="stop".
    script = [
        [
            AiStreamEvent(event_type="text", payload={"text": "{\""}),
            AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "stop", "usage": None},
            ),
        ],
    ]

    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter(script),
    )

    async def fake_execute_tool(name, args, api_base_url=None, auth_header=None):
        return {"ok": True, "data": {}}

    monkeypatch.setattr(ai_module, "execute_tool", fake_execute_tool)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "Hello"}],
        "context": {"current_date": "2026-05-23", "include_profile": False},
    })
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]

    # The text event still gets through (operator briefly sees `{"`
    # before the diagnostic banner replaces it).
    assert "text" in types

    # The critical assertion: a diagnostic error event must follow.
    error_events = [e for e in events if e["event_type"] == "error"]
    assert len(error_events) == 1, (
        f"Expected one diagnostic error event for malformed-tool-call "
        f"shape; got events={types}. If empty, the heuristic in "
        f"ai.py didn't fire — check the conditions (saw_text, "
        f"content_stripped startswith {{, stop_reason='stop')."
    )
    err = error_events[0]
    assert err["payload"]["code"] == "MODEL_INCOMPLETE_RESPONSE"
    assert "model isn't capable" in err["payload"]["message"].lower() or \
        "partial response" in err["payload"]["message"].lower()

    # The complete event MUST NOT be sent when we surface the
    # diagnostic — the error event is the terminal frame instead.
    # (Sending both would confuse the client's state machine which
    # treats the first of complete/error as end-of-stream.)
    complete_events = [e for e in events if e["event_type"] == "complete"]
    assert len(complete_events) == 0, (
        "Diagnostic-shaped failure should terminate with error event, "
        "not complete event."
    )


def test_chat_does_not_misfire_diagnostic_on_legitimate_short_response(
    chat_ready_client, monkeypatch,
):
    """1.1.4 — the diagnostic heuristic must NOT fire for legitimate
    short responses. A model answering 'Yes.' to a yes/no question is
    valid and must pass through unchanged.

    Distinguishes from the bug case by: content doesn't start with `{`.
    """
    import ursa_oscar.api.ai as ai_module
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent

    script = [
        [
            AiStreamEvent(event_type="text", payload={"text": "Yes."}),
            AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "stop", "usage": None},
            ),
        ],
    ]

    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter(script),
    )

    async def fake_execute_tool(name, args, api_base_url=None, auth_header=None):
        return {"ok": True, "data": {}}

    monkeypatch.setattr(ai_module, "execute_tool", fake_execute_tool)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "Yes or no?"}],
        "context": {"current_date": "2026-05-23", "include_profile": False},
    })
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]

    # Normal completion: text event, then complete. No diagnostic.
    assert "error" not in types, (
        f"Diagnostic heuristic over-fired on legitimate short response. "
        f"Events: {types}"
    )
    assert "complete" in types


@pytest.mark.asyncio
async def test_openai_compat_emits_reasoning_events_for_thinking_models():
    """1.1.3 regression — the OpenAI-compat adapter must surface
    chain-of-thought deltas from thinking-mode models (Qwen3,
    DeepSeek-R1, etc.) as ``reasoning`` events, not silently discard
    them.

    Qwen3 via LocalAI / Ollama emits ``delta.reasoning`` with
    ``delta.content`` null for the entire thinking phase. Before the
    1.1.3 fix, the adapter only read ``delta.content`` and dropped
    every reasoning chunk on the floor, producing the "121 seconds of
    silence then [DONE]" UX the operator reported on launch week.

    DeepSeek's native API uses ``delta.reasoning_content`` (different
    field name, same role). Adapter accepts both.
    """
    # Synthetic SSE chunks matching the Qwen3-4b shape captured from
    # the live LocalAI endpoint during the 1.1.3 investigation.
    sse_lines = [
        'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}',
        'data: {"choices":[{"delta":{"content":null,"reasoning":"Let me think."}}]}',
        'data: {"choices":[{"delta":{"content":null,"reasoning":" 2+2"}}]}',
        # Also exercise the DeepSeek-style field name in the same stream.
        'data: {"choices":[{"delta":{"content":null,"reasoning_content":" equals"}}]}',
        # Final answer.
        'data: {"choices":[{"delta":{"content":"The answer is 4."}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    class _FakeResponse:
        async def aiter_lines(self):
            for line in sse_lines:
                yield line

    adapter = OpenAiCompatAdapter(
        api_key="test", endpoint="http://test.local/v1",
        model="qwen3-4b", extra_headers={},
    )
    events = []
    async for ev in adapter._consume_sse(_FakeResponse()):
        events.append(ev)

    reasoning_events = [e for e in events if e.event_type == "reasoning"]
    text_events = [e for e in events if e.event_type == "text"]
    complete_events = [e for e in events if e.event_type == "complete"]

    # Three reasoning chunks: two `reasoning` + one `reasoning_content`.
    assert len(reasoning_events) == 3, (
        f"Expected 3 reasoning events, got {len(reasoning_events)}. "
        "If 0: the adapter is still discarding thinking-mode chunks "
        "(1.1.3 regression). If >3: an unrelated delta path is "
        "double-emitting."
    )
    assert reasoning_events[0].payload["text"] == "Let me think."
    assert reasoning_events[1].payload["text"] == " 2+2"
    assert reasoning_events[2].payload["text"] == " equals"  # reasoning_content path

    # One regular text event for the final answer (content delta).
    assert len(text_events) == 1
    assert text_events[0].payload["text"] == "The answer is 4."

    # Clean termination.
    assert len(complete_events) == 1
    assert complete_events[0].payload["stop_reason"] == "stop"


def test_chat_forwards_session_cookie_as_bearer_to_loopback(
    chat_ready_client, monkeypatch,
):
    """1.1.1 regression — Phase 6.4 added _AUTH_REQUIRED to every API
    router; the AI proxy's loopback to /api/v1/night/... etc. needs the
    operator's JWT or it 401s. The chat endpoint must forward the JWT
    via the ``auth_header`` kwarg on execute_tool, and the value must
    work whether the operator presented their JWT as a cookie (browser
    session) or as a Bearer header (MCP/CLI client).

    This locks down the cookie -> Bearer forwarding specifically — the
    case that 1.1.0 shipped broken because the first fix only read the
    Authorization header.
    """
    import ursa_oscar.api.ai as ai_module
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent

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
                event_type="text", payload={"text": "OK"},
            ),
            AiStreamEvent(
                event_type="complete",
                payload={"stop_reason": "end_turn", "usage": {"output_tokens": 1}},
            ),
        ],
    ]

    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter(script),
    )

    captured: dict = {}

    async def fake_execute_tool(
        name, args, api_base_url=None, auth_header=None,
    ):
        captured["name"] = name
        captured["auth_header"] = auth_header
        return {"ok": True, "data": {"date": "2026-05-13"}}

    monkeypatch.setattr(ai_module, "execute_tool", fake_execute_tool)

    # Browser-session case: the operator's JWT arrives as an
    # ursa_oscar_session cookie. The chat endpoint must convert it
    # into "Bearer <token>" and pass via auth_header.
    chat_ready_client.cookies.set("ursa_oscar_session", "dummy-jwt-from-cookie")
    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "How was my sleep?"}],
        "context": {"current_date": "2026-05-13", "include_profile": False},
    })
    assert r.status_code == 200, r.text

    assert captured["name"] == "get_nightly_summary"
    assert captured["auth_header"] == "Bearer dummy-jwt-from-cookie", (
        "Cookie session was not forwarded as Bearer header to the "
        "loopback. This is the 1.1.0 launch bug — every tool call "
        "would 401 against Phase 6.4 _AUTH_REQUIRED."
    )

    # Reset for the second case.
    chat_ready_client.cookies.clear()
    captured.clear()

    # Reset the adapter so a fresh tool-call sequence runs.
    monkeypatch.setattr(
        ai_module,
        "build_adapter",
        lambda *_a, **_kw: _ScriptedAdapter([
            [
                AiStreamEvent(
                    event_type="tool_call_start",
                    payload={"id": "tu_02", "name": "get_nightly_summary"},
                ),
                AiStreamEvent(
                    event_type="tool_call_complete",
                    payload={
                        "id": "tu_02",
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
                    event_type="text", payload={"text": "OK"},
                ),
                AiStreamEvent(
                    event_type="complete",
                    payload={
                        "stop_reason": "end_turn",
                        "usage": {"output_tokens": 1},
                    },
                ),
            ],
        ]),
    )

    # Bearer-header case: when no cookie is present, the inbound
    # Authorization header is forwarded verbatim. Covers MCP / CLI
    # callers that prefer Bearer over cookies.
    r = chat_ready_client.post(
        "/api/v1/ai/chat",
        json={
            "messages": [{"role": "user", "content": "How was my sleep?"}],
            "context": {
                "current_date": "2026-05-13", "include_profile": False,
            },
        },
        headers={"Authorization": "Bearer dummy-jwt-from-header"},
    )
    assert r.status_code == 200, r.text
    assert captured["auth_header"] == "Bearer dummy-jwt-from-header"


# -------------------------------------------------------------------------
# Phase 5.5 Item 3 — scripted-adapter coverage for the Q1-Q5 acceptance
# matrix + the error paths the operator-led validation missed in 0.9.0.
#
# These reuse the _ScriptedAdapter / _parse_sse_events / chat_ready_client
# scaffolding above. The pattern is: build a realistic event sequence
# for one query class, optionally stub execute_tool to return a canned
# envelope, POST /ai/chat, assert the wire output matches the contract.
#
# None of these require live LLM credentials. They run in normal CI and
# catch wire-protocol regressions before they reach the operator.
# -------------------------------------------------------------------------


def _ai_event(event_type, **payload):
    """Shorthand — half the test body would be ``AiStreamEvent(...)``
    constructor noise otherwise."""
    from ursa_oscar.ai_proxy.providers.base import AiStreamEvent
    return AiStreamEvent(event_type=event_type, payload=payload)


def _setup_chat(monkeypatch, script, tool_results: dict | list | None = None):
    """Wire a scripted adapter and a fake execute_tool into the ai
    module. ``tool_results`` can be:
      - a dict keyed by tool-name -> envelope (one-shot per call)
      - a list of envelopes (consumed FIFO across all tool calls)
      - None -> every tool call returns ``{ok: True, data: {}}``
    """
    import ursa_oscar.api.ai as ai_module

    adapter = _ScriptedAdapter(script)
    monkeypatch.setattr(
        ai_module, "build_adapter", lambda *_a, **_kw: adapter,
    )

    if isinstance(tool_results, list):
        results_iter = iter(tool_results)

        async def fake_execute(name, args, api_base_url=None, auth_header=None):
            try:
                return next(results_iter)
            except StopIteration:
                return {"ok": True, "data": {}}

    elif isinstance(tool_results, dict):

        async def fake_execute(name, args, api_base_url=None, auth_header=None):
            return tool_results.get(name, {"ok": True, "data": {}})

    else:

        async def fake_execute(name, args, api_base_url=None, auth_header=None):
            return {"ok": True, "data": {}}

    monkeypatch.setattr(ai_module, "execute_tool", fake_execute)


# --- Q1 — single-tool query (full-loop shape assertion) -----------------


def test_chat_single_tool_query_full_loop(chat_ready_client, monkeypatch):
    """Q1 acceptance — 'How was my sleep on 2026-05-13?' shape.

    Wire contract: tool_call_start -> tool_call_complete -> tool_result
    (server-injected after execute_tool) -> text deltas -> single final
    complete(end_turn). The 0.9.7 chat panel's reducer + the operator's
    live Claude validation both depend on this exact sequence."""
    script = [
        [
            _ai_event("tool_call_start", id="tu_q1", name="get_nightly_summary"),
            _ai_event("tool_call_complete", id="tu_q1",
                      name="get_nightly_summary",
                      arguments={"date": "2026-05-13"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="Your AHI was 3.94. "),
            _ai_event("text", text="Two sessions, 7h 04m total."),
            _ai_event("complete", stop_reason="end_turn",
                      usage={"output_tokens": 42}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {"date": "2026-05-13", "total_ahi": 3.94,
                              "session_count": 2}},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "How was my sleep on 2026-05-13?"}],
        "context": {"current_date": "2026-05-13", "include_profile": False},
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]
    assert types == [
        "tool_call_start", "tool_call_complete",
        "tool_result",
        "text", "text",
        "complete",
    ]

    # Tool-call argument plumbing made it through verbatim.
    tcc = next(e for e in events if e["event_type"] == "tool_call_complete")
    assert tcc["payload"]["name"] == "get_nightly_summary"
    assert tcc["payload"]["arguments"] == {"date": "2026-05-13"}

    # Tool result envelope reaches the client with its data intact.
    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["result"]["ok"] is True
    assert tr["payload"]["result"]["data"]["total_ahi"] == 3.94

    # The concatenated text deltas form the final assistant message.
    text_deltas = [e["payload"]["text"] for e in events if e["event_type"] == "text"]
    assert "".join(text_deltas) == "Your AHI was 3.94. Two sessions, 7h 04m total."

    # End-of-stream signal carries the right stop_reason.
    final = next(e for e in events if e["event_type"] == "complete")
    assert final["payload"]["stop_reason"] == "end_turn"

    # 1.1.14 — the terminal complete carries a per-turn meta block.
    meta = final["payload"]["meta"]
    # Breakdown buckets present + total is their sum.
    bd = meta["breakdown"]
    assert set(bd) == {"system", "tools", "tool_results", "history", "total"}
    assert bd["total"] == bd["system"] + bd["tools"] + bd["tool_results"] + bd["history"]
    # System prompt is a real chunk (persona + tool index + profile-off).
    assert bd["system"] > 0
    # The tool result we injected shows in the tool_results bucket.
    assert bd["tool_results"] > 0
    # Execution trace: exactly the one tool that ran.
    assert meta["tools_used"] == ["get_nightly_summary"]
    # Two adapter rounds (tool turn + prose turn).
    assert meta["rounds"] == 2
    assert meta["elapsed_ms"] >= 0
    # Real server usage was provided (output_tokens=42) -> not estimated.
    assert meta["tokens"]["completion"] == 42
    assert meta["tokens"]["estimated"] is False


def test_chat_meta_estimates_tokens_when_usage_absent(chat_ready_client, monkeypatch):
    """When the provider returns no usable usage (local servers that
    ignore include_usage), meta.tokens falls back to the chars/4 estimate
    and is flagged estimated=true so the UI can render a '~'."""
    script = [
        [
            _ai_event("text", text="You slept well."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "how did I sleep?"}],
        "context": {"include_profile": False},
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    meta = next(e for e in events if e["event_type"] == "complete")["payload"]["meta"]
    assert meta["tokens"]["estimated"] is True
    # Prompt estimate == the breakdown total (the exact payload sent).
    assert meta["tokens"]["prompt"] == meta["breakdown"]["total"]
    # Completion estimated from the answer text ("You slept well." -> 4 tok).
    assert meta["tokens"]["completion"] >= 1
    # No tools ran this turn.
    assert meta["tools_used"] == []
    assert meta["rounds"] == 1


# --- Q2 — multi-tool comparison loop ------------------------------------


def test_chat_multi_tool_comparison_loop(chat_ready_client, monkeypatch):
    """Q2 acceptance — 'Compare last night to the previous 5 nights'.

    Realistic pattern: model calls get_nightly_summary for context,
    then compare_periods for the actual comparison, then renders prose.
    Three adapter turns; two tool executions; one final complete."""
    script = [
        [  # Turn 1: nightly summary
            _ai_event("tool_call_start", id="tu_a", name="get_nightly_summary"),
            _ai_event("tool_call_complete", id="tu_a",
                      name="get_nightly_summary",
                      arguments={"date": "2026-05-13"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [  # Turn 2: compare periods
            _ai_event("tool_call_start", id="tu_b", name="compare_periods"),
            _ai_event("tool_call_complete", id="tu_b",
                      name="compare_periods",
                      arguments={"period_a_start": "2026-05-13",
                                 "period_a_end": "2026-05-13",
                                 "period_b_start": "2026-05-08",
                                 "period_b_end": "2026-05-12"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [  # Turn 3: prose answer
            _ai_event("text", text="Last night's AHI of 3.94 was better than the previous-5 mean of 6.7."),
            _ai_event("complete", stop_reason="end_turn",
                      usage={"output_tokens": 88}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {"date": "2026-05-13", "total_ahi": 3.94}},
        {"ok": True, "data": {"period_a": {"mean_ahi": 3.94},
                              "period_b": {"mean_ahi": 6.7},
                              "delta": -2.76}},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "Compare last night to the previous 5 nights."}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]

    # Two complete loops + final text. Critical assertion: exactly ONE
    # complete in the wire output (the 0.9.6 bug surfaced exactly here
    # under multi-turn loops — and was the test the operator ran that
    # validated the fix).
    completes = [e for e in events if e["event_type"] == "complete"]
    assert len(completes) == 1
    assert completes[0]["payload"]["stop_reason"] == "end_turn"

    # Two tool_result events, in the order the adapter requested them.
    tool_results = [e for e in events if e["event_type"] == "tool_result"]
    assert len(tool_results) == 2
    assert tool_results[0]["payload"]["id"] == "tu_a"
    assert tool_results[1]["payload"]["id"] == "tu_b"

    # tool_call_start ordering preserved (UI renders chips in this
    # order under the assistant message).
    starts = [e for e in events if e["event_type"] == "tool_call_start"]
    assert [s["payload"]["name"] for s in starts] == [
        "get_nightly_summary", "compare_periods",
    ]


# --- Q3 — correlation query --------------------------------------------


def test_chat_correlation_tool_call(chat_ready_client, monkeypatch):
    """Q3 acceptance — 'Does my AHI correlate with leak?' shape.

    Single analyze_correlation tool call. Verifies that complex argument
    payloads (date ranges, metric names) survive the JSON-encode +
    re-decode round trip through the SSE wire."""
    script = [
        [
            _ai_event("tool_call_start", id="tu_c", name="analyze_correlation"),
            _ai_event("tool_call_complete", id="tu_c",
                      name="analyze_correlation",
                      arguments={
                          "metric_a": "total_ahi",
                          "metric_b": "p95_leak",
                          "start_date": "2026-04-15",
                          "end_date": "2026-05-15",
                          "lag_days": 0,
                      }),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="Weak negative correlation (r=-0.21, n=28)."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {
            "pearson_r": -0.21, "p_value": 0.28, "n_pairs": 28,
            "interpretation": "weak_negative",
        }},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "Does my AHI correlate with leak?"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    tcc = next(e for e in events if e["event_type"] == "tool_call_complete")
    assert tcc["payload"]["name"] == "analyze_correlation"
    args = tcc["payload"]["arguments"]
    assert args["metric_a"] == "total_ahi"
    assert args["metric_b"] == "p95_leak"
    assert args["lag_days"] == 0  # int survives the JSON round-trip

    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["result"]["data"]["pearson_r"] == -0.21


# --- Q4 — trend query ---------------------------------------------------


def test_chat_trend_tool_call(chat_ready_client, monkeypatch):
    """Q4 acceptance — 'What's my AHI trend over the past month?' shape.

    Single get_trend tool call. Verifies float values (slope per day,
    intercept) round-trip cleanly through the SSE envelope."""
    script = [
        [
            _ai_event("tool_call_start", id="tu_t", name="get_trend"),
            _ai_event("tool_call_complete", id="tu_t", name="get_trend",
                      arguments={"metric": "total_ahi",
                                 "start_date": "2026-04-15",
                                 "end_date": "2026-05-15"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="AHI trending down (-0.04/day, n=28)."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {
            "slope_per_day": -0.04,
            "intercept": 6.2,
            "r_squared": 0.31,
            "interpretation": "improving",
        }},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "What's my AHI trend over the past month?"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    tcc = next(e for e in events if e["event_type"] == "tool_call_complete")
    assert tcc["payload"]["arguments"]["metric"] == "total_ahi"

    tr = next(e for e in events if e["event_type"] == "tool_result")
    # Negative float survives sign + precision.
    assert tr["payload"]["result"]["data"]["slope_per_day"] == -0.04


# --- Q5 — manual logs query --------------------------------------------


def test_chat_manual_logs_query(chat_ready_client, monkeypatch):
    """Q5 acceptance — 'How many times did I take doxepin last week?'

    Exercises the manual-logs tool path (distinct backend surface from
    the nightly_summary / analytics path; lives under /manual-logs)."""
    script = [
        [
            _ai_event("tool_call_start", id="tu_m",
                      name="get_manual_log_summary"),
            _ai_event("tool_call_complete", id="tu_m",
                      name="get_manual_log_summary",
                      arguments={"start_date": "2026-05-08",
                                 "end_date": "2026-05-14",
                                 "log_type": "medication"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="Doxepin logged on 5 of 7 days."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": True, "data": {
            "total_entries": 5,
            "by_type": {"medication": {"doxepin": 5}},
            "date_range": {"start": "2026-05-08", "end": "2026-05-14"},
        }},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "How many times did I take doxepin last week?"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]
    # Same canonical shape as the other tool queries.
    assert types == [
        "tool_call_start", "tool_call_complete",
        "tool_result",
        "text",
        "complete",
    ]

    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["result"]["data"]["total_entries"] == 5


# --- Error paths --------------------------------------------------------


def test_chat_tool_error_surfaces_to_client(chat_ready_client, monkeypatch):
    """Tool returns ok=False — the envelope must reach the client as a
    tool_result event so the UI can render the error chip + the model
    can describe what went wrong. Stream MUST continue (this is not a
    fatal error — the LLM might recover by trying a different tool)."""
    script = [
        [
            _ai_event("tool_call_start", id="tu_e", name="get_nightly_summary"),
            _ai_event("tool_call_complete", id="tu_e",
                      name="get_nightly_summary",
                      arguments={"date": "2099-01-01"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ],
        [
            _ai_event("text", text="I couldn't find data for that date."),
            _ai_event("complete", stop_reason="end_turn", usage={}),
        ],
    ]
    _setup_chat(monkeypatch, script, tool_results=[
        {"ok": False, "code": "NOT_FOUND",
         "error": "No nightly data for 2099-01-01"},
    ])

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "How was my sleep on 2099-01-01?"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    # tool_result delivered the error envelope intact (UI's tool chip
    # transitions to 'error' state based on this).
    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["result"]["ok"] is False
    assert tr["payload"]["result"]["code"] == "NOT_FOUND"

    # Stream still ends with a single clean complete — tool error is
    # not a stream-fatal condition.
    completes = [e for e in events if e["event_type"] == "complete"]
    assert len(completes) == 1
    assert completes[0]["payload"]["stop_reason"] == "end_turn"


def test_chat_adapter_error_mid_stream_terminates_cleanly(
    chat_ready_client, monkeypatch,
):
    """Distinct from the existing 'error as first event' test — here
    the adapter emits some text, THEN errors. The server must forward
    the partial text and the error, and NOT emit a trailing complete
    after the error (the client breaks on either)."""
    script = [
        [
            _ai_event("text", text="Looking at your data… "),
            _ai_event("error", message="upstream 503", code="upstream_error"),
            # Adapter doesn't emit a complete after error — but even if
            # a faulty adapter did, the server must surface the error
            # and stop. We don't add one here.
        ],
    ]
    _setup_chat(monkeypatch, script)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)
    types = [e["event_type"] for e in events]

    # Text arrived first, then error. NO trailing complete.
    assert types == ["text", "error"], f"unexpected sequence: {types}"
    assert events[-1]["payload"]["code"] == "upstream_error"


def test_chat_safety_cap_terminates_runaway_loop(chat_ready_client, monkeypatch):
    """A misbehaving model that keeps requesting tools indefinitely
    (Llama 3.2 3B has been observed to do this) must be capped at the
    8-iteration safety limit. The server emits a tool_loop_limit
    error and stops — no trailing complete, no unbounded resource use."""
    # Nine turns where the adapter always asks for another tool. The
    # server should process 8 and emit the safety-cap error on turn 9.
    script = []
    for i in range(9):
        script.append([
            _ai_event("tool_call_start", id=f"tu_{i}", name="get_nightly_summary"),
            _ai_event("tool_call_complete", id=f"tu_{i}",
                      name="get_nightly_summary",
                      arguments={"date": "2026-05-13"}),
            _ai_event("complete", stop_reason="tool_use", usage={}),
        ])
    _setup_chat(monkeypatch, script)

    r = chat_ready_client.post("/api/v1/ai/chat", json={
        "messages": [{"role": "user", "content": "loop forever please"}],
    })
    assert r.status_code == 200
    events = _parse_sse_events(r.content)

    # Should see exactly 8 tool_call_start events (one per loop iteration
    # within the cap), then a tool_loop_limit error.
    starts = [e for e in events if e["event_type"] == "tool_call_start"]
    assert len(starts) == 8, (
        f"expected exactly 8 loop iterations under the cap, got {len(starts)}"
    )
    errors = [e for e in events if e["event_type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["code"] == "tool_loop_limit"

    # No trailing complete after the cap error.
    completes = [e for e in events if e["event_type"] == "complete"]
    assert len(completes) == 0


# --- Existing pre-Phase-5.5 tests (kept here in original order) --------


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
