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

from ..ingestion.airsense11_layout import locate_import_root
from ..ingestion.importer import import_path  # noqa: F401  (kept for back-compat test imports)
from ..models.domain import ImportJob, ImportLogEntry  # noqa: F401  (ImportLogEntry retained for tests)
from ..storage.repositories import import_jobs as jobs_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["imports"])


# Allowed file shapes from a ResMed AirSense SD card. The importer only
# reads .edf — everything else is kept defensively so a full card upload
# round-trips cleanly. .tgt and .dat are ResMed companion files
# (IDENTIFICATION.tgt, JOURNAL.dat) that ship alongside on older
# firmware. Anything outside this list gets dropped server-side —
# defense against a user accidentally selecting their downloads folder
# instead of the SD card.
_ALLOWED_SUFFIXES = {".edf", ".crc", ".json", ".jnl", ".tgt", ".dat"}

# OS-level junk that ends up on the SD card when the operator plugs it
# into a Windows / macOS machine. None of these belong in the importer
# — explicitly drop any file whose path contains one of these as a
# segment so the per-file suffix check doesn't accidentally pull in
# `System Volume Information/WPSettings.dat` (matches the .dat
# allowlist but is pure OS noise). Match case-insensitively.
_OS_JUNK_SEGMENTS = frozenset(s.lower() for s in {
    "System Volume Information",   # Windows — restore points / indexer
    "$RECYCLE.BIN",                # Windows recycle bin
    "RECYCLER",                    # Pre-Vista Windows recycle bin
    ".Trashes",                    # macOS user trash
    ".Spotlight-V100",             # macOS Spotlight metadata
    ".fseventsd",                  # macOS file events
    "__MACOSX",                    # macOS zip-archive artifacts
    ".DocumentRevisions-V100",     # macOS document versions
    ".TemporaryItems",             # macOS scratch
})

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


@router.post("/imports", response_model=ImportJob)
def trigger_import(
    req: ImportRequest,
    request: Request,
    force: bool = False,
) -> ImportJob:
    """Enqueue a path-based import job.

    Phase 4 Ticket 2 — this endpoint no longer blocks for the duration
    of the import. It validates the source path, enqueues a row in
    import_jobs (status='queued'), and returns the job immediately
    with status='queued'. The ImportWorker picks it up on its next
    poll (within ~1s) and processes it; the operator polls
    /imports/jobs/{id} for status + result, or watches the Import
    page's Active Jobs section.

    Query params:
      force: if true, re-parse nights even when a `nightly_summary` row
             already exists. Defaults to false — the importer skips
             already-known nights for speed.
    """
    db = request.app.state.db
    src = Path(req.source_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"Source path does not exist: {src}")
    return jobs_repo.enqueue(
        db,
        source_path=str(src),
        force_reimport=force,
    )


@router.get("/imports/jobs", response_model=list[ImportJob])
def list_import_jobs(
    request: Request,
    active_only: bool = False,
    limit: int = 50,
) -> list[ImportJob]:
    """List import jobs, newest first.

    Query params:
      active_only: when true, only return rows with status in
                   (queued, running). The Import page polls this every
                   2s while any job is active.
      limit: max rows to return (defaults to 50). Recent imports tail.
    """
    db = request.app.state.db
    if active_only:
        return jobs_repo.list_active(db)
    return jobs_repo.list_jobs(db, limit=limit)


@router.get("/imports/jobs/{job_id}", response_model=ImportJob)
def get_import_job(job_id: int, request: Request) -> ImportJob:
    """Single-job status lookup. 404 when no job exists for the id."""
    db = request.app.state.db
    job = jobs_repo.get(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"No import job with id={job_id}",
        )
    return job


@router.post("/imports/upload", response_model=ImportJob)
async def upload_folder_and_import(
    request: Request,
    files: list[UploadFile] = File(...),
    force: bool = False,
) -> ImportJob:
    """Phase 3 Item 2 (refactored for Phase 4 Ticket 2) — browser-side
    folder-upload import, enqueued.

    Accepts a multipart form-data payload from a browser ``<input
    type="file" webkitdirectory>`` picker. Each part's filename includes
    the relative path inside the chosen folder (preserved via
    webkitRelativePath on the browser side); we reconstruct that tree
    into a tempdir on the API container, locate the right import root,
    and ENQUEUE a job pointing at it. The ImportWorker picks the job
    up and runs the actual EDF parse off the request thread. Tempdir
    cleanup also moves to the worker so it persists for the duration
    of the import.

    Behavior change from 0.7.x:
      - Returns immediately with the enqueued ImportJob (status='queued').
        No more multi-second blocking request for big SD cards.
      - 400 on no-usable-files still fires here (synchronously) — the
        sanitization is cheap and surfacing the diagnostic in the
        original request keeps the operator UX tight.

    Security / sanity guards (unchanged):
      - Per-file size cap (10 MB).
      - Suffix allowlist (.edf, .crc, .json, .jnl, .tgt, .dat).
      - Path-traversal protection in _sanitize_relpath.
      - OS-junk segment filter (System Volume Information, etc.).
      - Diagnostic 400 with per-reason counts + sample raw filenames.
    """
    db = request.app.state.db

    tempdir = Path(tempfile.gettempdir()) / f"ursa-upload-{uuid.uuid4().hex[:8]}"
    tempdir.mkdir(parents=True, exist_ok=True)
    logger.info("upload_folder_and_import: receiving into %s", tempdir)

    accepted = 0
    # Per-reason rejection tallies. The previous implementation lumped
    # everything into "bad suffix" which made the 400 path useless for
    # diagnostics — we couldn't tell whether the browser was sending
    # bad characters, wrong suffixes, or empty filenames.
    rejected: dict[str, int] = {
        "empty_name": 0,
        "traversal": 0,
        "absolute": 0,
        "os_junk": 0,
        "bad_suffix": 0,
        "too_big": 0,
    }
    rejected_samples: dict[str, list[str]] = {k: [] for k in rejected}

    def note_reject(reason: str, raw: str) -> None:
        rejected[reason] += 1
        if len(rejected_samples[reason]) < 5:
            rejected_samples[reason].append(raw)

    try:
        for f in files:
            # webkitdirectory inputs send filenames as 'folder/sub/file.edf'.
            # Some browsers normalize the multipart filename to use the OS
            # path separator (Windows → backslashes), which is what blew up
            # the original implementation. _sanitize_relpath now normalizes
            # both directions.
            raw_name = f.filename or ""
            if not raw_name:
                note_reject("empty_name", raw_name)
                continue
            rel, reason = _sanitize_relpath(raw_name)
            if rel is None:
                note_reject(reason or "bad_suffix", raw_name)
                continue

            content = await f.read()
            if len(content) > _MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "upload_folder_and_import: skipping %s (%d bytes > %d limit)",
                    raw_name, len(content), _MAX_FILE_SIZE_BYTES,
                )
                note_reject("too_big", raw_name)
                continue

            target = tempdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            accepted += 1

        if accepted == 0:
            # Build a diagnostic message so the next try doesn't require
            # the operator to ssh into the container for logs.
            tallies = ", ".join(f"{k}={v}" for k, v in rejected.items() if v)
            samples_flat: list[str] = []
            for reason, names in rejected_samples.items():
                for n in names:
                    samples_flat.append(f"[{reason}] {n}")
            samples_str = " ; ".join(samples_flat[:10])
            # Clean up immediately — no worker is going to pick this up.
            shutil.rmtree(tempdir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No usable files in upload ({tallies}). "
                    f"Pick the SD card's root folder or a DATALOG subdirectory. "
                    f"Samples: {samples_str}"
                ),
            )

        logger.info(
            "upload_folder_and_import: %d files accepted, rejected=%s, enqueueing tempdir=%s",
            accepted, rejected, tempdir,
        )
        # Enqueue and return immediately. The worker runs
        # _locate_import_root to find the right subdirectory, then
        # invokes import_path(), then removes the entire tempdir on
        # completion or failure. We pass the WHOLE tempdir (not the
        # import root) so cleanup wipes everything we wrote — including
        # the picked-folder wrapper that wraps the SD-card layout.
        return jobs_repo.enqueue(
            db,
            upload_dir=str(tempdir),
            force_reimport=force,
        )
    except Exception:
        # Any failure in receive/sanitize: bin the tempdir so we don't
        # leak. The 400 path above already did this — this catch is for
        # the truly unexpected (disk full, IOError, etc.).
        shutil.rmtree(tempdir, ignore_errors=True)
        raise


# Tuple return shape — (normalized Path on success, reason string on
# rejection). Sentinel `None` on the path side means "drop this file."
_RelpathResult = tuple[Optional[Path], Optional[str]]


def _sanitize_relpath(name: str) -> _RelpathResult:
    """Normalize a multipart filename into a safe relative path.

    Returns ``(Path, None)`` on success or ``(None, reason)`` if the
    file should be dropped, where ``reason`` is one of: ``empty_name``,
    ``traversal``, ``absolute``, ``os_junk``, ``bad_suffix``.

    Normalization rules:
      - Convert backslashes to forward slashes. Different browsers send
        the multipart filename differently — Firefox on Windows has
        historically preserved the OS separator. We accept both and
        normalize internally rather than reject on character.
      - Strip a leading drive letter (``D:`` / ``C:`` / etc.) if any —
        another Windows-browser quirk that used to take down the
        ``:`` check.
      - Strip leading slashes (absolute-path leaks become relative).
      - Reject any segment equal to ``..`` (path traversal).
      - Reject any path that's still absolute after stripping.
      - Reject any path containing a segment from
        ``_OS_JUNK_SEGMENTS`` — Windows/macOS shell artefacts that
        get auto-created on the SD card when the operator plugs it
        into a desktop machine. They have nothing to do with ResMed
        data and would only waste tempdir bytes (and worse, slip
        through the suffix check via the ``.dat`` allowlist).
      - Require the file's suffix matches ``_ALLOWED_SUFFIXES``.
    """
    name = name.replace("\\", "/")
    # Drop a leading drive letter prefix like 'D:' or 'C:'.
    if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
        name = name[2:]
    name = name.lstrip("/")
    if not name:
        return None, "empty_name"
    parts = name.split("/")
    if any(p == ".." for p in parts):
        return None, "traversal"
    # OS junk reject — checked BEFORE suffix so the rejection reason
    # is informative ("os_junk" vs "bad_suffix") for any file living
    # under one of these segments, even if its own suffix is allowlisted.
    if any(p.lower() in _OS_JUNK_SEGMENTS for p in parts):
        return None, "os_junk"
    rel = Path(name)
    if rel.is_absolute():
        return None, "absolute"
    if rel.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None, "bad_suffix"
    return rel, None


# _locate_import_root moved to ingestion/airsense11_layout.py (locate_import_root)
# in 0.8.0 so the worker can call it without depending on the api/ layer.
# The existing test_upload_endpoint.py keeps its local-name import compatible
# via the re-export below.
_locate_import_root = locate_import_root
