"""URSA-OSCAR MCP server — lifted from APEX template §5.

~150 LOC of boilerplate handling auth, transport, and discovery. Tools are
registered from `tools/` modules at the bottom. Adopted wholesale per
Decision 10 / ADR-002 — see APEX `docs/mcp-server-architecture-template.md`.
"""
from __future__ import annotations

import logging

from fastmcp import FastMCP

from .auth import build_auth_provider


logger = logging.getLogger("ursa-oscar-mcp")


# Build the auth provider FIRST — this fails fast if env is misconfigured,
# so the server never gets to a half-up state.
_auth_provider = build_auth_provider()
mcp = FastMCP("URSA-OSCAR", auth=_auth_provider)


# Register tools by importing the modules (each module decorates its
# function with @mcp.tool() against the shared `mcp` instance above).
# Adding a new tool = new file in tools/ + an import here.
from .tools import (  # noqa: E402 (import after FastMCP instantiation is required)
    ahi_breakdown,
    analyze_correlation,                # Phase 3 Item 5B (Tier 2)
    analyze_lag_correlation,            # Phase 6 Ticket 6.1 Item 3
    analyze_multivariate_correlation,   # Phase 6 Ticket 6.1 Item 2
    analyze_prediction,                 # Phase 6 Ticket 6.2
    compare_periods,                    # Phase 3 Item 5A (Tier 2)
    event_distribution,
    get_manual_log_summary,             # Phase 3 Item 5D (Tier 2)
    get_trend,                          # Phase 3 Item 5C (Tier 2)
    leak_profile,
    list_nights,
    nightly_summary,
    pressure_profile,
    session_breakdown,
    trigger_import,
    user_profile,                       # Phase 3 Item 5E (Tier 1)
)


# ---------------------------------------------------------------------------
# Entry point lives in __main__.py to avoid the python -m ursa_oscar_mcp.server
# duality bug (server module loaded twice — once as __main__, once as the
# package module — with tools registering on a different FastMCP instance than
# the one uvicorn serves). Use `python -m ursa_oscar_mcp` instead.
# ---------------------------------------------------------------------------
