"""Watched-folder daemon — Phase 4 work, scaffolded here for forward compat.

Watches `URSA_OSCAR_IMPORT_WATCH_PATH` for new DATALOG subdirectories and
triggers the importer when one appears. The actual integration with FastAPI
(POSTing to /api/imports rather than writing DuckDB directly) lives in the
watcher container (`watcher/` package), not here. This module is the shared
file-system-observer helper that the watcher container imports.

Phase 1 scope: import-on-demand via CLI (importer.py). This module is a
no-op placeholder so the package structure matches the Design v1.1 repo
layout without leaving dangling references.
"""
from __future__ import annotations

from pathlib import Path

# Intentional non-functional placeholder. Phase 4 will:
# 1. Use `watchdog.observers.Observer` to watch IMPORT_WATCH_PATH
# 2. On a new `YYYYMMDD` directory creation event, POST to /api/imports
# 3. Idempotency handled by the importer's dedup-on-date logic


def watch(path: Path) -> None:  # pragma: no cover — Phase 4 scaffold
    """Phase 4 entry point. Currently raises NotImplementedError."""
    raise NotImplementedError(
        "Folder-watch import is Phase 4 scope. Use the importer CLI for Phase 1."
    )
