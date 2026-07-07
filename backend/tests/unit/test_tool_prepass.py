"""Unit tests for the lexical tool pre-pass (1.1.12 slice 3).

These tests are dependency-free (no API, no LLM) so they run in
milliseconds. They cover the pure-function behavior:
  - Strong-intent messages activate the right groups
  - Generic verbs (get/list/compare) alone don't over-match
  - The MAX_PREPASS_GROUPS cap holds
  - Empty catalog / empty message → no activation
  - Deterministic ordering on score ties
"""
from __future__ import annotations

from ursa_oscar.ai_proxy.tool_index import build_tool_index
from ursa_oscar.ai_proxy.tool_prepass import (
    MAX_PREPASS_GROUPS,
    select_prepass_groups,
)


def _desc(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def _catalog(grouped: dict[str, list[dict]], labels: dict[str, str] | None = None):
    """Build a catalog for the pre-pass tests using group keys as
    default labels when the test doesn't specify."""
    labels = labels or {k: k.replace("-", " ").title() for k in grouped}
    return build_tool_index(grouped, labels)


def test_prepass_empty_message_returns_nothing():
    cat = _catalog({
        "analytics": [_desc("get_ahi_breakdown", "AHI decomposition per event type")],
    })
    assert select_prepass_groups("", cat) == []


def test_prepass_empty_catalog_returns_nothing():
    from ursa_oscar.ai_proxy.tool_index import build_tool_index
    empty = build_tool_index({}, {})
    assert select_prepass_groups("show me the AHI", empty) == []


def test_prepass_strong_intent_activates_matching_group():
    """A specific intent noun in the query should land on the matching
    group via label / name / description hit. "AHI trends" legitimately
    signals BOTH the analytics group (for AHI) and the trends group,
    so both should activate — the pre-pass isn't required to be
    surgical, just to catch the obvious cases without over-loading."""
    cat = _catalog({
        "trends": [
            _desc("get_trend", "Trend analysis for a metric over time"),
            _desc("compare_periods", "Compare two date ranges"),
        ],
        "analytics": [
            _desc("get_ahi_breakdown", "AHI decomposition by event type"),
        ],
    })
    got = select_prepass_groups("show me my AHI trends", cat)
    assert "trends" in got
    assert "analytics" in got  # "AHI" hits this group directly

    # A more targeted query that doesn't mention AHI should NOT load
    # the analytics group. Just trends.
    got_narrow = select_prepass_groups("compare my sleep trends over time", cat)
    assert got_narrow == ["trends"]


def test_prepass_ignores_generic_verbs():
    """URSA's stopword list adds the generic verbs URSA's own tools are
    named after (compare/analyze/show/list/etc). A query that's just
    "compare stuff" without an intent noun should NOT activate every
    group whose tool names start with those verbs."""
    cat = _catalog({
        "trends": [_desc("compare_periods", "compare two periods")],
        "analytics": [_desc("get_ahi_breakdown", "get the ahi breakdown")],
    })
    # "compare" is a stopword; "list" is a stopword; "show" is a stopword.
    # After filtering, nothing intent-carrying remains.
    got = select_prepass_groups("compare list show", cat)
    assert got == [], f"Generic verbs alone should not activate groups; got {got}"


def test_prepass_respects_max_groups_cap():
    """Even with a query that hits many groups, MAX_PREPASS_GROUPS caps
    the activation. Prevents silently reintroducing the tool tax."""
    cat = _catalog({
        "trends":            [_desc("get_trend", "trend")],
        "analytics":         [_desc("get_ahi_breakdown", "analytics")],
        "advanced-analysis": [_desc("analyze_correlation", "advanced-analysis")],
        "reports":           [_desc("generate_report", "reports")],
    })
    # Every group's own key hits in the query — each qualifies.
    got = select_prepass_groups(
        "trend analytics advanced-analysis reports",
        cat,
    )
    assert len(got) <= MAX_PREPASS_GROUPS


def test_prepass_ranking_prefers_direct_hits_over_description():
    """A direct label/name hit scores 3× a description hit. If a query
    lightly touches two groups by description and heavily one by name,
    the name-hit group should come first."""
    cat = _catalog({
        "trends": [
            _desc("get_trend", "This tool works with pressure and leak data"),
        ],
        "analytics": [
            _desc("get_pressure_profile", "Pressure profile decomposition"),
        ],
    })
    # "pressure" is in trends' description AND in analytics' name.
    # Analytics wins on the direct name hit.
    got = select_prepass_groups("what's my pressure like", cat)
    assert got[0] == "analytics"


def test_prepass_deterministic_on_ties():
    """Two groups tied on score should sort alphabetically by key so
    the same query always returns the same order. Helpful for
    reproducible conversation logs."""
    cat = _catalog({
        "zebra":  [_desc("zebra_tool", "some description")],
        "alpha":  [_desc("alpha_tool", "some description")],
    })
    # Only "description" is in both — same score, ordering must be stable.
    got1 = select_prepass_groups("description", cat)
    got2 = select_prepass_groups("description", cat)
    assert got1 == got2
    # Alpha before Zebra on tie.
    if len(got1) >= 2:
        assert got1[0] == "alpha"
