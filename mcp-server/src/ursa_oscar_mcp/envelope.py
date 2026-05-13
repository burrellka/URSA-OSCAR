"""Response envelope helpers — lifted verbatim from APEX template §6.1.

Every URSA-OSCAR MCP tool returns one of two shapes:
    {"ok": True, "data": {...}}                           # success
    {"ok": False, "error": "...", "code": "ERROR_CODE"}   # failure

Standard error codes (template §6.1): NOT_FOUND, INVALID_INPUT,
INVALID_OPERATION, ERROR.
"""
from __future__ import annotations

from typing import Any


def _ok(data: Any) -> dict:
    """Wrap a success payload in the canonical envelope."""
    return {"ok": True, "data": data}


def _err(error: str, code: str = "ERROR") -> dict:
    """Wrap a failure with a machine-readable code."""
    return {"ok": False, "error": error, "code": code}
