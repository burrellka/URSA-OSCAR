"""Phase 6.4.1 — MCP client token resolution.

Verifies the env-var-then-file fallback chain in
``ursa_oscar_mcp.client._resolve_api_token``. Pure unit tests; no API
or HTTP layer involved.
"""
from __future__ import annotations

import os

import pytest

from ursa_oscar_mcp import client


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Each test starts with a clean env for the token vars and a
    fresh ``URSA_OSCAR_DB_PATH`` pointing at the test's tmp_path. The
    autouse session-scoped api_server fixture sets these to real
    values; we need to override per-test."""
    monkeypatch.delenv(client.API_TOKEN_ENV, raising=False)
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(tmp_path / "test.duckdb"))
    yield


def test_env_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv(client.API_TOKEN_ENV, "env-supplied-jwt")
    # Even if a file is present, env wins.
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "mcp.jwt").write_text("file-supplied-jwt", encoding="utf-8")

    assert client._resolve_api_token() == "env-supplied-jwt"


def test_falls_back_to_file_when_env_unset(tmp_path):
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "mcp.jwt").write_text("file-supplied-jwt", encoding="utf-8")

    assert client._resolve_api_token() == "file-supplied-jwt"


def test_returns_none_when_neither_configured(tmp_path):
    # No env, no file.
    assert client._resolve_api_token() is None


def test_whitespace_env_falls_through_to_file(tmp_path, monkeypatch):
    """Pasted env vars sometimes have stray whitespace; treat empty-
    after-strip as unset so the file fallback engages."""
    monkeypatch.setenv(client.API_TOKEN_ENV, "   \t  ")
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "mcp.jwt").write_text("file-supplied-jwt", encoding="utf-8")
    assert client._resolve_api_token() == "file-supplied-jwt"


def test_empty_file_falls_through_to_none(tmp_path):
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "mcp.jwt").write_text("", encoding="utf-8")
    assert client._resolve_api_token() is None


def test_auth_headers_present_when_token_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv(client.API_TOKEN_ENV, "the-jwt")
    headers = client._auth_headers()
    assert headers == {"Authorization": "Bearer the-jwt"}


def test_auth_headers_empty_when_no_token(tmp_path):
    headers = client._auth_headers()
    assert headers == {}


def test_only_reads_mcp_jwt_not_watcher_jwt(tmp_path):
    """The MCP container must only ever pick up its own token, never
    the watcher's — otherwise a per-service revoke is impossible."""
    svc_dir = tmp_path / "service_tokens"
    svc_dir.mkdir()
    (svc_dir / "watcher.jwt").write_text("watcher-token", encoding="utf-8")
    # No mcp.jwt — should be None despite a watcher.jwt sibling.
    assert client._resolve_api_token() is None
