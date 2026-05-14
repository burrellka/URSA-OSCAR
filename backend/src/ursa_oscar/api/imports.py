"""Import-job endpoints.

Phase 1 runs imports synchronously inside the request. Phase 3 close-out
sprint adds POST /imports/upload for browser-driven multipart folder
uploads (Item 2). Phase 4 will move to an async job queue with
`GET /api/imports/{id}` returning live status.
"""
from __future__ import annotations

import logging
import re
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


@router.post("/imports", response_model=ImportLogEntry)
def trigger_import(
    req: ImportRequest,
    request: Request,
    force: bool = False,
) -> ImportLogEntry:
    """Trigger a path-based import.

    Query params:
      force: if true, re-parse nights even when a `nightly_summary` row
             already exists for that date. Defaults to false — the
             importer skips already-known nights for speed, which is
             the dominant path when the operator re-plugs the same SD
             card after a few new nights have accumulated.
    """
    db = request.app.state.db
    src = Path(req.source_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"Source path does not exist: {src}")
    return import_path(
        src, db,
        include_timeseries=req.include_timeseries,
        skip_existing=not force,
    )


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
    force: bool = False,
) -> ImportLogEntry:
    """Phase 3 Item 2 — browser-side folder-upload import.

    Accepts a multipart form-data payload from a browser ``<input
    type="file" webkitdirectory>`` picker. Each part's filename includes
    the relative path inside the chosen folder (preserved via
    webkitRelativePath on the browser side); we reconstruct that tree
    into a tempdir on the API container, then locate the right import
    root inside that tree, and run the existing importer.

    Security / sanity guards:
      - Per-file size cap (10 MB).
      - Suffix allowlist (.edf, .crc, .json, .jnl, .tgt, .dat). The
        importer only consumes ``.edf``; the rest are kept so a full
        SD-card upload round-trips cleanly. Anything else is silently
        dropped — defense if the user picked the wrong folder.
      - Reconstructed paths are normalized to forward slashes, leading
        drive letters and slash prefixes are stripped, '..' segments
        and absolute paths are rejected.
      - Tempdir is cleaned up in finally so a failed import doesn't
        leak GBs of EDF data into /tmp.
      - Per-rejection counts + sample filenames are surfaced in the 400
        response when nothing usable lands, so the operator can see
        what the browser actually sent.

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
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No usable files in upload ({tallies}). "
                    f"Pick the SD card's root folder or a DATALOG subdirectory. "
                    f"Samples: {samples_str}"
                ),
            )

        # webkitdirectory hands us paths rooted at the picked folder name
        # (e.g. 'curr sd card/DATALOG/...'). The importer's layout detector
        # looks for DATALOG/ at the supplied root, so we have to peel off
        # that outer wrapper. Walk the tree and prefer:
        #   1. A directory containing a DATALOG/ subdirectory (= SD root)
        #   2. A directory whose immediate children are YYYYMMDD/ (= DATALOG)
        #   3. Fall back to tempdir itself.
        # This also lets a future user pick just a DATALOG/ subfolder or
        # a sub-tree without surprising them.
        import_root = _locate_import_root(tempdir)
        logger.info(
            "upload_folder_and_import: %d files accepted, rejected=%s, root=%s",
            accepted, rejected, import_root,
        )

        # Run the existing importer. include_timeseries=True matches the
        # source-path path's default. skip_existing=not force is the
        # 0.6.3 dedup path — re-uploads of the same SD card skip
        # already-known nights for a fast re-import.
        return import_path(
            import_root, db,
            include_timeseries=True,
            skip_existing=not force,
        )
    finally:
        # Always clean up — even on import failure, the EDFs are now
        # transient state.
        try:
            shutil.rmtree(tempdir, ignore_errors=True)
        except Exception:
            logger.exception("upload_folder_and_import: tempdir cleanup failed for %s", tempdir)


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


def _locate_import_root(tempdir: Path) -> Path:
    """Find the right path inside ``tempdir`` to hand to ``import_path``.

    webkitdirectory uploads land under a single top-level wrapper named
    after the folder the user picked (e.g. ``tempdir/curr sd card/...``).
    The importer's layout detector keys off the presence of ``DATALOG/``
    or YYYYMMDD-shaped children at the root it gets handed, so we walk
    the tree (max depth 3) and pick the first dir that satisfies one of
    the two recognised shapes. Falls back to ``tempdir`` if neither
    matches — the importer then surfaces a clean "no nights found"
    error from its own layout detector.
    """
    night_re = re.compile(r"^\d{8}$")

    def _is_sd_root(d: Path) -> bool:
        return (d / "DATALOG").is_dir()

    def _is_datalog_root(d: Path) -> bool:
        try:
            return any(
                child.is_dir() and night_re.match(child.name)
                for child in d.iterdir()
            )
        except OSError:
            return False

    # BFS to depth 3 — cheap, and we never expect to dig further than
    # SD-root/folder-wrapper.
    queue: list[tuple[Path, int]] = [(tempdir, 0)]
    while queue:
        d, depth = queue.pop(0)
        if _is_sd_root(d):
            return d
        if _is_datalog_root(d):
            return d
        if depth < 3:
            try:
                for child in d.iterdir():
                    if child.is_dir():
                        queue.append((child, depth + 1))
            except OSError:
                pass

    return tempdir
