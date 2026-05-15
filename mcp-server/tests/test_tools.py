"""MCP tool functional tests.

Phase 5 Ticket 0 — unblocked (was previously ``pytest.mark.skip`` waiting
for a fixture that booted the API). The conftest in this directory now
spins up the real URSA-OSCAR API in a background thread (seeded with the
4-night regression fixture) and points the MCP client at it via
``URSA_OSCAR_API_URL``. Each test calls the tool function directly; the
tool's internal ``api_get`` / ``api_post`` reach the in-process API
exactly the way the production container would over kairos-net.

These tests cover the {ok, data, ...} envelope contract + the canonical
event-count and percentile values for the 4-night fixture (see
``backend/tests/regression/canonical_targets.py``).
"""
from __future__ import annotations


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
    """Phase 5 Ticket 0 — relaxed from exact-length-4 to canonical-subset
    semantics. Fixture set has grown across phases (Phase 2 + 3 + 4 added
    nights for various regression cases); the canonical 4 must still be
    present and 2026-05-07 must be the earliest. Matches the pattern
    backend/tests/integration/test_api_endpoints.py already uses."""
    from ursa_oscar_mcp.tools.list_nights import list_available_nights

    res = list_available_nights()
    assert res["ok"] is True
    nights = res["data"]["nights"]
    assert len(nights) >= 4
    dates = [n["date"] for n in nights]
    assert {"2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10"}.issubset(set(dates))
    assert dates[0] == "2026-05-07"  # earliest first


def test_list_available_nights_with_filter():
    """Same canonical-subset relaxation: 5/9 (7.376) and 5/10 (3.129) MUST
    qualify under AHI<10; any later fixture nights with AHI<10 are also
    accepted (the importer's regression target set has grown across
    phases). The original strict-equality check fails when the fixture
    contains a 5th low-AHI night."""
    from ursa_oscar_mcp.tools.list_nights import list_available_nights

    res = list_available_nights(filter_expression="AHI < 10")
    assert res["ok"] is True
    nights = res["data"]["nights"]
    dates = {n["date"] for n in nights}
    assert {"2026-05-09", "2026-05-10"}.issubset(dates)
    # Confirm 5/8 (which has AHI ~24) is NOT in the filtered set — sanity
    # check that the filter is actually applied.
    assert "2026-05-08" not in dates
