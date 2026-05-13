"""FastAPI smoke tests against the 4-night fixture imported into a temp DB."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import FIXTURE_ROOT


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Build a FastAPI TestClient backed by a temp DuckDB seeded with fixtures.

    Approach: point `URSA_OSCAR_DB_PATH` at a temp file, seed it, reset the
    settings cache, then let the FastAPI lifespan open it. This keeps the
    lifespan-managed DB lifecycle path under test (rather than bypassing it).
    """
    import ursa_oscar.config as _config_mod

    db_file = tmp_path / "api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None  # reset cache

    # Seed the file and close the seeding handle before the lifespan opens it
    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    import_path(FIXTURE_ROOT, seeder)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        yield client

    _config_mod._settings = None  # leave no global state behind


def test_healthz(api_client):
    r = api_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "ursa-oscar-api"


def test_list_nights_returns_canonical_four(api_client):
    """The 4 canonical-targets nights must be present, in date order. Extra
    fixture nights are allowed (the regression set grows organically — see
    canonical_targets.py docstring), so this test asserts the four are a
    subset of the returned list and that dates are sorted ascending."""
    r = api_client.get("/api/v1/nights")
    assert r.status_code == 200
    nights = r.json()
    dates = [n["date"] for n in nights]
    canonical = {"2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10"}
    assert canonical.issubset(set(dates)), (
        f"Missing canonical nights. Got: {dates}, expected ⊇ {canonical}"
    )
    assert dates == sorted(dates), "Nights should be returned in ascending date order"


def test_list_nights_range_filter(api_client):
    r = api_client.get("/api/v1/nights", params={"start": "2026-05-08", "end": "2026-05-09"})
    assert r.status_code == 200
    nights = r.json()
    assert [n["date"] for n in nights] == ["2026-05-08", "2026-05-09"]


def test_get_night_by_date(api_client):
    r = api_client.get("/api/v1/night/2026-05-10")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-05-10"
    # Canonical: AHI 3.129, session_count 2
    assert abs(body["total_ahi"] - 3.129) < 0.01
    assert body["session_count"] == 2


def test_get_night_includes_last_updated(api_client):
    """Phase 3 Item 1B: last_updated must round-trip through the API so
    the Daily View Device Settings card can render 'last imported {ts}'.

    Regression: prior to v0.5.0 the in-memory NightlySummary carried
    last_updated=None which was written as a literal NULL, suppressing
    the column's DEFAULT CURRENT_TIMESTAMP. The fix stamps the timestamp
    server-side at write time. This test guards against a regression of
    that behavior — every imported night must come back with a non-null
    last_updated parseable as an ISO datetime."""
    from datetime import datetime

    r = api_client.get("/api/v1/night/2026-05-10")
    assert r.status_code == 200
    body = r.json()
    assert body.get("last_updated") is not None, (
        "Expected last_updated to round-trip through the API; got None. "
        "Likely cause: nights.upsert wrote NULL instead of stamping the "
        "timestamp server-side, suppressing the column's DEFAULT."
    )
    # Must parse as an ISO datetime — FastAPI serializes datetimes that way.
    parsed = datetime.fromisoformat(body["last_updated"].replace("Z", "+00:00"))
    # And the value must be recent (last 5 minutes), since this api_client
    # fixture re-imports the fixtures fresh at test setup.
    age_seconds = (datetime.now(parsed.tzinfo) - parsed).total_seconds()
    assert 0 <= age_seconds < 300, (
        f"last_updated should be recent (this test just imported); "
        f"got {body['last_updated']} which is {age_seconds:.0f}s old."
    )


def test_get_night_404(api_client):
    r = api_client.get("/api/v1/night/2099-01-01")
    assert r.status_code == 404


def test_list_events_for_5_8(api_client):
    r = api_client.get("/api/v1/events", params={"date": "2026-05-08"})
    assert r.status_code == 200
    events = r.json()
    # Canonical: 47 CA + 28 OA + 2 A + 1 H + 0 RERA = 78 events (excluding LL)
    ca = [e for e in events if e["event_type"] == "ClearAirway"]
    assert len(ca) == 47


def test_list_events_filter_by_type(api_client):
    r = api_client.get(
        "/api/v1/events", params={"date": "2026-05-08", "event_type": "Obstructive"}
    )
    assert r.status_code == 200
    events = r.json()
    assert all(e["event_type"] == "Obstructive" for e in events)
    assert len(events) == 28


def test_manual_logs_stub_returns_501(api_client):
    r = api_client.get("/api/v1/manual-logs")
    assert r.status_code == 501


def test_imports_async_status_404(api_client):
    r = api_client.get("/api/v1/imports/123")
    assert r.status_code == 404


def test_openapi_doc_available(api_client):
    r = api_client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["title"] == "URSA-OSCAR API"
    paths = spec["paths"]
    assert "/healthz" in paths
    assert "/api/v1/nights" in paths
    assert "/api/v1/night/{target_date}" in paths
    assert "/api/v1/events" in paths
    assert "/api/v1/imports" in paths
