"""Import-job endpoints.

Phase 1 runs imports synchronously inside the request. Phase 4 will move to
an async job queue with `GET /api/imports/{id}` returning live status.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..ingestion.importer import import_path
from ..models.domain import ImportLogEntry

router = APIRouter(prefix="/api/v1", tags=["imports"])


class ImportRequest(BaseModel):
    """POST /api/imports body."""
    source_path: str = Field(
        description="Filesystem path to a DATALOG dir or SD-card root mounted into the container."
    )
    include_timeseries: bool = Field(
        default=True,
        description=(
            "Also write the per-channel time-series tables (pressure, leak, "
            "flow_limit, tidal_volume, minute_vent, resp_rate, snore). Required "
            "for the Daily View waveform charts."
        ),
    )


@router.post("/imports", response_model=ImportLogEntry)
def trigger_import(req: ImportRequest, request: Request) -> ImportLogEntry:
    db = request.app.state.db
    src = Path(req.source_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"Source path does not exist: {src}")
    return import_path(src, db, include_timeseries=req.include_timeseries)


@router.get("/imports/{job_id}", response_model=ImportLogEntry)
def get_import_status(job_id: int, request: Request) -> ImportLogEntry:
    """Phase 1 stub — synchronous imports don't have queryable job state yet.

    Returns 404 to indicate the endpoint exists but Phase 1 imports complete
    synchronously, so there's no async job to look up. Phase 4 will provide
    real status.
    """
    raise HTTPException(
        status_code=404,
        detail=(
            "Async import jobs land in Phase 4. Phase 1 imports complete "
            "synchronously inside POST /api/imports."
        ),
    )
