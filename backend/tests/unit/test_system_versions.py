"""1.1.3 regression — version-chip introspection.

The Settings page's image-version chips used to read env vars the
operator had to keep in sync with the compose ``image:`` lines. As of
1.1.3, each service is the source of truth for its own version. These
tests cover the three introspection helpers that drive the API
container's view of the four-service version state.

See ``ursa_oscar.api.system._resolve_image_versions`` for the
production resolver that chains these helpers.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ursa_oscar.api.system import (
    _mcp_version_via_http,
    _self_api_version,
    _watcher_version_from_file,
)


# -------------------------------------------------------------------------
# API container's own version (importlib.metadata).
# -------------------------------------------------------------------------


def test_self_api_version_returns_packaged_string():
    """Reads from importlib.metadata. In the test environment the
    package is installed (pip install -e .), so this returns the
    pyproject.toml's version field — not 'dev'."""
    v = _self_api_version()
    assert isinstance(v, str)
    assert v != "dev", (
        "Test environment should have ursa-oscar-backend installed. "
        "'dev' indicates importlib.metadata fell back to its sentinel."
    )
    # Version follows semver-ish: digits and dots, possibly with suffix.
    assert any(c.isdigit() for c in v)


def test_self_api_version_falls_back_to_dev_when_unpackaged(monkeypatch):
    """When the package is not installed, returns 'dev' rather than
    raising. Catches the unpackaged-source-tree edge case."""
    from importlib.metadata import PackageNotFoundError

    def _raise_not_found(_name):
        raise PackageNotFoundError("not installed")

    monkeypatch.setattr("ursa_oscar.api.system._pkg_version", _raise_not_found)
    assert _self_api_version() == "dev"


# -------------------------------------------------------------------------
# MCP /version HTTP probe.
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_version_via_http_returns_string_on_200(monkeypatch):
    """Happy path. MCP container's /version returns {"version": "1.1.3"};
    the helper extracts and returns the string."""

    class _FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"version": "1.1.3"}

    class _FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url):
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    result = await _mcp_version_via_http("http://ursa-oscar-mcp:8000")
    assert result == "1.1.3"


@pytest.mark.asyncio
async def test_mcp_version_via_http_returns_none_on_404(monkeypatch):
    """Old MCP image without /version endpoint. Helper returns None
    so the Settings UI can render 'unknown' instead of breaking."""

    class _FakeResp:
        status_code = 404

        @staticmethod
        def json():
            return {}

    class _FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url):
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    assert await _mcp_version_via_http("http://ursa-oscar-mcp:8000") is None


@pytest.mark.asyncio
async def test_mcp_version_via_http_returns_none_on_network_error(monkeypatch):
    """MCP container is down or unreachable. Helper swallows the
    exception and returns None — the API's /system/config request
    must complete cleanly even when a sibling container is unavailable."""

    class _ExplodingClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url):
            raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)
    assert await _mcp_version_via_http("http://ursa-oscar-mcp:8000") is None


@pytest.mark.asyncio
async def test_mcp_version_via_http_returns_none_when_url_missing():
    """No internal URL configured. Helper short-circuits to None
    without attempting any HTTP call."""
    assert await _mcp_version_via_http(None) is None
    assert await _mcp_version_via_http("") is None


@pytest.mark.asyncio
async def test_mcp_version_via_http_returns_none_on_bad_response_shape(monkeypatch):
    """MCP returns 200 but the body isn't the expected
    {"version": "..."} shape. Helper returns None rather than
    propagating the malformed value."""

    class _FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return ["not", "a", "dict"]

    class _FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url):
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    assert await _mcp_version_via_http("http://ursa-oscar-mcp:8000") is None


# -------------------------------------------------------------------------
# Watcher version file read.
# -------------------------------------------------------------------------


def test_watcher_version_from_file_reads_existing(tmp_path: Path):
    """Watcher container writes /data/versions/watcher.txt at startup.
    API reads it for the Settings page chip."""
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions" / "watcher.txt").write_text("1.1.3", encoding="utf-8")
    assert _watcher_version_from_file(tmp_path) == "1.1.3"


def test_watcher_version_from_file_strips_whitespace(tmp_path: Path):
    """The file write at startup may include a trailing newline. The
    reader strips so the chip displays cleanly."""
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions" / "watcher.txt").write_text("  1.1.3\n", encoding="utf-8")
    assert _watcher_version_from_file(tmp_path) == "1.1.3"


def test_watcher_version_from_file_returns_none_when_missing(tmp_path: Path):
    """First boot, watcher hasn't written yet, or watcher image
    predates 1.1.3. Helper returns None so the Settings UI renders
    'unknown' instead of crashing."""
    # versions/ directory deliberately not created
    assert _watcher_version_from_file(tmp_path) is None


def test_watcher_version_from_file_returns_none_when_empty(tmp_path: Path):
    """File exists but is empty (write was interrupted, manual clear,
    etc.). Treat as missing so the chip shows 'unknown' rather than
    an empty string."""
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions" / "watcher.txt").write_text("", encoding="utf-8")
    assert _watcher_version_from_file(tmp_path) is None
