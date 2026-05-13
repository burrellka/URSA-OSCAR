"""Smoke test for the storage layer: schema applies, repositories roundtrip."""
from __future__ import annotations

from datetime import date, datetime

from ursa_oscar.models.domain import NightlyEvent, NightlySummary
from ursa_oscar.storage.migrations import SCHEMA_VERSION, current_version
from ursa_oscar.storage.repositories import events, nights, timeseries


def test_migrations_record_version(temp_db) -> None:
    assert current_version(temp_db) == SCHEMA_VERSION


def test_nights_upsert_and_get(temp_db) -> None:
    n = NightlySummary(
        date=date(2026, 5, 7),
        session_count=3,
        total_time_minutes=409,
        total_ahi=11.736,
        median_pressure=6.96,
        p95_pressure=8.82,
    )
    nights.upsert(temp_db, n)
    fetched = nights.get_by_date(temp_db, date(2026, 5, 7))
    assert fetched is not None
    assert fetched.session_count == 3
    assert fetched.total_ahi == 11.736
    assert fetched.median_pressure == 6.96

    # Idempotent overwrite
    n2 = n.model_copy(update={"total_ahi": 11.737})
    nights.upsert(temp_db, n2)
    fetched2 = nights.get_by_date(temp_db, date(2026, 5, 7))
    assert fetched2.total_ahi == 11.737


def test_nights_list_in_range(temp_db) -> None:
    for d in [date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9)]:
        nights.upsert(temp_db, NightlySummary(date=d, total_ahi=5.0))
    res = nights.list_in_range(temp_db, date(2026, 5, 7), date(2026, 5, 8))
    assert [r.date for r in res] == [date(2026, 5, 7), date(2026, 5, 8)]


def test_events_bulk_insert_and_count(temp_db) -> None:
    evs = [
        NightlyEvent(
            date=date(2026, 5, 8),
            timestamp=datetime(2026, 5, 8, 22, 30, 12),
            event_type="ClearAirway",
            duration_seconds=11.0,
        ),
        NightlyEvent(
            date=date(2026, 5, 8),
            timestamp=datetime(2026, 5, 8, 22, 31, 0),
            event_type="Obstructive",
            duration_seconds=15.0,
        ),
        NightlyEvent(
            date=date(2026, 5, 8),
            timestamp=datetime(2026, 5, 8, 22, 33, 1),
            event_type="ClearAirway",
            duration_seconds=10.0,
        ),
    ]
    n = events.bulk_insert(temp_db, evs)
    assert n == 3
    counts = events.count_for_date(temp_db, date(2026, 5, 8))
    assert counts == {"ClearAirway": 2, "Obstructive": 1}


def test_events_list_for_date_filter(temp_db) -> None:
    evs = [
        NightlyEvent(date=date(2026, 5, 9), timestamp=datetime(2026, 5, 9, 23, 0, 0), event_type="Hypopnea"),
        NightlyEvent(date=date(2026, 5, 9), timestamp=datetime(2026, 5, 9, 23, 1, 0), event_type="ClearAirway"),
    ]
    events.bulk_insert(temp_db, evs)
    only_ca = events.list_for_date(temp_db, date(2026, 5, 9), event_types=["ClearAirway"])
    assert len(only_ca) == 1
    assert only_ca[0].event_type == "ClearAirway"


def test_timeseries_bulk_and_range(temp_db) -> None:
    d = date(2026, 5, 10)
    rows = [
        (d, datetime(2026, 5, 10, 22, 0, 0), 4.0, 4.0),
        (d, datetime(2026, 5, 10, 22, 0, 1), 4.0, 4.0),
        (d, datetime(2026, 5, 10, 22, 0, 2), 4.5, 4.5),
    ]
    inserted = timeseries.bulk_insert(temp_db, "pressure", rows)
    assert inserted == 3
    res = timeseries.range_query(
        temp_db,
        "pressure",
        datetime(2026, 5, 10, 22, 0, 0),
        datetime(2026, 5, 10, 22, 0, 2),
    )
    assert len(res) == 3
    assert res[0][1] == 4.0
    assert res[2][1] == 4.5
