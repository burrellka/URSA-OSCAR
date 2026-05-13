"""trigger_import — kick off a DATALOG/SD-card import via the API container.

The MCP container is read-only on /data per Decision 2; this tool calls into
the API container (the sole DB writer) over the kairos-net Docker network to
perform the actual import. The API container has no host port in production
by design (matches APEX's apex-api posture), so this MCP tool is the only
external way to trigger imports outside Phase 4's watched-folder daemon.
"""
from __future__ import annotations

import os

import httpx

from ..client import api_post
from ..envelope import _err, _ok
from ..server import mcp


@mcp.tool()
def trigger_import(source_path: str = "/cpap-import") -> dict:
    """Trigger an import of a DATALOG / SD-card directory into the URSA-OSCAR DB.

    Walks the source path for `YYYYMMDD/` night dirs (or `DATALOG/YYYYMMDD/`
    if you point at an SD-card root), parses every session's EDF files,
    detects events, and upserts the nightly_summary + events tables.
    Idempotent — re-importing a date overwrites prior data for that date.

    Use when the user asks:
        "Import the new nights"
        "Re-import everything from the SD card"
        "Pull in last week's data"
        "Trigger an import"

    Args:
        source_path: Container-side path to a DATALOG dir or SD-card root.
            Defaults to `/cpap-import` which is the bind-mounted CPAP source
            from the TrueNAS host.

    Returns:
        {"ok": True, "data": {
            "nights_imported": int,
            "earliest_date": str | null,
            "latest_date": str | null,
            "status": "completed" | "failed",
            "source_path": str
        }}
    """
    if not isinstance(source_path, str) or not source_path.strip():
        return _err("source_path must be a non-empty string", code="INVALID_INPUT")

    try:
        return _ok(api_post("/api/v1/imports", json_body={"source_path": source_path}))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = e.response.text
            return _err(str(detail)[:200], code="INVALID_INPUT")
        return _err(f"API returned {e.response.status_code}", code="ERROR")
    except httpx.RequestError as e:
        return _err(f"Could not reach API container: {e}", code="ERROR")
    except Exception as e:
        return _err(f"Import failed: {e}", code="ERROR")
