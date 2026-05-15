"""Phase 5 Ticket 0 — fixture unblocking the previously-skipped MCP tool tests.

Architecture: each MCP tool (post-Phase 3 ADR-003 refactor) hits the URSA-OSCAR
API container over HTTP rather than touching DuckDB directly. To test the tools
in isolation we need a real API process serving requests. This conftest:

  1. Allocates a free TCP port on 127.0.0.1.
  2. Sets ``URSA_OSCAR_API_URL`` / ``URSA_OSCAR_DB_PATH`` env vars at module
     load time — BEFORE any test imports ``ursa_oscar_mcp.client`` (which
     captures the URL at import time).
  3. Seeds a temp DuckDB with the canonical 4-night regression fixture.
  4. Boots the real FastAPI app in a background thread via uvicorn.
  5. Waits for /healthz to return 200 before yielding to tests.
  6. Tears down (signal server to exit, join thread, remove temp DB).

The fixture is session-scoped + autouse: every test in this directory gets
the same server, no per-test boot/teardown overhead.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest


# -------------------------------------------------------------------------
# Module-level setup. Runs once when pytest collects this conftest, BEFORE
# any test file imports a tool module. Env vars set here are visible to
# every subsequent import of ursa_oscar_mcp.client.
# -------------------------------------------------------------------------


def _allocate_port() -> int:
    """Reserve a TCP port. Bind + release pattern — uvicorn rebinds in a
    moment. Race window is acceptable on localhost in CI/test contexts."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_TEST_PORT = _allocate_port()
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="ursa-mcp-test-"))
_TEST_DB_PATH = _TEST_DB_DIR / "test.duckdb"
_TEST_API_URL = f"http://127.0.0.1:{_TEST_PORT}"

# Critical — ursa_oscar_mcp.client captures URSA_OSCAR_API_URL at module
# import time. Set the env before any test triggers that import.
os.environ["URSA_OSCAR_API_URL"] = _TEST_API_URL
os.environ["URSA_OSCAR_DB_PATH"] = str(_TEST_DB_PATH)

# Required by the MCP server's auth provider at import time. Values are
# arbitrary — the tool tests don't exercise auth, they call tool functions
# directly which go through the API client (HTTP), not through MCP routing.
os.environ.setdefault("URSA_OSCAR_MCP_BEARER_TOKEN", "test-static")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_ID", "test-client")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET", "test-secret")
os.environ.setdefault("URSA_OSCAR_MCP_BASE_URL", "https://test.local")
os.environ.setdefault("URSA_OSCAR_MCP_INTERNAL_URL", _TEST_API_URL)


# Make the backend AND mcp-server packages importable. The MCP container's
# runtime has its own venv; in test we just rely on Python path manipulation
# so we don't have to pip-install ursa-oscar-mcp (which pins fastmcp 3.2.4
# + starlette 1.0.0 — incompatible with FastAPI's starlette range, would
# clobber the backend test environment).
_BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
_BACKEND_SRC = _BACKEND_ROOT / "src"
_MCP_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (_BACKEND_SRC, _BACKEND_ROOT, _MCP_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# -------------------------------------------------------------------------
# Session fixture — boots the API, seeds the DB, tears down on exit.
# -------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def api_server():
    """Boot the URSA-OSCAR API in a background thread, seeded with the
    canonical 4-night regression fixture. Yields the API URL string;
    tears down on session exit."""
    from ursa_oscar.ingestion.importer import import_path
    from ursa_oscar.storage.db import DuckDBManager
    from ursa_oscar.storage.migrations import apply_migrations

    fixture_root = (
        _BACKEND_ROOT / "tests" / "regression" / "fixtures" / "nights" / "oscar-reference"
    )
    if not fixture_root.is_dir():
        pytest.skip(f"regression fixtures not present at {fixture_root}")

    # Seed first (before the API opens the file). The migration runs again
    # when the API boots — that's idempotent so the double-application is
    # safe; it just ensures the test sees the schema version that the
    # current code expects regardless of seed order.
    seeder = DuckDBManager(_TEST_DB_PATH, read_only=False)
    apply_migrations(seeder)
    import_path(fixture_root, seeder, skip_existing=False)
    seeder.close()

    # Start the API.
    import uvicorn

    from ursa_oscar.main import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=_TEST_PORT,
        log_level="warning",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run,
        daemon=True,
        name="ursa-oscar-api-test",
    )
    thread.start()

    # Wait for readiness — poll /healthz until 200 or timeout.
    deadline = time.time() + 30.0
    last_err: Exception | None = None
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{_TEST_API_URL}/healthz", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception as e:
            last_err = e
        time.sleep(0.1)
    if not ready:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError(
            f"Test API didn't become ready at {_TEST_API_URL} within 30s; "
            f"last error: {last_err!r}"
        )

    yield _TEST_API_URL

    # Teardown — signal exit and join. The 30s join cap matches the
    # ImportWorker.stop() timeout; if the worker hangs the daemon
    # thread dies with the process and we don't block forever.
    server.should_exit = True
    thread.join(timeout=30.0)

    try:
        if _TEST_DB_PATH.exists():
            _TEST_DB_PATH.unlink()
        wal = _TEST_DB_PATH.with_suffix(_TEST_DB_PATH.suffix + ".wal")
        if wal.exists():
            wal.unlink()
    except Exception:
        pass  # best-effort cleanup; Windows file locks can foil this
