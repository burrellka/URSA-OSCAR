"""MCP tool functional tests against a fixture-loaded DuckDB.

These call the tool functions directly (not through SSE) to validate the
envelope shape + data payload against the canonical regression targets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# These tests were written when MCP tools queried DuckDB directly. After the
# v0.1.3 refactor MCP tools call the API container over kairos-net (DuckDB's
# cross-container lock model required this). They now need a running API
# container to exercise. Marked as integration; re-enable when we have a
# fixture that boots the API.
pytestmark = pytest.mark.skip(
    reason="Pending rewrite: tools moved from direct-DuckDB to API-proxy; "
    "need a running API container fixture (tracked for Phase 1.5)."
)


# Set env BEFORE importing the server module (which calls build_auth_provider
# at import time). Use a fixture DB that the importer will populate.
_TMP_DB_PATH = Path(__file__).resolve().parents[1] / "_test_tools.duckdb"
os.environ.setdefault("URSA_OSCAR_MCP_BEARER_TOKEN", "test-static")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_ID", "test-client")
os.environ.setdefault("URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET", "test-secret")
os.environ.setdefault("URSA_OSCAR_MCP_BASE_URL", "https://test.local")
os.environ["URSA_OSCAR_DB_PATH"] = str(_TMP_DB_PATH)


@pytest.fixture(scope="module", autouse=True)
def seed_db():
    """Build the URSA-OSCAR DuckDB once, populate with the 4-night fixture."""
    # Make the backend package importable
    backend_src = Path(__file__).resolve().parents[2] / "backend" / "src"
    sys.path.insert(0, str(backend_src))
    backend_pkg_root = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend_pkg_root))

    from ursa_oscar.ingestion.importer import import_path
    from ursa_oscar.storage.db import DuckDBManager
    from ursa_oscar.storage.migrations import apply_migrations

    fixture_root = backend_pkg_root / "tests" / "regression" / "fixtures" / "nights" / "oscar-reference"
    if _TMP_DB_PATH.exists():
        _TMP_DB_PATH.unlink()
    db = DuckDBManager(_TMP_DB_PATH, read_only=False)
    apply_migrations(db)
    import_path(fixture_root, db)
    db.close()

    yield

    # Cleanup: reset the read-only MCP client + drop the test DB
    from ursa_oscar_mcp.client import close_db
    close_db()
    if _TMP_DB_PATH.exists():
        try:
            _TMP_DB_PATH.unlink()
        except PermissionError:
            pass  # Windows file lock; harmless during teardown


def test_get_nightly_summary_single_date():
    from ursa_oscar_mcp.tools.nightly_summary import get_nightly_summary

    res = get_nightly_summary(date="2026-05-10")
    assert res["ok"] is True
    data = res["data"]
    assert data["date"] == "2026-05-10"
    assert data["session_count"] == 2
    assert abs(data["total_ahi"] - 3.129) < 0.01


def test_get_nightly_summary_404():
    from ursa_oscar_mcp.tools.nightly_summary import get_nightly_summary

    res = get_nightly_summary(date="2099-01-01")
    assert res["ok"] is False
    assert res["code"] == "NOT_FOUND"


def test_get_nightly_summary_invalid_date():
    from ursa_oscar_mcp.tools.nightly_summary import get_nightly_summary

    res = get_nightly_summary(date="not-a-date")
    assert res["ok"] is False
    assert res["code"] == "INVALID_INPUT"


def test_get_nightly_summary_range():
    from ursa_oscar_mcp.tools.nightly_summary import get_nightly_summary

    res = get_nightly_summary(date="2026-05-08", end_date="2026-05-10")
    assert res["ok"] is True
    data = res["data"]
    assert isinstance(data, list) and len(data) == 3
    assert [d["date"] for d in data] == ["2026-05-08", "2026-05-09", "2026-05-10"]


def test_get_ahi_breakdown_5_8():
    from ursa_oscar_mcp.tools.ahi_breakdown import get_ahi_breakdown

    res = get_ahi_breakdown(date="2026-05-08")
    assert res["ok"] is True
    data = res["data"]
    assert data["central"]["count"] == 47
    assert data["obstructive"]["count"] == 28
    assert data["hypopnea"]["count"] == 1
    assert data["apnea"]["count"] == 2
    # 5/8 has a high central-vs-obstructive ratio
    interp = data["interpretation"]
    assert interp["tecsa_likely"] is True


def test_get_event_distribution_by_hour():
    from ursa_oscar_mcp.tools.event_distribution import get_event_distribution_by_hour

    res = get_event_distribution_by_hour(date="2026-05-08")
    assert res["ok"] is True
    data = res["data"]
    assert data["date"] == "2026-05-08"
    assert isinstance(data["hours"], list) and len(data["hours"]) > 0
    # 5/8 has events spanning ~23:48 → 04:54, so hours should include both
    # late-evening and post-midnight buckets
    hours_present = {h["hour"] for h in data["hours"]}
    assert any(h >= 23 or h < 5 for h in hours_present)


def test_get_event_distribution_filter():
    from ursa_oscar_mcp.tools.event_distribution import get_event_distribution_by_hour

    res = get_event_distribution_by_hour(
        date="2026-05-08", event_types=["ClearAirway"]
    )
    assert res["ok"] is True
    # Only CA events should appear in the per-hour breakdown
    for hr in res["data"]["hours"]:
        assert set(hr["counts"].keys()) <= {"ClearAirway"}


def test_get_pressure_profile_5_7():
    from ursa_oscar_mcp.tools.pressure_profile import get_pressure_profile

    res = get_pressure_profile(date="2026-05-07")
    assert res["ok"] is True
    data = res["data"]
    assert data["median_pressure"] is not None
    assert abs(data["median_pressure"] - 6.96) < 0.05


def test_get_leak_profile():
    from ursa_oscar_mcp.tools.leak_profile import get_leak_profile

    res = get_leak_profile(date="2026-05-10")
    assert res["ok"] is True
    data = res["data"]
    assert "median_leak" in data
    assert "minutes_over_redline" in data
    assert "interpretation" in data
    assert data["interpretation"]["seal_quality"] in {"good", "marginal", "poor"}


def test_get_session_breakdown_5_10():
    from ursa_oscar_mcp.tools.session_breakdown import get_session_breakdown

    res = get_session_breakdown(date="2026-05-10")
    assert res["ok"] is True
    data = res["data"]
    # 5/10 has 2 sessions
    assert len(data["sessions"]) == 2
    total = sum(s["total_events"] for s in data["sessions"])
    assert total >= 17  # CA + OA + H + RERA + maybe LL


def test_list_available_nights():
    from ursa_oscar_mcp.tools.list_nights import list_available_nights

    res = list_available_nights()
    assert res["ok"] is True
    nights = res["data"]["nights"]
    assert len(nights) == 4
    assert nights[0]["date"] == "2026-05-07"


def test_list_available_nights_with_filter():
    from ursa_oscar_mcp.tools.list_nights import list_available_nights

    res = list_available_nights(filter_expression="AHI < 10")
    assert res["ok"] is True
    nights = res["data"]["nights"]
    # 5/9 (7.376) and 5/10 (3.129) qualify
    assert {n["date"] for n in nights} == {"2026-05-09", "2026-05-10"}
