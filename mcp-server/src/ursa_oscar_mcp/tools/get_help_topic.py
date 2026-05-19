"""get_help_topic — Phase 7.3.

Exposes URSA-OSCAR's in-app Help content to AI assistants so the LLM
can answer "how does X work in URSA-OSCAR" without inventing answers
or pulling from possibly-outdated training data.

The Help content is the same markdown an operator sees in the web UI
at /help. The tool is a thin wrapper around the api container's
``/api/v1/help/*`` endpoints — single source of truth for topics + a
no-drift regression test that catches stale references in the markdown.
"""
from __future__ import annotations

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def get_help_topic(
    slug: str | None = None,
    search: str | None = None,
) -> dict:
    """Read URSA-OSCAR's in-app Help content.

    Three modes:

    1. **List every topic** — call with no arguments. Returns a
       directory of {slug, title, section, keywords} so the AI can
       see what's available without pulling every body.

    2. **Get one topic** — pass ``slug`` (e.g., 'arch-overview',
       'ahi-and-subindices', 'troubleshoot-mcp'). Returns the full
       markdown body plus metadata.

    3. **Search topics** — pass ``search`` (any substring). Returns
       up to ~20 ranked results — title matches first, then keyword
       matches, then body matches. Each result includes the full body.

    Use this tool when:
      - The user asks "how does URSA-OSCAR do X?"
      - The user asks about a specific page, feature, or concept
        defined in the Help system
      - You need to ground your answer in URSA-OSCAR's documented
        behavior rather than general knowledge
      - The user asks a methodology question (Pearson, partial
        correlation, ridge regression, etc.) — the Methodology section
        is the canonical explanation

    Don't use this tool when:
      - The user asks about their actual data (use get_nightly_summary,
        analyze_correlation, etc. instead)
      - The user asks a clinical question (general knowledge + their
        provider, not URSA-OSCAR documentation)

    Args:
        slug: optional. Specific topic slug to fetch.
        search: optional. Substring search query.
            If both ``slug`` and ``search`` are provided, ``slug`` wins.
            If neither is provided, returns the topic directory.

    Returns:
        {"ok": True, "data": {"topics": [...], "match_mode": "list" | "slug" | "search"}}
        or {"ok": False, "code": "NOT_FOUND" | ..., "error": "..."}
    """
    try:
        if slug:
            try:
                topic = api_get(f"/api/v1/help/topics/{slug}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return _err(
                        f"No Help topic with slug '{slug}'. Call "
                        f"get_help_topic with no arguments to see the "
                        f"full topic directory.",
                        code="NOT_FOUND",
                    )
                raise
            return _ok({"topics": [topic], "match_mode": "slug"})

        if search:
            results = api_get("/api/v1/help/search", params={"q": search})
            return _ok({"topics": results, "match_mode": "search"})

        # No args — return the directory (slug + title + section + keywords, no bodies).
        topics = api_get("/api/v1/help/topics")
        return _ok({"topics": topics, "match_mode": "list"})
    except httpx.HTTPStatusError as e:
        return _err(
            f"Help API returned {e.response.status_code}: {e.response.text[:200]}",
            code="HTTP_ERROR",
        )
    except Exception as e:
        return _err(
            f"{type(e).__name__}: {e}",
            code="INTERNAL_ERROR",
        )
