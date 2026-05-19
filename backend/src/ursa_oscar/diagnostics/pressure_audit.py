"""Diagnostic: audit PLD.edf files to identify the root cause of NULL
median_pressure on ~90% of recent nights.

Background — long-parked since Ticket 6.1 close (Doc 29). The
nightly_summary.median_pressure field is computed from PLD.edf's
``Press.2s`` channel via two code paths:

  - Import (analytics/summary_builder.py:101 reads from
    session_analyzer.py:105's ``pld_signals.get("Press.2s")``)
  - Recompute (analytics/recompute_summary.py:243 SELECTs from
    pressure_timeseries, which only gets rows when the import path
    populated them)

Both paths use the same single-label match against ``"Press.2s"``. If
PLD.edf labels its pressure channel differently — or if PLD.edf is
missing entirely for some sessions — median_pressure ends up NULL.

This script walks /cpap-import/DATALOG/YYYYMMDD/ and inventories every
PLD*.edf it finds, recording:

  - File path (date + session prefix)
  - Header metadata (n_records, record_duration, n_signals,
    start_datetime, EDF+ reserved field which often carries firmware
    info on ResMed devices)
  - Channel labels (from MNE waveform parse — slow but authoritative)
  - File size + computed duration

It then clusters nights by their PLD label-set fingerprint and outputs
JSON to stdout. The expected pattern is one of:

  - Label drift (e.g., some nights have ``Press.2s``, others have
    ``MaskPress.2s`` or ``Pressure.2s``) → extend the parser channel
    map
  - Missing PLD files for some sessions → log warning at import,
    cannot retroactively fix
  - Aggregation logic edge case → fix recompute_summary
  - Firmware version variant → extend version-specific parsing

Usage (filesystem-only; does NOT open DuckDB so it's safe to run
against a live api container):

  # On the docker host:
  docker cp backend/src/ursa_oscar/diagnostics/pressure_audit.py \\
      ursa-oscar-api:/tmp/pressure_audit.py
  docker exec ursa-oscar-api python /tmp/pressure_audit.py > audit.json

Output format: JSON document with two top-level keys:

  - "per_night": list of {date, sessions: [{prefix, pld_path, ...}]}
  - "label_set_clusters": {fingerprint: [dates...]} so it's obvious
    at a glance whether the universe is one cluster or several
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Per-file audit
# ---------------------------------------------------------------------------


@dataclass
class PldFileAudit:
    """Everything we capture from a single PLD.edf."""
    path: str
    session_prefix: str
    file_size_bytes: int
    # From the 256-byte EDF main header (no MNE needed):
    header_start_datetime: str | None = None
    header_n_records: int | None = None
    header_record_duration_seconds: float | None = None
    header_n_signals: int | None = None
    header_reserved: str | None = None   # often "EDF+D" or carries firmware info
    header_recording_id: str | None = None  # EDF spec offset 88-168
    header_patient_id: str | None = None    # EDF spec offset 8-88
    # From the MNE waveform parse:
    channel_labels: list[str] = field(default_factory=list)
    has_press_2s: bool = False
    # Diagnostics for "PLD.edf opened but no data":
    channel_count_loaded: int = 0
    computed_duration_seconds: float | None = None
    parse_error: str | None = None


@dataclass
class NightAudit:
    """All PLD files for one DATALOG/YYYYMMDD/ directory."""
    date: str   # YYYY-MM-DD format
    datalog_dir: str
    pld_files: list[PldFileAudit] = field(default_factory=list)
    n_sessions_with_pld: int = 0
    union_channel_labels: list[str] = field(default_factory=list)
    label_set_fingerprint: str = ""
    has_any_press_2s: bool = False
    notes: list[str] = field(default_factory=list)
    # 0.13.4 — full file inventory for the DATALOG/YYYYMMDD/ dir.
    # Distinguishes "empty dir" (no therapy session) from "EVE/BRP
    # present but no PLD" (firmware quirk / partial session).
    all_files_by_kind: dict[str, list[str]] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    is_empty_dir: bool = False


# ---------------------------------------------------------------------------
# Header parsing — pure Python, no MNE
# ---------------------------------------------------------------------------


def _read_edf_main_header(path: Path) -> dict[str, Any]:
    """Parse the 256-byte EDF main header into a dict. Same byte layout
    as analytics/edf_parser.py::parse_header but with extra fields."""
    with open(path, "rb") as f:
        raw = f.read(256)
    if len(raw) < 256:
        return {"_too_short": True}

    def _slice(start: int, end: int) -> str:
        return raw[start:end].decode("ascii", errors="replace").strip()

    return {
        # 0-8: version (usually "0")
        "version": _slice(0, 8),
        # 8-88: patient identification
        "patient_id": _slice(8, 88),
        # 88-168: recording identification — ResMed often packs firmware here
        "recording_id": _slice(88, 168),
        "startdate": _slice(168, 176),
        "starttime": _slice(176, 184),
        # 184-192: bytes in header record (ignored)
        # 192-236: reserved — EDF+D marker
        "reserved": _slice(192, 236),
        "n_records": int(_slice(236, 244)) if _slice(236, 244).lstrip("-").isdigit() else None,
        "record_duration_seconds": (
            float(_slice(244, 252)) if _slice(244, 252) else None
        ),
        "n_signals": int(_slice(252, 256)) if _slice(252, 256).lstrip("-").isdigit() else None,
    }


def _safe_parse_datetime(startdate: str, starttime: str) -> str | None:
    """EDF: 'dd.mm.yy' + 'hh.mm.ss'. Return ISO string or None on bad input."""
    try:
        dt = datetime.strptime(f"{startdate} {starttime}", "%d.%m.%y %H.%M.%S")
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Channel-label parsing — uses MNE (already a backend dep)
# ---------------------------------------------------------------------------


def _read_channel_labels(path: Path) -> tuple[list[str], int, str | None]:
    """Return (labels, n_channels_loaded, parse_error_or_None)."""
    try:
        import mne
    except ImportError:
        return ([], 0, "mne not installed")
    try:
        raw = mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
        return (list(raw.ch_names), len(raw.ch_names), None)
    except Exception as e:
        return ([], 0, f"{type(e).__name__}: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def _find_pld_files(datalog_dir: Path) -> list[Path]:
    """Find all PLD*.edf files in a DATALOG/YYYYMMDD/ directory.
    Sessions are timestamp-prefixed, e.g., '20260508_220523_PLD.edf'."""
    if not datalog_dir.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(datalog_dir.iterdir()):
        name = p.name
        # Match anything that ends in 'PLD.edf' (case-insensitive).
        if name.lower().endswith("pld.edf"):
            out.append(p)
    return out


def _inventory_datalog_dir(datalog_dir: Path) -> tuple[dict[str, list[str]], int, int]:
    """List EVERY file in the DATALOG/YYYYMMDD/ dir, categorized by EDF
    kind (CSL/EVE/BRP/PLD/SA2/OTHER). Returns (by_kind, total_files,
    total_bytes). This is the diagnostic for nights that have no PLD —
    we need to know whether EVE/BRP exist (firmware quirk) or the dir
    is totally empty (no therapy session ran)."""
    by_kind: dict[str, list[str]] = {
        "CSL": [], "EVE": [], "BRP": [], "PLD": [], "SA2": [], "OTHER": [],
    }
    total_files = 0
    total_bytes = 0
    if not datalog_dir.is_dir():
        return by_kind, 0, 0
    for p in sorted(datalog_dir.iterdir()):
        if not p.is_file():
            continue
        total_files += 1
        try:
            total_bytes += p.stat().st_size
        except OSError:
            pass
        name = p.name
        # Filenames look like "20260508_220523_PLD.edf" — the kind is
        # the segment between the last "_" and the ".edf".
        stem_lower = name.lower()
        matched = False
        for kind in ("csl", "eve", "brp", "pld", "sa2"):
            if stem_lower.endswith(f"_{kind}.edf"):
                by_kind[kind.upper()].append(name)
                matched = True
                break
        if not matched:
            by_kind["OTHER"].append(name)
    return by_kind, total_files, total_bytes


def _session_prefix_from_filename(filename: str) -> str:
    """Extract the YYYYMMDD_HHMMSS prefix from e.g.
    '20260508_220523_PLD.edf' → '20260508_220523'."""
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return stem


def _audit_one_pld(path: Path) -> PldFileAudit:
    audit = PldFileAudit(
        path=str(path),
        session_prefix=_session_prefix_from_filename(path.name),
        file_size_bytes=path.stat().st_size,
    )

    try:
        hdr = _read_edf_main_header(path)
    except Exception as e:
        audit.parse_error = f"header read failed: {type(e).__name__}: {e}"
        return audit

    if hdr.get("_too_short"):
        audit.parse_error = "file too short for EDF header"
        return audit

    audit.header_start_datetime = _safe_parse_datetime(
        hdr.get("startdate", ""), hdr.get("starttime", ""),
    )
    audit.header_n_records = hdr.get("n_records")
    audit.header_record_duration_seconds = hdr.get("record_duration_seconds")
    audit.header_n_signals = hdr.get("n_signals")
    audit.header_reserved = hdr.get("reserved")
    audit.header_recording_id = hdr.get("recording_id")
    audit.header_patient_id = hdr.get("patient_id")

    if audit.header_n_records is not None and audit.header_record_duration_seconds is not None:
        audit.computed_duration_seconds = (
            audit.header_n_records * audit.header_record_duration_seconds
        )

    # If the file is a zero-record placeholder, MNE will choke. Skip it.
    if audit.header_n_records in (0, None):
        audit.parse_error = (
            audit.parse_error or
            f"n_records={audit.header_n_records} — placeholder/empty PLD, no signals to parse"
        )
        return audit

    labels, n_loaded, err = _read_channel_labels(path)
    audit.channel_labels = labels
    audit.channel_count_loaded = n_loaded
    if err:
        audit.parse_error = err
    audit.has_press_2s = "Press.2s" in labels
    return audit


def _audit_one_night(datalog_dir: Path) -> NightAudit:
    """Audit every PLD.edf in one DATALOG/YYYYMMDD/ directory."""
    raw_date = datalog_dir.name
    # YYYYMMDD → YYYY-MM-DD for readable output
    iso_date = (
        f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        if len(raw_date) == 8 and raw_date.isdigit()
        else raw_date
    )

    night = NightAudit(date=iso_date, datalog_dir=str(datalog_dir))

    # 0.13.4 — always inventory the full dir, even when there are no
    # PLD files. This is what distinguishes "no therapy session"
    # (empty dir) from "events recorded but no waveform" (EVE present,
    # PLD missing).
    by_kind, total_files, total_bytes = _inventory_datalog_dir(datalog_dir)
    night.all_files_by_kind = by_kind
    night.total_files = total_files
    night.total_bytes = total_bytes
    night.is_empty_dir = (total_files == 0)

    pld_paths = _find_pld_files(datalog_dir)
    if not pld_paths:
        if night.is_empty_dir:
            night.notes.append("dir is empty — no therapy session recorded")
        else:
            non_pld_summary = ", ".join(
                f"{kind}={len(by_kind[kind])}"
                for kind in ("CSL", "EVE", "BRP", "SA2", "OTHER")
                if by_kind[kind]
            )
            night.notes.append(
                f"no PLD.edf but {non_pld_summary} present — partial session?"
            )
        return night

    for pld in pld_paths:
        try:
            night.pld_files.append(_audit_one_pld(pld))
        except Exception as e:
            night.notes.append(f"audit of {pld.name} raised {type(e).__name__}: {e}")

    night.n_sessions_with_pld = sum(
        1 for f in night.pld_files if f.channel_count_loaded > 0
    )

    # Union channel labels across all sessions for this night.
    union: set[str] = set()
    for f in night.pld_files:
        union.update(f.channel_labels)
    night.union_channel_labels = sorted(union)
    # Fingerprint = sorted union; nights with the same fingerprint are
    # candidates for the same root-cause cluster.
    night.label_set_fingerprint = "|".join(night.union_channel_labels)
    night.has_any_press_2s = any(f.has_press_2s for f in night.pld_files)
    return night


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def run_audit(cpap_import_root: Path, max_nights: int | None = None) -> dict[str, Any]:
    """Walk cpap_import_root/DATALOG/* and audit every night."""
    datalog_root = cpap_import_root / "DATALOG"
    if not datalog_root.is_dir():
        return {
            "error": f"DATALOG dir not found at {datalog_root}",
            "cpap_import_root": str(cpap_import_root),
        }

    night_dirs = sorted(
        d for d in datalog_root.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 8
    )
    if max_nights is not None:
        night_dirs = night_dirs[-max_nights:]   # most recent N

    night_audits: list[NightAudit] = []
    for d in night_dirs:
        print(f"[audit] {d.name} ...", file=sys.stderr, flush=True)
        night_audits.append(_audit_one_night(d))

    # Cluster nights by label-set fingerprint.
    clusters: dict[str, list[str]] = {}
    for n in night_audits:
        clusters.setdefault(n.label_set_fingerprint, []).append(n.date)

    # 0.13.4 — partition the "no PLD" nights into two diagnostic buckets:
    #  * empty dir → no therapy session ran (NULL is correct)
    #  * non-empty dir without PLD → device recorded EVE/BRP/etc. but
    #    not the pressure waveform (firmware/short-session edge case)
    empty_dir_dates = [n.date for n in night_audits if n.is_empty_dir]
    no_pld_but_has_other_dates = [
        n.date for n in night_audits
        if not n.pld_files and not n.is_empty_dir
    ]
    no_pld_but_has_other_examples = [
        {
            "date": n.date,
            "files_by_kind": {k: len(v) for k, v in n.all_files_by_kind.items() if v},
            "total_bytes": n.total_bytes,
            "notes": n.notes,
        }
        for n in night_audits[:200]
        if not n.pld_files and not n.is_empty_dir
    ][:10]

    summary = {
        "total_nights_walked": len(night_audits),
        "nights_with_press_2s_in_at_least_one_session": sum(
            1 for n in night_audits if n.has_any_press_2s
        ),
        "nights_with_no_press_2s_anywhere": sum(
            1 for n in night_audits if not n.has_any_press_2s
        ),
        "nights_with_zero_pld_files": sum(
            1 for n in night_audits if not n.pld_files
        ),
        # NEW partitioning of the no-PLD bucket:
        "no_pld_breakdown": {
            "empty_dir_count": len(empty_dir_dates),
            "non_empty_dir_without_pld_count": len(no_pld_but_has_other_dates),
            "empty_dir_dates": empty_dir_dates,
            "non_empty_dir_without_pld_dates": no_pld_but_has_other_dates,
            "non_empty_dir_without_pld_examples": no_pld_but_has_other_examples,
        },
        "label_set_cluster_count": len(clusters),
        "cluster_sizes": {fp: len(dates) for fp, dates in clusters.items()},
    }

    return {
        "summary": summary,
        "label_set_clusters": {
            fp: {"n_nights": len(dates), "example_dates": dates[:5], "all_dates": dates}
            for fp, dates in sorted(clusters.items(), key=lambda kv: -len(kv[1]))
        },
        "per_night": [asdict(n) for n in night_audits],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit PLD.edf files to diagnose NULL median_pressure.",
    )
    parser.add_argument(
        "--cpap-import",
        default=os.environ.get("URSA_OSCAR_CPAP_IMPORT_PATH", "/cpap-import"),
        help="Root of the bind-mounted CPAP source (default: /cpap-import)",
    )
    parser.add_argument(
        "--max-nights",
        type=int,
        default=None,
        help="Cap to the N most recent night dirs (default: all)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only the summary + cluster sizes (no per-night detail)",
    )
    args = parser.parse_args(argv)

    result = run_audit(Path(args.cpap_import), max_nights=args.max_nights)
    if args.summary_only:
        result.pop("per_night", None)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
