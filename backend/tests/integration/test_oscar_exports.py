"""Regression coverage for the OSCAR-compatible CSV export endpoints
(0.9.7). Locks down the column layout so downstream OSCAR-aware tools
(SleepHQ, oscar-parity scripts) keep parsing the output cleanly.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from ursa_oscar.ingestion.importer import import_path
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations
from tests.conftest import FIXTURE_ROOT


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "exports.duckdb"
    exports_dir = tmp_path / "exports"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    monkeypatch.setenv("URSA_OSCAR_EXPORTS_PATH", str(exports_dir))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    import_path(FIXTURE_ROOT, seeder)
    seeder.close()

    app = create_app()
    with TestClient(app) as client:
        yield client, exports_dir

    _config_mod._settings = None


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    assert rows, "CSV had no rows at all"
    return rows[0], rows[1:]


# -----------------------------------------------------------------------
# Column-layout regression — these are the OSCAR shapes byte-for-byte.
# -----------------------------------------------------------------------

OSCAR_SUMMARY_HEADER = [
    "Date", "Session Count", "Start", "End", "Total Time", "AHI",
    "CA Count", "A Count", "OA Count", "H Count", "UA Count",
    "VS Count", "VS2 Count", "RE Count", "FL Count", "SA Count",
    "NR Count", "EP Count", "LF Count", "UF1 Count", "UF2 Count", "PP Count",
    "Median Pressure", "Median Pressure Set",
    "Median IPAP", "Median IPAP Set",
    "Median EPAP", "Median EPAP Set", "Median Flow Limit.",
    "95% Pressure", "95% Pressure Set",
    "95% IPAP", "95% IPAP Set",
    "95% EPAP", "95% EPAP Set", "95% Flow Limit.",
    "99.5% Pressure", "99.5% Pressure Set",
    "99.5% IPAP", "99.5% IPAP Set",
    "99.5% EPAP", "99.5% EPAP Set", "99.5% Flow Limit.",
]
OSCAR_SESSIONS_HEADER = (
    ["Date", "Session"] + OSCAR_SUMMARY_HEADER[2:]
)
OSCAR_DAILY_HEADER = ["DateTime", "Session", "Event", "Data/Duration"]


def test_summary_header_matches_oscar(api_client):
    """OSCAR Summary's first row, byte-for-byte. Adding a column or
    reordering would break SleepHQ + the operator's own scripts."""
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-07", "end_date": "2026-05-10"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="URSA-OSCAR_Summary_2026-05-07_to_2026-05-10.csv"' in (
        r.headers["content-disposition"]
    )
    header, rows = _parse_csv(r.text)
    assert header == OSCAR_SUMMARY_HEADER
    assert len(rows) == 4  # 4-night fixture


def test_summary_single_day_filename_no_range_suffix(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    assert r.status_code == 200
    assert 'filename="URSA-OSCAR_Summary_2026-05-08.csv"' in (
        r.headers["content-disposition"]
    )
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1


def test_summary_default_range_is_most_recent_day(api_client):
    """No date params -> latest night only. Matches OSCAR's
    'Most Recent Day' default."""
    client, _ = api_client
    # Discover the true latest date from /nights so this test stays
    # robust to fixture changes.
    nights = client.get("/api/v1/nights").json()
    latest = max(n["date"] for n in nights)

    r = client.get("/api/v1/exports/oscar/summary.csv")
    assert r.status_code == 200
    _, rows = _parse_csv(r.text)
    assert len(rows) == 1, "default range should return exactly one row"
    assert rows[0][0] == latest, (
        f"default-range row should be latest night ({latest}), "
        f"got {rows[0][0]}"
    )
    assert f'filename="URSA-OSCAR_Summary_{latest}.csv"' in (
        r.headers["content-disposition"]
    )


def test_summary_total_time_is_hhmmss_format(api_client):
    """OSCAR uses HH:MM:SS in the Total Time column. Operator's scripts
    parse it that way."""
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-07", "end_date": "2026-05-10"},
    )
    _, rows = _parse_csv(r.text)
    total_time_col = OSCAR_SUMMARY_HEADER.index("Total Time")
    for row in rows:
        tt = row[total_time_col]
        assert len(tt) == 8 and tt[2] == ":" and tt[5] == ":", (
            f"Total Time should be HH:MM:SS, got {tt!r}"
        )


def test_summary_zero_fills_untracked_event_columns(api_client):
    """URA-OSCAR doesn't track UA/VS/VS2/SA/NR/EP/UF1/UF2 events.
    Those columns must be present and zero-filled (per 0.9.7 decision)
    so the layout stays drop-in OSCAR-compatible."""
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    header, rows = _parse_csv(r.text)
    row = rows[0]
    for untracked in ("UA Count", "VS Count", "VS2 Count", "SA Count",
                      "NR Count", "EP Count", "UF1 Count", "UF2 Count"):
        idx = header.index(untracked)
        assert row[idx] == "0", (
            f"{untracked} should zero-fill (untracked), got {row[idx]!r}"
        )


def test_summary_zero_fills_untracked_pressure_set_columns(api_client):
    """The 'Set' / 'IPAP' / 'Flow Limit.' pressure stats are also
    zero-filled — URSA tracks Median/95%/99.5% × Pressure & EPAP only."""
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    header, rows = _parse_csv(r.text)
    row = rows[0]
    for untracked in ("Median Pressure Set", "Median IPAP", "Median IPAP Set",
                      "Median EPAP Set", "Median Flow Limit.",
                      "95% Pressure Set", "95% IPAP", "95% IPAP Set",
                      "95% EPAP Set", "95% Flow Limit.",
                      "99.5% Pressure Set", "99.5% IPAP", "99.5% IPAP Set",
                      "99.5% EPAP Set", "99.5% Flow Limit."):
        idx = header.index(untracked)
        assert row[idx] == "0", (
            f"{untracked} should zero-fill (untracked), got {row[idx]!r}"
        )


# -----------------------------------------------------------------------
# Sessions endpoint
# -----------------------------------------------------------------------


def test_sessions_header_matches_oscar(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/sessions.csv",
        params={"start_date": "2026-05-07", "end_date": "2026-05-10"},
    )
    assert r.status_code == 200
    header, rows = _parse_csv(r.text)
    assert header == OSCAR_SESSIONS_HEADER
    # Each night has >= 1 session.
    assert len(rows) >= 4


def test_sessions_filename_convention(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/sessions.csv",
        params={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    assert 'filename="URSA-OSCAR_Sessions_2026-05-08.csv"' in (
        r.headers["content-disposition"]
    )


# -----------------------------------------------------------------------
# Daily endpoint
# -----------------------------------------------------------------------


def test_daily_header_matches_oscar(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/daily.csv",
        params={"start_date": "2026-05-07", "end_date": "2026-05-10"},
    )
    assert r.status_code == 200
    header, rows = _parse_csv(r.text)
    assert header == OSCAR_DAILY_HEADER
    # The fixture has events on each night — at least a few rows.
    assert len(rows) > 0


def test_daily_data_duration_has_two_decimals(api_client):
    """OSCAR's Data/Duration column is always 2-decimal float text.
    Parsers downstream assume that shape."""
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/daily.csv",
        params={"start_date": "2026-05-08", "end_date": "2026-05-08"},
    )
    _, rows = _parse_csv(r.text)
    assert rows, "Expected at least one event row on 2026-05-08"
    for row in rows:
        duration_text = row[3]
        assert "." in duration_text and duration_text.split(".")[-1] == "00" or len(
            duration_text.split(".")[-1]
        ) == 2, f"Data/Duration not 2dp: {duration_text!r}"


# -----------------------------------------------------------------------
# Server-save endpoint
# -----------------------------------------------------------------------


def test_server_save_writes_csv_to_exports_path(api_client):
    client, exports_dir = api_client
    r = client.post(
        "/api/v1/exports/oscar/server",
        json={
            "export_type": "summary",
            "start_date": "2026-05-07",
            "end_date": "2026-05-10",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "URSA-OSCAR_Summary_2026-05-07_to_2026-05-10.csv"
    assert body["rows"] == 4  # 4 nights
    assert body["bytes"] > 0
    written = exports_dir / body["filename"]
    assert written.exists(), f"server didn't actually write to {written}"
    # Verify the on-disk file parses cleanly and starts with the OSCAR header.
    text = written.read_text(encoding="utf-8")
    header, rows = _parse_csv(text)
    assert header == OSCAR_SUMMARY_HEADER
    assert len(rows) == 4


def test_server_save_with_no_dates_uses_most_recent_day(api_client):
    client, exports_dir = api_client
    nights = client.get("/api/v1/nights").json()
    latest = max(n["date"] for n in nights)
    r = client.get("/api/v1/exports/oscar/summary.csv")  # warm up + verify
    assert r.status_code == 200

    r = client.post(
        "/api/v1/exports/oscar/server",
        json={"export_type": "daily"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == f"URSA-OSCAR_Daily_{latest}.csv"
    written = exports_dir / body["filename"]
    assert written.exists()


def test_server_save_rejects_invalid_type(api_client):
    client, _ = api_client
    r = client.post(
        "/api/v1/exports/oscar/server",
        json={"export_type": "trend"},  # not a real export type
    )
    # Pydantic validation rejects the Literal mismatch -> 422.
    assert r.status_code == 422


# -----------------------------------------------------------------------
# Range validation
# -----------------------------------------------------------------------


def test_range_validation_inverted_bounds(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-10", "end_date": "2026-05-07"},
    )
    assert r.status_code == 400
    assert "on or after" in r.json()["detail"]


def test_range_validation_only_one_date(api_client):
    client, _ = api_client
    r = client.get(
        "/api/v1/exports/oscar/summary.csv",
        params={"start_date": "2026-05-08"},
    )
    assert r.status_code == 400
