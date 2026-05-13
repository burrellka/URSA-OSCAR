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
