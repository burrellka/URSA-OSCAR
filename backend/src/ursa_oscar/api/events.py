"""GET endpoints for nightly_events."""
from __future__ import annotations

from datetime import date as date_t
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..models.domain import NightlyEvent
from ..storage.repositories import events as events_repo

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.get("/events", response_model=list[NightlyEvent])
def list_events(
    request: Request,
    date: date_t = Query(..., description="Night date (single night per request)"),
    event_type: Optional[list[str]] = Query(
        default=None, description="Filter to one or more event_type values"
    ),
) -> list[NightlyEvent]:
    """All events for one night, optionally filtered by type.

    Phase 1 only supports single-date queries; date-range support will land
    when Phase 2 charting needs it.
    """
    db = request.app.state.db
    return events_repo.list_for_date(db, date, event_types=event_type)
