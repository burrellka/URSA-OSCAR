"""API client for the URSA-OSCAR backend.

Per the DuckDB concurrency model (a single writer locks the file even
against read-only opens from other processes), the MCP container no longer
opens DuckDB directly. Instead all reads + writes go through the API
container over the `kairos-net` Docker network. The API container is the
single owner of the DuckDB file.

This is the same pattern `trigger_import` already uses, generalized to all
tools.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


API_BASE_URL = os.environ.get("URSA_OSCAR_API_URL", "http://ursa-oscar-api:8000")


def get_client(timeout: float = 30.0) -> httpx.Client:
    """Return a fresh sync httpx client targeting the API."""
    return httpx.Client(base_url=API_BASE_URL, timeout=timeout)


def api_get(path: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    """GET the API and return the parsed JSON body.

    Raises httpx.HTTPStatusError on non-2xx; tools wrap that in the
    {"ok": False, "code": "..."} envelope.
    """
    with get_client(timeout=timeout) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def api_post(path: str, json_body: dict[str, Any] | None = None, timeout: float = 300.0) -> Any:
    """POST to the API. Used by trigger_import."""
    with get_client(timeout=timeout) as c:
        r = c.post(path, json=json_body)
        r.raise_for_status()
        return r.json()
