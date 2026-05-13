"""get_user_profile — Phase 3 Item 5E, Tier-1 MCP tool.

This is the URSA agent's source-of-truth for clinical context. Unlike the
analytical Tier-2 tools (compare_periods, analyze_correlation, etc.), this
is foundational data the agent should fetch at the START of any
conversation that touches clinical reasoning — diagnoses, medications,
treatment goals — so it doesn't have to ask the user to repeat their
context every chat.

The tool wraps GET /api/v1/profile per ADR-003 (MCP-as-API-proxy). A
``section`` parameter lets the agent fetch just one of the three top-level
sections — typically ``clinical`` at session start — rather than always
pulling the full profile.
"""
from __future__ import annotations

import httpx

from ..client import api_get
from ..envelope import _err, _ok
from ..server import mcp


_VALID_SECTIONS = {"display", "clinical", "personalization", "all"}


@mcp.tool()
def get_user_profile(section: str = "all") -> dict:
    """Get the user's clinical profile — diagnoses, providers, active
    medications, treatment goals, and equipment. **Call this at the start
    of any conversation involving clinical reasoning** so you have the
    user's baseline context. The profile is the authoritative source for
    what conditions the user has and what they're currently being treated
    with — never ask the user to repeat clinical info that's in here.

    The profile has three top-level sections:

    - ``clinical`` — diagnoses (with ICD-10 codes), providers, treatment
      goals (e.g., "Maintain AHI < 5"), active medications with doses
      and schedules, equipment (CPAP machine, mask, wearables).
    - ``display`` — display preferences (timezone, units, date format).
      Use ``display.timezone`` when interpreting timestamps the user
      mentions in natural language ("at 9pm" vs "at 21:00 UTC").
    - ``personalization`` — UI preferences plus an ``active_concerns``
      list. Active concerns are short narrative statements the user has
      flagged (e.g., "Investigating whether evening alcohol affects AHI")
      that you should weigh in any analytical conversation.

    When to call:

    - First tool call of a new clinical conversation.
    - Any time the user mentions a medication and you're not sure if it's
      in their active list.
    - When interpreting a date/time you'd otherwise display in UTC.

    When NOT to call:

    - Every turn — once per conversation is enough; the profile rarely
      changes mid-chat.
    - For purely descriptive queries that don't need context ("what was
      my AHI last night" — call get_nightly_summary directly).

    Args:
        section: Which section to fetch. One of ``"all"`` (default),
            ``"display"``, ``"clinical"``, or ``"personalization"``.
            ``"all"`` returns the full UserProfile envelope; section-
            scoped calls return just that subtree, which keeps the
            response small when you only need one slice.

    Returns:
        On success with section="all":
            {"ok": true, "data": {
                "version": 1,
                "last_updated": "<ISO timestamp>",
                "display": {...},
                "clinical": {...},
                "personalization": {...}
            }}

        On success with a specific section, the response's "data" field
        is just that section's subtree:
            {"ok": true, "data": {"diagnoses": [...], "providers": [...], ...}}

        On error:
            {"ok": false, "error": "...", "code": "INVALID_INPUT" | "ERROR"}

    Security note: profile data contains clinical detail (diagnoses,
    medication names, provider names). The MCP layer surfaces this as-is
    to the requesting agent because the agent IS the user's authenticated
    session. Don't relay profile contents to other parties in tool
    chains, agents, or external services without explicit user direction.
    """
    if section not in _VALID_SECTIONS:
        return _err(
            f"Invalid section '{section}'. Must be one of {sorted(_VALID_SECTIONS)}.",
            code="INVALID_INPUT",
        )

    try:
        profile = api_get("/api/v1/profile")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 503:
            return _err(
                "Profile not initialized on the API container. This is unexpected — "
                "the API's lifespan hook should have copied the community default to "
                "/data/profile.json on startup.",
                code="ERROR",
            )
        return _err(f"API error: {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")

    if section == "all":
        return _ok(profile)

    # Section-scoped response — return just the requested subtree under
    # "data", not the wrapping envelope.
    subtree = profile.get(section)
    if subtree is None:
        return _err(
            f"Profile is missing section '{section}'. Likely a profile-schema mismatch "
            "between the MCP container and the API container.",
            code="ERROR",
        )
    return _ok(subtree)
