"""GET endpoints for nightly_summary rows."""
from __future__ import annotations

from datetime import date as date_t
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..models.domain import NightlySummary
from ..storage.repositories import nights as nights_repo

router = APIRouter(prefix="/api/v1", tags=["nights"])


@router.get("/nights", response_model=list[NightlySummary])
def list_nights(
    request: Request,
    start: Optional[date_t] = Query(default=None, description="Inclusive lower bound"),
    end: Optional[date_t] = Query(default=None, description="Inclusive upper bound"),
) -> list[NightlySummary]:
    """List nightly_summary records, optionally bounded by a date range."""
    db = request.app.state.db
    if start is None and end is None:
        # Get every available date, then list rows
        all_dates = nights_repo.list_dates(db)
        if not all_dates:
            return []
        start, end = all_dates[0], all_dates[-1]
    elif start is None:
        all_dates = nights_repo.list_dates(db, end=end)
        if not all_dates:
            return []
        start = all_dates[0]
    elif end is None:
        all_dates = nights_repo.list_dates(db, start=start)
        if not all_dates:
            return []
        end = all_dates[-1]
    return nights_repo.list_in_range(db, start, end)


@router.get("/night/{target_date}", response_model=NightlySummary)
def get_night(target_date: date_t, request: Request) -> NightlySummary:
    """Single nightly_summary row by date. 404 if no data for that night."""
    db = request.app.state.db
    night = nights_repo.get_by_date(db, target_date)
    if night is None:
        raise HTTPException(status_code=404, detail=f"No data for night {target_date}")
    return night
