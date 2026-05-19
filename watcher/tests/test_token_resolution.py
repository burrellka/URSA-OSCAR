"""Phase 6.4.1 — watcher auto-managed service-token resolution.

The watcher config follows the same env-then-file fallback as the MCP
client. These tests cover that chain pure-unit (no httpx).
"""
from __future__ import annotations

import pytest

from ursa_oscar_watcher import config
from ursa_oscar_watcher.config import WatcherConfig


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Each test starts with a clean env for the token vars and a
    fresh DB_PATH pointing at the test's tmp_path."""
    monkeypatch.delenv("URSA_OSCAR_WATCHER_TOKEN", raising=False)
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("URSA_OSCAR_WATCH_PATH", "/tmp/watch")
    yield


def test_env_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("URSA_OSCAR_WATCHER_TOKEN", "env-supplied-jwt")
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "watcher.jwt").write_text("file-supplied-jwt", encoding="utf-8")

    assert config._resolve_api_token() == "env-supplied-jwt"
    cfg = WatcherConfig.from_env()
    assert cfg.api_token == "env-supplied-jwt"


def test_falls_back_to_file_when_env_unset(tmp_path):
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "watcher.jwt").write_text("file-supplied-jwt", encoding="utf-8")

    cfg = WatcherConfig.from_env()
    assert cfg.api_token == "file-supplied-jwt"


def test_returns_none_when_neither_configured(tmp_path):
    cfg = WatcherConfig.from_env()
    assert cfg.api_token is None


def test_whitespace_env_falls_through_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv("URSA_OSCAR_WATCHER_TOKEN", "   ")
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "watcher.jwt").write_text("file-supplied-jwt", encoding="utf-8")
    assert config._resolve_api_token() == "file-supplied-jwt"


def test_empty_file_is_treated_as_missing(tmp_path):
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "watcher.jwt").write_text("", encoding="utf-8")
    assert config._resolve_api_token() is None


def test_only_reads_watcher_jwt_not_mcp_jwt(tmp_path):
    """Symmetric guarantee with MCP — the watcher must not accidentally
    pick up the MCP token (or vice versa) if both files are present."""
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "mcp.jwt").write_text("mcp-token", encoding="utf-8")
    # No watcher.jwt — should be None.
    assert config._resolve_api_token() is None
