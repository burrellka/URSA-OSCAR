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
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from ..analytics.edf_parser import WaveformSignal, discover_sessions
from ..analytics.recompute_summary import recompute_for_date
from ..analytics.session_analyzer import SessionAggregate, analyze_session
from ..analytics.settings_parser import parse_equipment_settings
from ..analytics.summary_builder import build_summary
from ..config import get_settings
from ..models.domain import ImportLogEntry, NightlySummary, SkippedNight
from ..storage.db import DuckDBManager
from ..storage.migrations import apply_migrations
from ..storage.repositories import events as events_repo
from ..storage.repositories import nights as nights_repo
from ..storage.repositories import sessions as sessions_repo
from ..storage.repositories import timeseries as timeseries_repo
from .airsense11_layout import list_night_dirs


def _has_exclusions(db: DuckDBManager, night_date) -> bool:
    """Lightweight probe — does this night have any excluded_sessions
    rows? Used by the importer to decide whether to call the
    recompute path after a fresh write. Skipping the recompute on
    the common no-exclusions case keeps re-imports fast."""
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT 1 FROM excluded_sessions WHERE date = ? LIMIT 1",
            (night_date,),
        ).fetchone()
    return row is not None


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
    skip_existing: bool = True,
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

    **skip_existing** (default True, 0.6.3): if a `nightly_summary` row
    already exists for a night's date, skip parsing it entirely. Counts
    land on `nights_skipped_existing`. Pass `skip_existing=False` to force
    a full re-parse (used by the `force=true` upload query param). This
    cuts a 66-night re-import from minutes to seconds on the happy path
    where the operator just plugged the same SD card in again.

    Returns a populated ImportLogEntry. The caller is responsible for
    persisting it (the importer doesn't write to import_log itself so it
    works against a read-only DB if dry-running).
    """
    source_path = Path(source_path)
    started_at = datetime.now(timezone.utc)
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
    nights_skipped_existing = 0
    earliest: NightlySummary | None = None
    latest: NightlySummary | None = None
    skipped: list[SkippedNight] = []

    for night_date, datalog_dir in night_dirs:
        # 0.6.3 — skip nights already in the DB unless the caller
        # explicitly asked for a force re-parse. This is the dominant
        # path when an operator re-uploads the same SD card after
        # adding a few new nights.
        if skip_existing and nights_repo.date_exists(db, night_date):
            nights_skipped_existing += 1
            if verbose:
                print(f"  {night_date}: already in DB — skipped (use force=true to overwrite)")
            continue
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
            # Sessions get wiped + rewritten the same way; excluded_sessions
            # is left intact so operator exclusions persist across
            # re-imports (architect decision — orphan rows acceptable).
            nights_repo.delete_for_date(db, night_date)
            events_repo.delete_for_date(db, night_date)
            sessions_repo.delete_for_date(db, night_date)
            nights_repo.upsert(db, summary)
            events_repo.bulk_insert(db, all_events)

            # Phase 4 Ticket 1 — write the per-session canonical record
            # alongside summary + events. mask_on_minutes comes straight
            # off the SessionAggregate (duration of the longest non-empty
            # EDF for the session). recompute_summary() will join against
            # this table to redo the night math when an exclusion toggles.
            for sess in non_empty:
                sessions_repo.upsert_session(
                    db,
                    date=night_date,
                    session_id=sess.session_id,
                    start_ts=sess.start,
                    end_ts=sess.end,
                    mask_on_minutes=sess.duration_minutes,
                )

            # Phase 4 Ticket 1 — preserve exclusions across re-imports.
            # The importer just wrote the "all sessions included" summary
            # straight from EDF math; if the operator had previously
            # excluded any of this night's sessions, we need to recompute
            # back to their preferred view. The recompute reads sessions
            # + excluded_sessions + nightly_events + timeseries from the
            # DB (no EDF re-parse needed). Cheap when there are no
            # exclusions — recompute is still called but produces a
            # numerically-identical summary and only bumps last_updated.
            # We skip that cheap-but-pointless call to keep imports fast.
            if _has_exclusions(db, night_date):
                recompute_for_date(db, night_date)

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

            # v6 — per-session pressure-stat cache. Runs AFTER timeseries
            # are written (the percentile queries read from them) and
            # AFTER sessions are upserted (the helper walks the sessions
            # table to find rows needing computation). Idempotent — if
            # the operator imports without include_timeseries=True, the
            # filter `pressure_median IS NULL` still matches and the
            # helper computes NULLs for each channel (timeseries tables
            # are empty for this date), which the Sessions CSV exporter
            # renders as zero. A later import with include_timeseries=True
            # will refill the stats once a `delete_for_date` resets the
            # rows (sessions_repo.delete_for_date above did so).
            from ..storage.migrations import backfill_session_pressure_stats
            backfill_session_pressure_stats(db, date_filter=night_date)

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

    # Phase 3 Item 1C + Phase 5 Ticket 0.5: tri-state status discriminator.
    #
    # Branches, in priority order:
    #   completed — every attempted night landed cleanly; OR every night
    #               was already in the DB and got deduped; OR the source
    #               had no recognizable nights at all.
    #   partial   — at least one new night landed AND at least one was
    #               skipped (with reason); OR no new nights landed BUT
    #               some skipped (errors) AND some known (deduped) — the
    #               operator should look at the skipped list.
    #   failed    — no nights landed AND no nights deduped; either every
    #               attempted dir errored or the call asked for an import
    #               but found nothing usable.
    #
    # The Phase 5 fix corrects the case Kevin hit in the field: when 0
    # nights imported BUT 28 were already known AND 36 errored, the
    # previous logic reported "failed" — but the import ran fine; 28
    # known nights are still in the DB. The new logic surfaces "partial"
    # which renders an amber badge + invites a look at the skipped list,
    # without alarming the operator unnecessarily.
    skipped_count = len(skipped)
    has_imported = nights_imported > 0
    has_errors = skipped_count > 0
    has_dedup = nights_skipped_existing > 0

    error_message: str | None = None
    if has_imported and not has_errors:
        status = "completed"
    elif has_imported and has_errors:
        status = "partial"
        error_message = (
            f"{nights_imported} night(s) imported; {skipped_count} skipped. "
            f"See the skipped list for per-night reasons."
        )
    elif has_errors and has_dedup:
        # Operator's screenshot case: 0 new, some known, some errored.
        # Not "failed" — the import ran fine; some nights just have no
        # usable EDF data. Partial draws attention to the skip list
        # without painting the result tile red.
        status = "partial"
        error_message = (
            f"No new nights to import. {nights_skipped_existing} already known; "
            f"{skipped_count} skipped (no usable EDF data). See the skipped "
            f"list for per-night reasons."
        )
    elif has_errors:
        # All attempted dirs errored AND nothing was even already in the DB.
        # This is the real failure case — nothing worked.
        status = "failed"
        error_message = (
            f"All {skipped_count} night dir(s) failed to import. "
            f"First error: {skipped[0].reason}"
        )
    else:
        # Either: pure-dedup re-import (0 new, N known, 0 errors), OR an
        # empty source (0 new, 0 known, 0 errors). Both are benign.
        status = "completed"

    return ImportLogEntry(
        source_path=str(source_path),
        nights_imported=nights_imported,
        earliest_date=earliest.date if earliest else None,
        latest_date=latest.date if latest else None,
        status=status,
        error_message=error_message,
        nights_skipped=len(skipped),
        skipped=skipped,
        nights_skipped_existing=nights_skipped_existing,
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-parse nights already in the DB. Default skips them for speed.",
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
        skip_existing=not args.force,
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
