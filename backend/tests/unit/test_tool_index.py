"""Unit tests for the progressive tool disclosure catalog + index render.

These tests are dependency-free (no API server, no DB) so they run in
milliseconds. They cover the pure-function behavior of tool_index —
catalog construction, index rendering, and the load_tools resolver.

The chat-loop wiring (which mutates active_tools on a load_tools call)
is covered separately in the integration suite.
"""
from __future__ import annotations

from ursa_oscar.ai_proxy.tool_index import (
    LOAD_TOOLS_NAME,
    build_tool_index,
    format_load_result,
)


def _descriptor(name: str, description: str = "test tool") -> dict:
    """Build an OpenAI-shape descriptor for a test tool."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def test_load_tools_name_constant_matches_spec():
    """Both the tool descriptor (in tools.py) and the chat loop reference
    this exact string. Guard against silent drift."""
    assert LOAD_TOOLS_NAME == "load_tools"


def test_catalog_is_empty_when_all_tools_are_core():
    """When there's nothing deferred, build_tool_index returns an empty
    catalog and callers should NOT inject an empty index block."""
    cat = build_tool_index({}, {})
    assert cat.is_empty
    assert cat.index_text == ""


def test_catalog_builds_groups_and_index_text():
    """Catalog carries every deferred descriptor keyed by name; groups
    map to member lists; index_text renders one line per group with the
    group key + label + comma-separated tool names."""
    grouped = {
        "analytics": [_descriptor("get_ahi_breakdown"), _descriptor("get_pressure_profile")],
        "trends":    [_descriptor("compare_periods"), _descriptor("get_trend")],
    }
    labels = {"analytics": "Per-night analytics", "trends": "Multi-night trends"}

    cat = build_tool_index(grouped, labels)
    assert not cat.is_empty
    assert set(cat.descriptors_by_name.keys()) == {
        "get_ahi_breakdown", "get_pressure_profile", "compare_periods", "get_trend",
    }
    assert cat.groups["analytics"] == ["get_ahi_breakdown", "get_pressure_profile"]
    assert cat.groups["trends"] == ["compare_periods", "get_trend"]

    text = cat.index_text
    assert "## AVAILABLE TOOLS (inactive — load before use)" in text
    assert "[group: analytics]" in text
    assert "Per-night analytics" in text
    assert "[group: trends]" in text
    assert "Multi-night trends" in text
    assert "get_ahi_breakdown" in text
    assert "compare_periods" in text


def test_catalog_index_truncates_long_groups():
    """Groups larger than the display cap get a trailing "(+N more)"
    marker so the index stays scannable without hiding the true size."""
    long_group = [_descriptor(f"tool_{i:02d}") for i in range(15)]
    cat = build_tool_index({"big": long_group}, {"big": "Big Group"})
    text = cat.index_text
    assert "tool_00" in text
    assert "(+5 more)" in text
    # But descriptors_by_name still contains every tool for the resolver.
    assert len(cat.descriptors_by_name) == 15


def test_resolve_by_group_key_returns_all_members():
    grouped = {
        "analytics": [_descriptor("get_ahi_breakdown"), _descriptor("get_pressure_profile")],
    }
    cat = build_tool_index(grouped, {"analytics": "Analytics"})

    res = cat.resolve(groups=["analytics"])
    assert set(res.loaded_names) == {"get_ahi_breakdown", "get_pressure_profile"}
    assert res.unknown == []
    assert len(res.descriptors) == 2


def test_resolve_by_name_returns_exact_tool():
    grouped = {
        "analytics": [_descriptor("get_ahi_breakdown"), _descriptor("get_pressure_profile")],
    }
    cat = build_tool_index(grouped, {"analytics": "Analytics"})

    res = cat.resolve(names=["get_ahi_breakdown"])
    assert res.loaded_names == ["get_ahi_breakdown"]
    assert res.unknown == []


def test_resolve_unknown_group_and_name_reported_not_ignored():
    """The model needs to see what it asked for that didn't resolve so it
    can correct itself instead of retrying the same wrong string."""
    grouped = {"analytics": [_descriptor("get_ahi_breakdown")]}
    cat = build_tool_index(grouped, {"analytics": "Analytics"})

    res = cat.resolve(names=["nonexistent"], groups=["also_bad"])
    assert res.loaded_names == []
    assert "nonexistent" in res.unknown
    assert "group:also_bad" in res.unknown


def test_resolve_dedupes_when_group_and_name_overlap():
    """Loading a group AND asking for a name that's in the group must
    yield one entry, not two — else the adapter re-ships the same
    schema next turn."""
    grouped = {"analytics": [_descriptor("get_ahi_breakdown")]}
    cat = build_tool_index(grouped, {"analytics": "Analytics"})

    res = cat.resolve(groups=["analytics"], names=["get_ahi_breakdown"])
    assert res.loaded_names == ["get_ahi_breakdown"]
    assert len(res.descriptors) == 1


def test_resolve_group_key_is_case_insensitive():
    grouped = {"analytics": [_descriptor("get_ahi_breakdown")]}
    cat = build_tool_index(grouped, {"analytics": "Analytics"})

    res = cat.resolve(groups=["ANALYTICS"])
    assert set(res.loaded_names) == {"get_ahi_breakdown"}


def test_format_load_result_confirms_activation():
    from ursa_oscar.ai_proxy.tool_index import ResolveResult
    r = ResolveResult(
        descriptors=[_descriptor("a"), _descriptor("b")],
        loaded_names=["a", "b"],
        unknown=[],
    )
    msg = format_load_result(r)
    assert "Activated 2 tool(s)" in msg
    assert "a" in msg and "b" in msg


def test_format_load_result_flags_only_unknown_when_nothing_matched():
    from ursa_oscar.ai_proxy.tool_index import ResolveResult
    r = ResolveResult(descriptors=[], loaded_names=[], unknown=["foo", "group:bar"])
    msg = format_load_result(r)
    assert "No tools matched" in msg
    assert "foo" in msg
    assert "bar" in msg
