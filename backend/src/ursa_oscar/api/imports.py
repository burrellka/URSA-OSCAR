"""Import-job endpoints.

Phase 1 runs imports synchronously inside the request. Phase 3 close-out
sprint adds POST /imports/upload for browser-driven multipart folder
uploads (Item 2). Phase 4 will move to an async job queue with
`GET /api/imports/{id}` returning live status.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..ingestion.importer import import_path
from ..models.domain import ImportLogEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["imports"])


# Allowed file shapes from a ResMed AirSense SD card. Anything else gets
# dropped on the floor server-side — defense against a user accidentally
# selecting their downloads folder instead of the SD card.
_ALLOWED_SUFFIXES = {".edf", ".crc", ".json", ".jnl"}
# 10 MB per file. The big files are the 25 Hz BRP flow waveforms,
# typically 1-3 MB. 10 MB gives headroom without enabling abuse.
_MAX_FILE_SIZE_MB = 10
_MAX_FILE_SIZE_BYTES = _MAX_FILE_SIZE_MB * 1024 * 1024


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


@router.post("/imports/upload", response_model=ImportLogEntry)
async def upload_folder_and_import(
    request: Request,
    files: list[UploadFile] = File(...),
) -> ImportLogEntry:
    """Phase 3 Item 2 — browser-side folder-upload import.

    Accepts a multipart form-data payload from a browser ``<input
    type="file" webkitdirectory>`` picker. Each part's filename includes
    the relative path inside the chosen folder (preserved via
    webkitRelativePath on the browser side); we reconstruct that tree
    into a tempdir on the API container, run the existing importer
    against it, and return the standard ImportLogEntry.

    Security / sanity guards:
      - Per-file size cap (10 MB).
      - Suffix allowlist (.edf, .crc, .json, .jnl). Anything else is
        silently dropped — defense if the user picked the wrong folder.
      - Reconstructed paths are joined inside the tempdir using only
        the path basenames; absolute paths or '..' segments in the
        uploaded filename are stripped to prevent traversal.
      - Tempdir is cleaned up in finally so a failed import doesn't
        leak GBs of EDF data into /tmp.

    The upload is whole-file buffered into memory then streamed to disk
    per file — UploadFile.read() returns bytes. For multi-GB SD cards
    this may become a problem; today the per-file 10 MB cap means
    the import-time peak memory is bounded.
    """
    db = request.app.state.db

    tempdir = Path(tempfile.gettempdir()) / f"ursa-upload-{uuid.uuid4().hex[:8]}"
    tempdir.mkdir(parents=True, exist_ok=True)
    logger.info("upload_folder_and_import: receiving into %s", tempdir)

    accepted = 0
    rejected_too_big = 0
    rejected_bad_suffix = 0

    try:
        for f in files:
            # webkitdirectory inputs send filenames as 'folder/sub/file.edf'.
            raw_name = f.filename or ""
            if not raw_name:
                continue
            rel = _sanitize_relpath(raw_name)
            if rel is None:
                rejected_bad_suffix += 1
                continue

            content = await f.read()
            if len(content) > _MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "upload_folder_and_import: skipping %s (%d bytes > %d limit)",
                    raw_name, len(content), _MAX_FILE_SIZE_BYTES,
                )
                rejected_too_big += 1
                continue

            target = tempdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            accepted += 1

        if accepted == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No usable files in upload (rejected {rejected_too_big} for "
                    f"size, {rejected_bad_suffix} for non-CPAP suffix). Pick the "
                    f"SD card's root folder or a DATALOG subdirectory."
                ),
            )

        logger.info(
            "upload_folder_and_import: %d files accepted, %d too big, %d bad suffix",
            accepted, rejected_too_big, rejected_bad_suffix,
        )

        # Run the existing importer. include_timeseries=True matches the
        # source-path path's default.
        return import_path(tempdir, db, include_timeseries=True)
    finally:
        # Always clean up — even on import failure, the EDFs are now
        # transient state.
        try:
            shutil.rmtree(tempdir, ignore_errors=True)
        except Exception:
            logger.exception("upload_folder_and_import: tempdir cleanup failed for %s", tempdir)


def _sanitize_relpath(name: str) -> Path | None:
    """Validate + normalize a multipart filename into a safe relative path.

    Returns the relative Path on success, or None if the file should be
    dropped (suffix not in allowlist, or unsafe traversal pattern).

    Sanitization rules:
      - Strip leading slashes (absolute path leaks → just take basename).
      - Reject any segment containing '..' (path traversal attempt).
      - Reject any segment with a drive letter or backslashes (Windows
        path mishap from the browser).
      - Require the file's suffix matches _ALLOWED_SUFFIXES.
    """
    name = name.lstrip("/").lstrip("\\")
    if not name:
        return None
    if ".." in name.split("/") or "\\" in name or ":" in name:
        return None
    rel = Path(name)
    if rel.is_absolute():
        return None
    if rel.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None
    return rel
