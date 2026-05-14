"""Cheap stable fingerprint of the watched-tree's structure + mtimes.

Phase 4 Ticket 3 — the watcher polls this every N seconds; when the
fingerprint changes, it resets a quiescence timer and waits for the
tree to stabilize before triggering an import. This is more robust
than fsevents-style notifications over network filesystems
(SMB/NFS bind-mounts often fire spurious events).

We walk the DATALOG/ subdir at the watched path if it exists,
otherwise the watch root itself. For each immediate child directory
we capture name, dir-mtime, and the mtime of its newest file. That
gives us "new YYYYMMDD/ appeared" + "new file inside YYYYMMDD/
appeared" detection without recursing the full tree.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# A fingerprint is a tuple of (name, newest_mtime) pairs sorted by
# name. ``newest_mtime`` is max(dir_mtime, max(file mtimes inside))
# — this matters on Windows where the OS updates directory mtimes
# asynchronously after file writes. Tracking dir_mtime + file mtime
# separately would mean two scans of an idle tree produce different
# fingerprints (the dir mtime catches up between scans) and we'd
# never reach quiescence. Collapsing to max() converges as soon as
# the file write completes.
Fingerprint = tuple[tuple[str, float], ...]


def compute_fingerprint(watch_path: str) -> Optional[Fingerprint]:
    """Return a fingerprint of the watched tree, or ``None`` if the
    path is unreachable. None is distinct from () — an unreachable
    path triggers a different code path in the caller (don't trigger
    imports for a card that's been pulled mid-poll)."""
    root = Path(watch_path)
    if not root.exists():
        logger.debug("compute_fingerprint: watch_path %s does not exist", root)
        return None
    if not root.is_dir():
        logger.warning("compute_fingerprint: %s is not a directory", root)
        return None

    datalog = root / "DATALOG"
    scan_dir = datalog if datalog.is_dir() else root

    items: list[tuple[str, float]] = []
    try:
        for entry in os.scandir(scan_dir):
            if not entry.is_dir(follow_symlinks=False):
                continue
            try:
                dir_stat = entry.stat()
                # Take max(dir_mtime, newest_file_mtime). For an empty
                # dir that's just dir_mtime; for a populated dir it's
                # whichever of (dir-was-created-or-renamed) or
                # (file-was-written) is newer.
                newest_mtime = dir_stat.st_mtime
                try:
                    for f in os.scandir(entry.path):
                        if f.is_file(follow_symlinks=False):
                            m = f.stat().st_mtime
                            if m > newest_mtime:
                                newest_mtime = m
                except OSError:
                    pass
                items.append((entry.name, newest_mtime))
            except OSError as e:
                logger.debug("compute_fingerprint: skipping %s — %s", entry.path, e)
                continue
    except OSError as e:
        logger.warning("compute_fingerprint: scandir %s failed: %s", scan_dir, e)
        return None

    items.sort(key=lambda t: t[0])
    return tuple(items)
