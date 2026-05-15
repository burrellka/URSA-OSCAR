"""Operator-facing forced re-run of the v6 per-session pressure-stat backfill.

Schema-v6 (Phase 5.5) auto-backfills on first 0.9.8 startup via
``apply_migrations``, so the typical operator never needs to run this.
Two scenarios where it's useful:

  1. **Force-clear + recompute.** If something looks wrong with the
     cached values (a re-import didn't update them, a manual UPDATE
     nulled them out, etc.), run this with ``--clear`` to drop every
     cached value and re-compute from current timeseries.

  2. **Re-run after re-importing timeseries.** A historical re-import
     with ``include_timeseries=True`` already triggers the per-night
     backfill via the importer hook, but if you manually re-loaded
     timeseries data outside the importer this is the explicit way
     to refresh.

Usage::

    python -m scripts.backfill_session_pressure                  # default
    python -m scripts.backfill_session_pressure --clear           # force-recompute all
    python -m scripts.backfill_session_pressure --db-path /data/ursa-oscar.duckdb

The script honors ``URSA_OSCAR_DB_PATH`` env when ``--db-path`` is
absent, so calling it inside the API container with no args Just Works.

Idempotent — re-running after everything is filled is a no-op.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ursa_oscar.config import get_settings
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import backfill_session_pressure_stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="DuckDB file path. Defaults to URSA_OSCAR_DB_PATH or the config default.",
    )
    ap.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Force-recompute every session: NULLs all cached values "
            "before backfilling. Use when cached values appear stale "
            "or wrong."
        ),
    )
    args = ap.parse_args(argv)

    db_path: Path = args.db_path or get_settings().db_path
    if not db_path.exists():
        print(f"ERROR: DuckDB file not found at {db_path}", file=sys.stderr)
        return 2

    print(f"backfill_session_pressure: opening {db_path}")
    db = DuckDBManager(db_path, read_only=False)
    try:
        if args.clear:
            print("backfill_session_pressure: --clear: nulling all cached values")
            with db.serialized() as conn:
                conn.execute(
                    """
                    UPDATE sessions
                       SET pressure_median=NULL, pressure_p95=NULL, pressure_p995=NULL,
                           ipap_median=NULL,     ipap_p95=NULL,     ipap_p995=NULL,
                           epap_median=NULL,     epap_p95=NULL,     epap_p995=NULL,
                           flow_limit_median=NULL, flow_limit_p95=NULL, flow_limit_p995=NULL,
                           leak_median=NULL,     leak_p95=NULL,     leak_p995=NULL
                    """
                )

        t0 = time.perf_counter()
        n = backfill_session_pressure_stats(db)
        dt = time.perf_counter() - t0
        if n == 0:
            print("backfill_session_pressure: nothing to do — all sessions already have cached stats")
        else:
            print(f"backfill_session_pressure: populated {n} session row(s) in {dt:.2f}s")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
