"""Folder-upload endpoint regression coverage.

The Phase 3 Item 2 upload path went out at 0.6.0 with a sanitizer that
rejected every file when the browser sent backslash-style paths in the
multipart filename (Windows path-separator quirk surfaced by Edge
or by FormData filename normalization on some platforms). This file
locks down the fix:

  1. The sanitizer accepts forward-slash AND backslash paths, normalizes
     to forward slashes, and strips a leading drive letter.
  2. The import-root locator finds the right subdirectory inside the
     reconstructed tempdir regardless of how the user picked the
     folder (SD root with wrapper, DATALOG directly, etc.).
  3. End-to-end against a real ResMed AirSense 11 SD card at
     ``C:/dev/URSA-OSCAR/curr sd card/`` — the live regression
     fixture for this code path. The test skips when that path
     isn't present (CI, public repo, anyone but the operator).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ursa_oscar.api.imports import (
    _ALLOWED_SUFFIXES,
    _locate_import_root,
    _sanitize_relpath,
)
from ursa_oscar.main import create_app
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


# -------------------------------------------------------------------------
# Sanitizer unit-style cases — fast, deterministic, no fixtures required.
# -------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected_rel", [
    # Forward-slash (Chrome/Edge on most platforms)
    ("SDcard/STR.edf", "SDcard/STR.edf"),
    ("SDcard/DATALOG/20230424/20230424_130617_CSL.edf",
     "SDcard/DATALOG/20230424/20230424_130617_CSL.edf"),
    # Backslash (Windows-style — was 100 % rejected pre-fix)
    ("SDcard\\STR.edf", "SDcard/STR.edf"),
    ("SDcard\\DATALOG\\20230424\\foo.edf",
     "SDcard/DATALOG/20230424/foo.edf"),
    # Drive letter prefix
    ("D:\\SDcard\\STR.edf", "SDcard/STR.edf"),
    ("d:/SDcard/STR.edf", "SDcard/STR.edf"),
    # Case-insensitive suffix
    ("SDcard/STR.EDF", "SDcard/STR.EDF"),
    # All allowlisted suffixes
    ("a.crc", "a.crc"),
    ("a.json", "a.json"),
    ("a.jnl", "a.jnl"),
    ("a.tgt", "a.tgt"),
    ("a.dat", "a.dat"),
])
def test_sanitize_accepts_realistic_paths(raw: str, expected_rel: str):
    rel, reason = _sanitize_relpath(raw)
    assert rel is not None, f"expected ACCEPT for {raw!r}, got reject={reason}"
    # On Windows the Path object renders with `\`; normalize for comparison.
    assert rel.as_posix() == expected_rel


@pytest.mark.parametrize("raw,reason", [
    ("", "empty_name"),
    ("../../etc/passwd", "traversal"),
    ("good/../../etc/passwd", "traversal"),
    ("no_extension_at_all", "bad_suffix"),
    ("IndexerVolumeGuid", "bad_suffix"),
    ("Thumbs.db", "bad_suffix"),
    ("something.weird", "bad_suffix"),
    # OS-junk segment filter — these would otherwise sneak through
    # because their suffix IS in the allowlist (.dat for WPSettings,
    # .json for various OS files).
    ("SDcard/System Volume Information/WPSettings.dat", "os_junk"),
    ("SDcard/System Volume Information/IndexerVolumeGuid", "os_junk"),
    ("SDcard/$RECYCLE.BIN/info.json", "os_junk"),
    ("SDcard/.Trashes/file.edf", "os_junk"),
    ("SDcard/__MACOSX/STR.edf", "os_junk"),
    # Case-insensitive blocklist matching.
    ("sdcard/system volume information/WPSettings.dat", "os_junk"),
])
def test_sanitize_rejects_with_reason(raw: str, reason: str):
    rel, got_reason = _sanitize_relpath(raw)
    assert rel is None, f"expected REJECT for {raw!r}, got accept={rel}"
    assert got_reason == reason


def test_allowlist_covers_resmed_airsense11():
    """The allowlist must accept every extension the AirSense 11 SD card
    ships with. New firmware versions occasionally introduce new suffixes;
    this test fails loudly if the allowlist drifts behind."""
    required = {".edf", ".crc", ".json", ".jnl", ".tgt", ".dat"}
    assert required.issubset(_ALLOWED_SUFFIXES)


# -------------------------------------------------------------------------
# Import-root locator — verifies the BFS picks the right dir for each
# webkitdirectory wrapping shape.
# -------------------------------------------------------------------------


def _make_tree(root: Path, rels: list[str]) -> None:
    for r in rels:
        p = root / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_locate_import_root_under_wrapper(tmp_path):
    """SD root nested inside a single picked-folder wrapper."""
    _make_tree(tmp_path, [
        "curr_sd_card/STR.edf",
        "curr_sd_card/DATALOG/20230424/20230424_130617_CSL.edf",
    ])
    assert _locate_import_root(tmp_path) == tmp_path / "curr_sd_card"


def test_locate_import_root_datalog_picked_directly(tmp_path):
    """User picked the DATALOG/ folder directly — children are YYYYMMDD/."""
    _make_tree(tmp_path, [
        "DATALOG_PICKED/20230424/20230424_130617_CSL.edf",
        "DATALOG_PICKED/20230425/20230425_220000_BRP.edf",
    ])
    assert _locate_import_root(tmp_path) == tmp_path / "DATALOG_PICKED"


def test_locate_import_root_no_wrapper(tmp_path):
    """Tempdir itself is the SD root (no wrapper segment)."""
    _make_tree(tmp_path, [
        "STR.edf",
        "DATALOG/20230424/20230424_130617_CSL.edf",
    ])
    assert _locate_import_root(tmp_path) == tmp_path


def test_locate_import_root_falls_back_to_tempdir(tmp_path):
    """No DATALOG anywhere — fall back to tempdir, let the importer error
    cleanly with 'no nights found'."""
    _make_tree(tmp_path, ["random/folder/STR.edf"])
    assert _locate_import_root(tmp_path) == tmp_path


# -------------------------------------------------------------------------
# End-to-end against the operator's real SD card.
# Skips when the data isn't present (CI, fresh clone, other contributors).
# -------------------------------------------------------------------------


REAL_SD_CARD = Path("C:/dev/URSA-OSCAR/curr sd card")


@pytest.fixture
def real_sd_files() -> list[tuple[str, bytes]]:
    """Return (webkitRelativePath, contents) tuples for the entire SD card.

    Each path is rooted at the picked-folder name ("curr sd card") to
    match what the browser actually sends. We also expose two variants
    of each filename to the multipart layer in different tests below
    (forward and backslash) to lock in the regression.
    """
    if not REAL_SD_CARD.is_dir():
        pytest.skip(f"real SD card not present at {REAL_SD_CARD}")
    out: list[tuple[str, bytes]] = []
    for dirpath, _, filenames in os.walk(REAL_SD_CARD):
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(REAL_SD_CARD.parent).as_posix()
            out.append((rel, full.read_bytes()))
    assert out, "found no files inside curr sd card/"
    return out


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None
    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    seeder.close()
    app = create_app()
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def _post_upload(client: TestClient, named_files: list[tuple[str, bytes]]):
    """POST a list of (filename, content) tuples as multipart form-data.

    requests/httpx accepts files as ``[("files", (name, fh, ctype)), ...]``.
    We send a BytesIO per file so the client streams correctly.
    """
    import io
    parts = [
        ("files", (name, io.BytesIO(content), "application/octet-stream"))
        for name, content in named_files
    ]
    return client.post("/api/v1/imports/upload", files=parts)


def _wait_for_job(client: TestClient, job_id: int, timeout_s: float = 60.0) -> dict:
    """Poll /imports/jobs/{id} until status is terminal (completed/failed/orphaned),
    then return the job row. 0.8.0 — the upload endpoint enqueues rather
    than blocking, so e2e tests need to await worker completion.

    Test budget defaults to 60s — generous because a full SD-card import
    can take a few seconds and the worker polls at 1s intervals.
    """
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/imports/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in {"completed", "failed", "orphaned"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not finish within {timeout_s}s")


def test_upload_real_sd_card_forward_slash(api_client, real_sd_files):
    """The full SD card uploads cleanly with forward-slash filenames
    (the path shape Chrome/Edge sends on most platforms).

    0.8.0 — the endpoint enqueues; we poll the job to await the result
    rather than getting the ImportLogEntry inline.
    """
    r = _post_upload(api_client, real_sd_files)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["upload_dir"] is not None
    finished = _wait_for_job(api_client, job["id"])
    assert finished["status"] == "completed", finished
    result = finished["result_json"]
    assert result is not None
    # The ResMed SD card has been worn long enough to register multiple
    # nights — just assert we got non-zero imports rather than pinning to
    # a fixed count that drifts as the operator wears the device.
    assert result["nights_imported"] > 0
    assert result["status"] in {"completed", "partial"}


def test_upload_real_sd_card_backslash_filenames(api_client, real_sd_files):
    """REGRESSION — the 0.6.0 release rejected 389/389 files because the
    sanitizer's ``\\`` check fired on Windows-style filenames. Same data
    with backslashes in every filename must produce the same result as
    the forward-slash test."""
    backslash_files = [(name.replace("/", "\\"), content) for name, content in real_sd_files]
    r = _post_upload(api_client, backslash_files)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued"
    finished = _wait_for_job(api_client, job["id"])
    assert finished["status"] == "completed", finished
    result = finished["result_json"]
    assert result is not None
    assert result["nights_imported"] > 0


def test_upload_real_sd_card_drops_os_junk(api_client, real_sd_files, tmp_path):
    """End-to-end: real SD-card payload must NOT carry the Windows-created
    ``System Volume Information/`` files into the tempdir. The full upload
    should succeed AND the rejected tally for ``os_junk`` should be
    non-zero (the snapshot contains 2 such files: WPSettings.dat and
    IndexerVolumeGuid).

    We can't see the rejection counts directly because the 200 response
    only returns the ImportLogEntry, so we assert indirectly: that the
    OS-junk paths in the input ARE rejected at sanitize time. The
    e2e success of the upload (other tests) is independent evidence
    that the rest of the payload still gets through.
    """
    os_junk_paths = [
        rel for rel, _ in real_sd_files
        if "system volume information" in rel.lower()
        or "$recycle.bin" in rel.lower()
    ]
    # The snapshot must contain at least one OS-junk file for this test
    # to be meaningful; otherwise it'd silently no-op.
    assert os_junk_paths, (
        f"snapshot at {REAL_SD_CARD} contains no OS-junk files — test would no-op. "
        f"Confirm the snapshot was taken with the volume mounted on Windows."
    )
    for path in os_junk_paths:
        rel, reason = _sanitize_relpath(path)
        assert rel is None, f"expected REJECT for OS-junk path {path!r}, got {rel}"
        assert reason == "os_junk", f"expected reason=os_junk for {path!r}, got {reason}"


def test_upload_empty_payload_returns_400_with_diagnostics(api_client):
    """No usable files → 400 with a structured detail message naming the
    actual reject reasons. The pre-fix message lumped everything under
    'non-CPAP suffix' and gave the operator nothing actionable."""
    import io
    # Suffixes guaranteed NOT in the allowlist — picking '.exe' / '.txt'
    # rather than '.db' / '.dat' (the latter is allowlisted as a ResMed
    # journal file shape).
    parts = [
        ("files", ("Thumbs.exe", io.BytesIO(b"junk"), "application/octet-stream")),
        ("files", ("$RECYCLE.BIN/info.txt", io.BytesIO(b"junk"), "application/octet-stream")),
        ("files", ("readme.md", io.BytesIO(b"junk"), "application/octet-stream")),
    ]
    r = api_client.post("/api/v1/imports/upload", files=parts)
    assert r.status_code == 400
    detail = r.json()["detail"]
    # Detail must surface per-reason counts AND sample raw filenames so
    # the operator can see what the browser actually sent.
    assert "bad_suffix=" in detail
    assert "Samples:" in detail
