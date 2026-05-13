"""SD-card / DATALOG importer.

Walks a layout (SD-card root or DATALOG-flat), processes each night dir
through the analytics pipeline (analyze_session → filter empty →
build_summary), and writes the results into DuckDB:
- nightly_summary (one row per night)
- nightly_events (many rows per night)
- import_log (one row per import operation)

Time-series tables are NOT populated by default — they're large (~720k
rows/night for flow) and not required for the Phase 1 acceptance gate. The
`include_timeseries` flag opts in for Phase 2 charting prep.

CLI usage:
    python -m ursa_oscar.ingestion.importer <path-to-DATALOG-or-SD-root>

The DuckDB path comes from `URSA_OSCAR_DB_PATH` env (Settings); pass
`--db-path` to override.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

from ..analytics.edf_parser import WaveformSignal, discover_sessions
from ..analytics.session_analyzer import SessionAggregate, analyze_session
from ..analytics.settings_parser import parse_equipment_settings
from ..analytics.summary_builder import build_summary
from ..config import get_settings
from ..models.domain import ImportLogEntry, NightlySummary, SkippedNight
from ..storage.db import DuckDBManager
from ..storage.migrations import apply_migrations
from ..storage.repositories import events as events_repo
from ..storage.repositories import nights as nights_repo
from ..storage.repositories import timeseries as timeseries_repo
from .airsense11_layout import list_night_dirs


# Map our public series name -> (PLD signal label, whether to scale L/s -> L/min)
# pressure is special-cased (uses Press.2s + EprPress.2s for the EPAP companion).
_PLD_SERIES_MAP: list[tuple[str, str, float]] = [
    ("leak",         "Leak.2s",      60.0),  # L/s -> L/min
    ("flow_limit",   "FlowLim.2s",   1.0),
    ("tidal_volume", "TidVol.2s",    1.0),
    ("minute_vent",  "MinVent.2s",   1.0),
    ("resp_rate",    "RespRate.2s",  1.0),
    ("snore",        "Snore.2s",     1.0),
]


def _write_session_timeseries(
    db: DuckDBManager,
    night_date,
    session: SessionAggregate,
) -> int:
    """Bulk-write the 0.5 Hz PLD-derived time-series for one session.

    Skips the 25 Hz BRP flow waveform — that adds ~720k rows/night and is
    not on the Phase 2 Daily View critical path. Phase 2.5/3 will add it.

    Returns total rows inserted across all series.

    Performance: builds row tuples via vectorized numpy + zip (no per-sample
    Python loop). DuckDB's executemany on 14k 4-tuples completes in ~50 ms;
    the previous element-by-element loop ran at ~3 s per series per session
    and timed out 4-night imports.
    """
    pressure = session.pld_signals.get("Press.2s")
    epap = session.pld_signals.get("EprPress.2s")
    total = 0

    if pressure is not None and pressure.values.size > 0:
        ts_py = pressure.timestamps().astype("datetime64[us]").astype(object).tolist()
        p_vals = pressure.values.tolist()
        if epap is not None and epap.values.size == pressure.values.size:
            e_vals = epap.values.tolist()
        else:
            e_vals = [None] * len(p_vals)
        n = len(p_vals)
        rows = list(zip([night_date] * n, ts_py, p_vals, e_vals))
        total += timeseries_repo.bulk_insert(db, "pressure", rows)

    for series_name, pld_label, scale in _PLD_SERIES_MAP:
        sig = session.pld_signals.get(pld_label)
        if sig is None or sig.values.size == 0:
            continue
        ts_py = sig.timestamps().astype("datetime64[us]").astype(object).tolist()
        v_vals = (sig.values * scale).tolist() if scale != 1.0 else sig.values.tolist()
        n = len(v_vals)
        rows3 = list(zip([night_date] * n, ts_py, v_vals))
        total += timeseries_repo.bulk_insert(db, series_name, rows3)
    return total


def import_path(
    source_path: Path,
    db: DuckDBManager,
    *,
    include_timeseries: bool = True,
    verbose: bool = False,
) -> ImportLogEntry:
    """Import every night dir found under `source_path` into DuckDB.

    **Resilient per-night**: a parse/build/insert error on any single night
    is caught, logged into the returned ImportLogEntry's `skipped` list with
    a reason, and the loop continues to the next night. The whole import
    only reports `status="failed"` when *every* night dir errored — partial
    success reports `status="completed"` with `nights_skipped` > 0 and the
    skip reasons inline.

    Empty nights (no sessions, or all sessions filtered as empty) also land
    in `skipped` with a benign reason, so the UI shows the full inventory of
    what was and wasn't imported.

    Returns a populated ImportLogEntry. The caller is responsible for
    persisting it (the importer doesn't write to import_log itself so it
    works against a read-only DB if dry-running).
    """
    source_path = Path(source_path)
    started_at = datetime.utcnow()
    try:
        night_dirs = list_night_dirs(source_path)
    except FileNotFoundError as e:
        # Path doesn't exist or isn't a directory — that's a hard fail,
        # not a per-night skip.
        return ImportLogEntry(
            source_path=str(source_path),
            nights_imported=0,
            earliest_date=None,
            latest_date=None,
            status="failed",
            error_message=repr(e),
            nights_skipped=0,
            skipped=[],
        )

    # Parse equipment-settings JSONs once for the whole import — they
    # describe the most-recent device state at SD-card-export time.
    # Phase 1.5: applied to all imported nights. Phase 4 work to refine
    # per-night via STR.edf when prescriptions changed mid-week.
    try:
        equipment_settings = parse_equipment_settings(source_path)
    except Exception:
        # Settings parsing failures shouldn't block night ingestion.
        equipment_settings = None

    nights_imported = 0
    earliest: NightlySummary | None = None
    latest: NightlySummary | None = None
    skipped: list[SkippedNight] = []

    for night_date, datalog_dir in night_dirs:
        try:
            sessions_raw = discover_sessions(datalog_dir)
            aggregates: list[SessionAggregate] = []
            for i, s in enumerate(sessions_raw):
                aggregates.append(analyze_session(s, session_id=i + 1))
            non_empty = [a for a in aggregates if not a.is_empty]
            # Renumber sessions 1..N after dropping empties
            for new_id, agg in enumerate(non_empty, start=1):
                agg.session_id = new_id
                for ev in agg.events:
                    ev.session_id = new_id

            if not non_empty:
                skipped.append(SkippedNight(
                    date=night_date,
                    reason=(
                        f"No usable sessions ({len(sessions_raw)} session(s) found, all empty/short). "
                        "Common for nights you never put the mask on, or mid-import partial DATALOG."
                    ),
                ))
                if verbose:
                    print(f"  {night_date}: 0 sessions after filtering — skipped")
                continue

            summary, all_events = build_summary(
                night_date, non_empty, equipment_settings=equipment_settings,
            )

            # Dedup-on-date: re-import overwrites prior data for this night.
            nights_repo.delete_for_date(db, night_date)
            events_repo.delete_for_date(db, night_date)
            nights_repo.upsert(db, summary)
            events_repo.bulk_insert(db, all_events)

            # Time-series — clear and rewrite per night.
            if include_timeseries:
                ts_rows = 0
                for series_name in ("pressure", "leak", "flow_limit", "tidal_volume",
                                    "minute_vent", "resp_rate", "snore"):
                    timeseries_repo.delete_for_date(db, series_name, night_date)
                for s in non_empty:
                    ts_rows += _write_session_timeseries(db, night_date, s)
                if verbose:
                    print(f"  {night_date}: wrote {ts_rows} time-series rows")

            nights_imported += 1
            if earliest is None or summary.date < earliest.date:
                earliest = summary
            if latest is None or summary.date > latest.date:
                latest = summary
            if verbose:
                print(
                    f"  {night_date}: sessions={summary.session_count} "
                    f"AHI={summary.total_ahi:.3f} events={len(all_events)}"
                )
        except Exception as e:
            # Per-night isolation: log the traceback to container stderr
            # (visible in `docker logs ursa-oscar-api`), record a short
            # reason on the ImportLogEntry, move to the next night.
            print(
                f"  {night_date}: skipped — {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            skipped.append(SkippedNight(
                date=night_date,
                reason=f"{type(e).__name__}: {e}",
            ))
            continue

    # Status logic:
    # - completed:  at least one night imported (possibly with skips)
    # - failed:     zero nights imported AND at least one error tried to import
    # - completed:  zero nights AND zero errors (i.e., source dir had no night dirs at all)
    if nights_imported > 0:
        status = "completed"
        error_message: str | None = None
    elif skipped:
        status = "failed"
        # Aggregate reasons by frequency so the UI gets a useful summary.
        error_message = (
            f"All {len(skipped)} night dir(s) failed to import. "
            f"First error: {skipped[0].reason}"
        )
    else:
        status = "completed"
        error_message = None

    return ImportLogEntry(
        source_path=str(source_path),
        nights_imported=nights_imported,
        earliest_date=earliest.date if earliest else None,
        latest_date=latest.date if latest else None,
        status=status,
        error_message=error_message,
        nights_skipped=len(skipped),
        skipped=skipped,
    )


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ursa_oscar.ingestion.importer",
        description="Import a DATALOG / SD-card export into the URSA-OSCAR DuckDB.",
    )
    parser.add_argument("source", type=Path, help="Path to a DATALOG dir or SD-card root.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override URSA_OSCAR_DB_PATH from env.",
    )
    parser.add_argument(
        "--include-timeseries",
        action="store_true",
        help="Also write the high-resolution time-series tables (slow, large).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    db_path = args.db_path if args.db_path is not None else settings.db_path
    db = DuckDBManager(db_path, read_only=False)
    apply_migrations(db)

    started = time.monotonic()
    log_entry = import_path(
        args.source, db,
        include_timeseries=args.include_timeseries,
        verbose=args.verbose,
    )
    elapsed = time.monotonic() - started
    db.close()

    print(
        f"Imported {log_entry.nights_imported} nights from {log_entry.source_path} "
        f"in {elapsed:.1f}s "
        f"(range: {log_entry.earliest_date} → {log_entry.latest_date})"
    )
    return 0 if log_entry.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(_cli())
