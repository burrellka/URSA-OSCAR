"""Layout detection for ResMed AirSense 11 SD-card exports.

Two shapes the importer must accept:

1. **SD-card root** — a directory containing a `DATALOG/` subdirectory plus
   `STR.edf` / `Identification.json` / `SETTINGS/` siblings. This is the
   shape Kevin's SD card mounts as.

2. **DATALOG-flat** — a directory whose immediate children are
   `YYYYMMDD/` night dirs. This is the test-fixture shape per Phase 1 V1
   Option 2: night dirs at `fixtures/nights/oscar-reference/YYYYMMDD/`.

Detection is structural: if a `DATALOG` subdirectory exists, treat the input
as an SD-card root. Otherwise scan immediate children for `YYYYMMDD`-shaped
dir names and treat the input as DATALOG-flat.
"""
from __future__ import annotations

import re
from datetime import date as date_t
from pathlib import Path


NIGHT_DIR_PATTERN = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def locate_import_root(start: Path) -> Path:
    """Find the right path inside ``start`` to hand to ``import_path``.

    webkitdirectory uploads (and zip-extracted SD-card dumps) land under
    a single top-level wrapper named after the folder the operator
    picked (e.g. ``tempdir/curr sd card/...``). ``list_night_dirs``
    keys off the presence of ``DATALOG/`` or YYYYMMDD-shaped children
    at the path it's handed, so we walk the tree (max depth 3) and
    pick the first directory that satisfies one of the two recognised
    shapes:
      1) Contains a ``DATALOG/`` subdirectory (= SD-card root)
      2) Has YYYYMMDD-shaped immediate children (= ``DATALOG/`` itself)

    Falls back to ``start`` if neither shape is found within the
    depth budget — ``list_night_dirs`` then surfaces a clean
    "no nights found" path from its own scan logic.

    Used by:
      - The async import worker, to locate the actual data root inside
        a multipart-upload tempdir before invoking import_path().
      - The 0.6.x synchronous /imports/upload endpoint (deprecated path
        kept for back-compat in unit tests).
    """
    def _is_sd_root(d: Path) -> bool:
        return (d / "DATALOG").is_dir()

    def _is_datalog_root(d: Path) -> bool:
        try:
            return any(
                child.is_dir() and NIGHT_DIR_PATTERN.match(child.name)
                for child in d.iterdir()
            )
        except OSError:
            return False

    queue: list[tuple[Path, int]] = [(start, 0)]
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

    return start


def list_night_dirs(root: Path) -> list[tuple[date_t, Path]]:
    """Return a list of (night_date, datalog_subdir) tuples, sorted by date.

    Empty list if `root` doesn't contain any recognizable night dirs.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")

    # If a `DATALOG/` subdirectory exists, use it.
    datalog = root / "DATALOG"
    scan_dir = datalog if datalog.is_dir() else root

    out: list[tuple[date_t, Path]] = []
    for child in scan_dir.iterdir():
        if not child.is_dir():
            continue
        m = NIGHT_DIR_PATTERN.match(child.name)
        if not m:
            continue
        yyyy, mm, dd = (int(g) for g in m.groups())
        try:
            night_date = date_t(yyyy, mm, dd)
        except ValueError:
            continue
        out.append((night_date, child))

    out.sort(key=lambda t: t[0])
    return out
