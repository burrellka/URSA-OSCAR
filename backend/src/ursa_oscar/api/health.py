"""Health-check endpoint."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", tags=["health"])
def healthz() -> dict:
    """Liveness probe. Returns 200 + minimal payload as long as the app is up."""
    return {"ok": True, "service": "ursa-oscar-api"}
