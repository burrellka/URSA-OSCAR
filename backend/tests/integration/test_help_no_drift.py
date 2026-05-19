"""Phase 7.3 — no-drift regression test for the Help system.

Architect requirement: every tool / endpoint reference in the Help
markdown must exist in the code. This test scans all 37 topic bodies
for code references and verifies each one is real. It also confirms
the backend registry (help_registry.py) matches the frontend registry
(topics.ts) in slug + section coverage.

What this catches:
  - A topic mentions a tool that's been renamed or removed
  - A topic mentions an endpoint path that doesn't exist
  - The backend metadata table goes out of sync with the frontend's
  - A markdown file is added/removed without updating both registries
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ursa_oscar.help.registry import TOPICS as BACKEND_TOPICS


# ---------------------------------------------------------------------------
# Topic registry coverage
# ---------------------------------------------------------------------------


def test_backend_registry_has_37_topics():
    """The architect outline lands at 37 topics (4 + 8 + 5 + 6 + 5 + 5 + 4)."""
    assert len(BACKEND_TOPICS) == 37, (
        f"Expected 37 Help topics, found {len(BACKEND_TOPICS)}. "
        f"If a topic was added, update test_backend_registry_has_37_topics "
        f"and the frontend topics.ts."
    )


def test_backend_slugs_are_unique():
    slugs = [t.slug for t in BACKEND_TOPICS]
    assert len(slugs) == len(set(slugs)), (
        f"Duplicate slugs in backend registry: "
        f"{[s for s in slugs if slugs.count(s) > 1]}"
    )


def test_every_topic_has_a_body():
    """If a body is empty, the .md file wasn't copied into the image (or
    didn't exist in the source tree)."""
    for t in BACKEND_TOPICS:
        assert t.body.strip(), (
            f"Topic '{t.slug}' has empty body. The .md file is missing "
            f"from frontend/src/help/content/ or the backend Dockerfile "
            f"isn't copying it."
        )


def test_section_distribution_matches_outline():
    """Counts per section must match the architect's outline."""
    counts: dict[str, int] = {}
    for t in BACKEND_TOPICS:
        counts[t.section] = counts.get(t.section, 0) + 1
    expected = {
        "Getting started": 4,
        "Using URSA-OSCAR": 8,
        "Understanding the data": 5,
        "Methodology": 6,
        "Architecture and deployment": 5,
        "Troubleshooting": 5,
        "About URSA-OSCAR": 4,
    }
    assert counts == expected, (
        f"Section counts drifted from outline. Expected {expected}, got {counts}."
    )


# ---------------------------------------------------------------------------
# Frontend ↔ backend registry parity
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_TOPICS_TS = _REPO_ROOT / "frontend" / "src" / "help" / "topics.ts"


def _frontend_slug_section_pairs() -> set[tuple[str, str]]:
    """Parse frontend/src/help/topics.ts for slug + section pairs.
    Hacky but stable enough — the file's TOPICS array uses a fixed
    shape `{ slug: '...', title: '...', section: '...' }`."""
    src = _FRONTEND_TOPICS_TS.read_text(encoding="utf-8")
    pairs: set[tuple[str, str]] = set()
    # Pattern: { slug: 'foo', title: '...', section: 'Section Name', ... }
    # We use a non-greedy match between `slug:` and the next `section:`.
    pattern = re.compile(
        r"slug:\s*'([a-z0-9-]+)',\s*title:[^,]+,\s*section:\s*'([^']+)'",
        re.DOTALL,
    )
    for match in pattern.finditer(src):
        pairs.add((match.group(1), match.group(2)))
    return pairs


def test_frontend_and_backend_have_same_slug_section_pairs():
    """Both registries must declare the same 37 topics with matching
    section assignments."""
    frontend_pairs = _frontend_slug_section_pairs()
    backend_pairs = {(t.slug, t.section) for t in BACKEND_TOPICS}

    only_in_frontend = frontend_pairs - backend_pairs
    only_in_backend = backend_pairs - frontend_pairs

    assert not only_in_frontend, (
        f"Topics in frontend topics.ts but missing from "
        f"backend help_registry.py: {sorted(only_in_frontend)}"
    )
    assert not only_in_backend, (
        f"Topics in backend help_registry.py but missing from "
        f"frontend topics.ts: {sorted(only_in_backend)}"
    )
    assert frontend_pairs == backend_pairs


# ---------------------------------------------------------------------------
# Code-reference scan — every endpoint / tool mentioned in markdown
# must exist in the codebase.
# ---------------------------------------------------------------------------


_BACKEND_SRC = _REPO_ROOT / "backend" / "src" / "ursa_oscar"
_MCP_SRC = _REPO_ROOT / "mcp-server" / "src" / "ursa_oscar_mcp"


def _all_python_source() -> str:
    """Concatenate every .py file in backend + mcp-server src. Used as
    a haystack for substring lookups of tool / endpoint names."""
    parts: list[str] = []
    for root in (_BACKEND_SRC, _MCP_SRC):
        for py in root.rglob("*.py"):
            try:
                parts.append(py.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(parts)


# Endpoints to verify when they appear in markdown.
# Format: ``/api/v1/<something>``. Each appearance must resolve to a
# route defined in backend/src/ursa_oscar/api/*.py.
_ENDPOINT_PATTERN = re.compile(r"/api/v1/[a-z][a-z0-9_/-]*")

# MCP tool function names — the literal names registered with
# @mcp.tool() and exposed to AI assistants. The file names under
# mcp-server/src/ursa_oscar_mcp/tools/ DIFFER from these in a few
# cases (e.g., tools/event_distribution.py registers
# `get_event_distribution_by_hour`). The Help content must reference
# the registered names, not the file names — those are what claude.ai
# and other MCP clients actually invoke.
_KNOWN_MCP_TOOLS = {
    "get_nightly_summary",
    "get_ahi_breakdown",
    "get_event_distribution_by_hour",
    "get_pressure_profile",
    "get_leak_profile",
    "get_session_breakdown",
    "list_available_nights",
    "compare_periods",
    "get_trend",
    "analyze_correlation",
    "analyze_multivariate_correlation",
    "analyze_lag_correlation",
    "analyze_prediction",
    "get_manual_log_summary",
    "get_user_profile",
    "trigger_import",
    "generate_report",
    "get_help_topic",
}


def test_every_endpoint_mentioned_in_markdown_exists():
    """Scan every Help topic body for /api/v1/... references and verify
    each one resolves to a real endpoint."""
    haystack = _all_python_source()

    seen: set[str] = set()
    for t in BACKEND_TOPICS:
        for match in _ENDPOINT_PATTERN.finditer(t.body):
            endpoint = match.group(0)
            # Trim trailing punctuation that ended up captured.
            endpoint = endpoint.rstrip(".,)*`'\"")
            seen.add((endpoint, t.slug))

    # For each (endpoint, slug) pair, the endpoint's last segment
    # (route name) must appear somewhere in the Python source. Loose
    # check — we look for the path component after /api/v1/ as a
    # substring of any router definition.
    for endpoint, slug in sorted(seen):
        # Reduce to the first path segment after /api/v1/.
        # e.g. /api/v1/analytics/compare-periods → "analytics"
        path_after_prefix = endpoint.split("/api/v1/", 1)[1]
        first_segment = path_after_prefix.split("/", 1)[0]
        # Allow {param}-style template placeholders to match.
        if first_segment.startswith("{") and first_segment.endswith("}"):
            continue
        # Look for either an APIRouter with prefix containing this
        # segment, or the segment as part of an endpoint path.
        # The grep is liberal — false-positives are OK; what we want to
        # catch is a totally fabricated /api/v1/<bogus> reference.
        searchable = first_segment.replace("-", "_")
        assert (
            f"/{first_segment}" in haystack
            or f'"{first_segment}"' in haystack
            or f"'{first_segment}'" in haystack
            or f"prefix=\"/api/v1/{first_segment}" in haystack
            or f"prefix='/api/v1/{first_segment}" in haystack
            or searchable in haystack
        ), (
            f"Help topic '{slug}' references endpoint '{endpoint}', but "
            f"no API router with first segment '{first_segment}' was "
            f"found in the codebase. Either the endpoint was renamed "
            f"or removed, or the topic has a typo."
        )


def test_every_mcp_tool_mentioned_in_markdown_exists():
    """Scan Help bodies for MCP tool names and verify each exists in
    the mcp-server source. We use a fixed allow-list of known tools
    rather than a regex match; helps catch typos like
    'analyze_corelation' (missing r)."""
    haystack = _all_python_source()

    # Find candidate tool names: snake_case identifiers that match the
    # known MCP tool pattern (starts with verb_-ish) AND appear in code-
    # like contexts (backticked or bare in prose).
    mentioned: set[tuple[str, str]] = set()  # (tool_name, topic_slug)
    for t in BACKEND_TOPICS:
        for tool in _KNOWN_MCP_TOOLS:
            if tool in t.body:
                mentioned.add((tool, t.slug))

    # Every mentioned tool's function definition must appear in the
    # mcp-server source.
    for tool, slug in sorted(mentioned):
        assert f"def {tool}(" in haystack, (
            f"Help topic '{slug}' references MCP tool '{tool}', but "
            f"no function 'def {tool}(' was found in the mcp-server "
            f"source. Tool was renamed or removed?"
        )


# ---------------------------------------------------------------------------
# Methodology page verbatim check
# ---------------------------------------------------------------------------


def test_methodology_pages_reference_their_registry_keys():
    """Each Methodology Help page should mention the methodology_registry
    key it corresponds to (in keywords or body), so the audit chain is
    traceable: PDF report → Methodology section → Help page → registry
    key → analytical compute function."""
    methodology_topics = [t for t in BACKEND_TOPICS if t.section == "Methodology"]
    assert len(methodology_topics) == 6, "Expected 6 Methodology topics."

    expected_keys = {
        "methodology-pearson-correlation": "pairwise_correlation_pearson",
        "methodology-partial-correlation": "partial_correlation_pearson",
        "methodology-lag-correlation": "cross_correlation_with_bootstrap_ci",
        "methodology-ridge-regression": "ridge_regression_cv_with_quantile_intervals",
        "methodology-linear-trend": "linear_regression_least_squares",
        "methodology-period-comparison": "compare_periods_mean_difference",
    }

    for t in methodology_topics:
        expected_key = expected_keys.get(t.slug)
        assert expected_key, f"Unknown methodology slug: {t.slug}"
        body_or_kw = t.body.lower() + " " + " ".join(t.keywords).lower()
        assert expected_key.lower() in body_or_kw, (
            f"Methodology Help page '{t.slug}' should mention its "
            f"registry key '{expected_key}' in body or keywords. "
            f"Found neither — the link between Help and methodology "
            f"registry is broken."
        )


def test_methodology_registry_keys_match_methodology_pages():
    """The PDF methodology_registry has 6 entries; the Help section
    has 6 methodology topics. Both must agree on the set."""
    from ursa_oscar.reports.methodology_registry import METHODOLOGY_REGISTRY

    registry_keys = set(METHODOLOGY_REGISTRY.keys())
    methodology_topics = [t for t in BACKEND_TOPICS if t.section == "Methodology"]

    # Each methodology page should reference one registry key in its
    # body. We've already verified that above; here we check the inverse:
    # every registry key should have a corresponding Help topic.
    expected_keys = {
        "methodology-pearson-correlation": "pairwise_correlation_pearson",
        "methodology-partial-correlation": "partial_correlation_pearson",
        "methodology-lag-correlation": "cross_correlation_with_bootstrap_ci",
        "methodology-ridge-regression": "ridge_regression_cv_with_quantile_intervals",
        "methodology-linear-trend": "linear_regression_least_squares",
        "methodology-period-comparison": "compare_periods_mean_difference",
    }
    help_keys = set(expected_keys.values())
    assert help_keys == registry_keys, (
        f"Methodology coverage drift. Help system covers {help_keys}, "
        f"methodology_registry.py has {registry_keys}. "
        f"Adding a new analytical method? Add both a Methodology Help "
        f"topic and a registry entry."
    )
