"""Manual-log CRUD — Phase 3 work, stubbed in Phase 1.

The stub exposes the routes with 501 responses so frontend / MCP clients can
discover the shape without breaking when they call.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/manual-logs", tags=["manual-logs"])


@router.get("")
def list_manual_logs() -> dict:
    raise HTTPException(status_code=501, detail="Manual logs ship in Phase 3.")


@router.post("")
def create_manual_log() -> dict:
    raise HTTPException(status_code=501, detail="Manual logs ship in Phase 3.")


@router.patch("/{log_id}")
def update_manual_log(log_id: int) -> dict:
    raise HTTPException(status_code=501, detail="Manual logs ship in Phase 3.")


@router.delete("/{log_id}")
def delete_manual_log(log_id: int) -> dict:
    raise HTTPException(status_code=501, detail="Manual logs ship in Phase 3.")
