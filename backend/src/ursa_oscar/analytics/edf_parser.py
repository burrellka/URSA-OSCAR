"""EDF parser for ResMed AirSense 11 SD-card exports.

Two parsing paths because the AirSense 11 produces two distinct EDF flavors:

1. Annotation-only EDF+D files (EVE.edf, CSL.edf) — event log encoded as
   TAL (Time-Stamped Annotation List) in the "EDF Annotations" channel.
   pyedflib refuses these as "discontinuous"; MNE drops the per-event
   annotations and only surfaces "Recording starts". We parse raw bytes.

2. Waveform / channel EDF+D files (BRP.edf @ 25 Hz, PLD.edf @ 0.5 Hz,
   SA2.edf @ 1 Hz) — readable by MNE's `read_raw_edf`. We extract signals
   as numpy arrays with absolute timestamps.

Output records use session-relative wall-clock time anchored to the EDF
header's startdatetime, so multiple sessions in one night can be merged by
the session_analyzer without timezone gymnastics.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import numpy as np

logger = logging.getLogger(__name__)

# 1.1.8 — track which raw labels we've already warned about so the log
# doesn't repeat once per event for the same unknown label. One WARN
# per unique raw label per process lifetime. Reset on container restart,
# which is fine — the point is to surface the label to the operator
# once so they can report it for a future EVENT_LABEL_MAP addition.
_UNMAPPED_LABELS_LOGGED: set[str] = set()


# --- Event-type normalization ---------------------------------------------
# Annotation texts that aren't real events — these are EDF+D housekeeping
# emitted at the start/end of each session and should be filtered out before
# event counting.
NON_EVENT_LABELS: frozenset[str] = frozenset({
    "Recording starts",
    "Recording ends",
})

# Maps the raw text label from ResMed's EVE.edf TAL to our canonical
# event_type enum (per models.domain.EventType). The mapping is curated from
# observed labels on Kevin's AirSense 11 fixtures and from OSCAR's
# resmed_loader.cpp event taxonomy. Anything unknown falls through to its raw
# label so we can surface it as an anomaly during regression.
EVENT_LABEL_MAP: dict[str, str] = {
    "Central Apnea": "ClearAirway",
    "Obstructive Apnea": "Obstructive",
    "Apnea": "Apnea",
    "Hypopnea": "Hypopnea",
    "RERA": "RERA",
    "Respiratory Effort Related Arousal": "RERA",
    # AirSense 11 emits "Arousal"; OSCAR's Summary CSV "RE Count" column
    # treats it as RERA per AASM convention (see canonical_targets.py §2).
    "Arousal": "RERA",
    "Large Leak": "LargeLeak",
    "LL": "LargeLeak",
    "Cheyne-Stokes Respiration": "CheyneStokes",
    "CSR": "CheyneStokes",
    "Periodic Breathing": "PeriodicBreathing",
    "PB": "PeriodicBreathing",
    "Flow Limitation": "FlowLimit",
    "FL": "FlowLimit",
}


# --- Data classes ---------------------------------------------------------

@dataclass(frozen=True)
class EdfHeader:
    """Subset of the 256-byte EDF main header we care about."""
    path: Path
    start_datetime: datetime
    n_records: int
    record_duration_seconds: float
    n_signals: int
    is_edf_plus_d: bool


@dataclass(frozen=True)
class ParsedEvent:
    """One TAL-decoded event from an EVE.edf annotation channel."""
    timestamp: datetime          # absolute, derived from session start + onset
    raw_label: str               # text from the TAL (e.g., 'Central Apnea')
    event_type: str              # normalized via EVENT_LABEL_MAP, fallback to raw
    duration_seconds: float


@dataclass
class WaveformSignal:
    """One channel from a BRP / PLD / SA2 EDF, materialized as numpy arrays."""
    label: str
    unit: str
    sample_rate_hz: float
    start_datetime: datetime
    values: np.ndarray           # 1-D float
    # Convenience: derive timestamp array on demand via .timestamps()

    def timestamps(self) -> np.ndarray:
        """Return absolute timestamps as numpy datetime64[ms] for each sample."""
        n = self.values.size
        offsets_ms = (np.arange(n, dtype=np.int64) * (1000.0 / self.sample_rate_hz)).astype(np.int64)
        start_ms = np.datetime64(self.start_datetime).astype("datetime64[ms]")
        return start_ms + offsets_ms.astype("timedelta64[ms]")


@dataclass(frozen=True)
class SessionEDFs:
    """All five EDF flavors for one session, paired by their shared timestamp.

    1.1.10 — ResMed occasionally records multiple near-adjacent sub-sessions
    within a single mask-on period (mask-on test at 21:59:39, real sleep
    starting 22:00:00, etc.). Each sub-session writes its own CSL.edf +
    EVE.edf at boot, but the BRP/PLD/SA2 waveforms only start once for the
    whole mask-on period. URSA clusters all of these into one logical
    session via the 30-second sliding window. The annotation files
    (CSL/EVE) need to be carried as TUPLES so events from each sub-session
    can be combined; the waveform files (BRP/PLD/SA2) remain singletons.

    The legacy single-path fields (eve_path / csl_path) are retained as
    "first in the cluster" pointers for backward compat with any code
    that hasn't been updated to the tuples. New code should iterate
    eve_paths / csl_paths.
    """
    session_timestamp_str: str   # e.g., "20260508_220523" — the filename prefix
    csl_path: Path | None        # 1.1.10: first CSL in cluster (compat shim)
    eve_path: Path | None        # 1.1.10: first EVE in cluster (compat shim)
    brp_path: Path | None
    pld_path: Path | None
    sa2_path: Path | None
    # 1.1.10 — all CSLs / EVEs in the cluster, chronological. eve_path /
    # csl_path point at eve_paths[0] / csl_paths[0] respectively (or None
    # when the tuple is empty).
    csl_paths: tuple[Path, ...] = ()
    eve_paths: tuple[Path, ...] = ()

    @property
    def session_start(self) -> datetime:
        """Parse the timestamp prefix into a datetime."""
        return datetime.strptime(self.session_timestamp_str, "%Y%m%d_%H%M%S")


# --- Header parsing -------------------------------------------------------

def parse_header(path: Path) -> EdfHeader:
    """Read the 256-byte main header of an EDF file. Pure-Python, no deps."""
    path = Path(path)
    with open(path, "rb") as f:
        raw = f.read(256)
    if len(raw) < 256:
        raise ValueError(f"{path}: file too short for an EDF header ({len(raw)} bytes)")

    # Per the EDF spec, the main header is 256 ASCII bytes laid out as fixed
    # offsets. Numbers are space-padded ASCII.
    startdate = raw[168:176].decode("ascii").strip()       # dd.mm.yy
    starttime = raw[176:184].decode("ascii").strip()       # hh.mm.ss
    reserved = raw[192:236].decode("ascii").strip()
    n_records = int(raw[236:244].decode("ascii").strip())
    record_dur = float(raw[244:252].decode("ascii").strip())
    n_signals = int(raw[252:256].decode("ascii").strip())

    # Parse dd.mm.yy hh.mm.ss into a datetime. EDF specifies 2-digit years
    # with the EDF+ clarification that 1985..2084 maps via the "year >= 85"
    # rule. ResMed uses 4-digit conceptually but encodes 2-digit here — 26
    # means 2026. The fixtures confirm this works.
    dt = datetime.strptime(f"{startdate} {starttime}", "%d.%m.%y %H.%M.%S")
    return EdfHeader(
        path=path,
        start_datetime=dt,
        n_records=n_records,
        record_duration_seconds=record_dur,
        n_signals=n_signals,
        is_edf_plus_d=reserved.startswith("EDF+D"),
    )


def _signal_header_offset(n_signals: int, *, field_offset: int, signal_idx: int) -> int:
    """Return file offset for one field of one signal's header.

    EDF signal headers are stored as:
       [label x n] [transducer x n] [phys_dim x n] [phys_min x n] [phys_max x n]
       [dig_min x n] [dig_max x n] [prefilter x n] [n_samples x n] [reserved x n]

    Each field is fixed-width (16, 80, 8, 8, 8, 8, 8, 80, 8, 32 bytes).
    """
    return 256 + (field_offset * n_signals) + (signal_idx * _FIELD_WIDTH[field_offset])


# 16/80/8/8/8/8/8/80/8/32 — indexed by "field number" 0..9
_FIELD_WIDTH = [16, 80, 8, 8, 8, 8, 8, 80, 8, 32]
_FIELD_OFFSETS = [sum(_FIELD_WIDTH[:i]) for i in range(len(_FIELD_WIDTH))]


def _signal_field_starts(n_signals: int) -> list[int]:
    """File offset where each per-signal field block starts (post-header)."""
    starts = []
    running = 256
    for w in _FIELD_WIDTH:
        starts.append(running)
        running += w * n_signals
    return starts


# --- TAL annotation parser (event EDFs) -----------------------------------

def parse_events(path: Path) -> list[ParsedEvent]:
    """Read all event annotations from an EVE.edf / CSL.edf file.

    Returns events in chronological order. Empty list if the file has no
    annotations (some BRP/PLD-only sessions don't get a populated EVE).
    """
    path = Path(path)
    hdr = parse_header(path)
    if hdr.n_records == 0:
        return []

    with open(path, "rb") as f:
        data = f.read()

    # Signal labels (16-byte block) — find the 'EDF Annotations' channel
    sig_starts = _signal_field_starts(hdr.n_signals)
    label_start = sig_starts[0]
    labels = [
        data[label_start + i * 16: label_start + (i + 1) * 16].decode("ascii").strip()
        for i in range(hdr.n_signals)
    ]
    try:
        ann_idx = labels.index("EDF Annotations")
    except ValueError:
        return []

    # Samples per record per signal (n_samples is field index 8)
    n_samples_start = sig_starts[8]
    n_samples = [
        int(data[n_samples_start + i * 8: n_samples_start + (i + 1) * 8].decode("ascii").strip())
        for i in range(hdr.n_signals)
    ]

    # Each sample is 2 bytes (int16). Record size:
    bytes_per_record = sum(n_samples) * 2
    pre_ann_bytes = sum(n_samples[:ann_idx]) * 2
    ann_bytes_per_record = n_samples[ann_idx] * 2

    data_start = 256 + 256 * hdr.n_signals  # main hdr + per-signal hdrs

    events: list[ParsedEvent] = []
    for rec in range(hdr.n_records):
        rec_off = data_start + rec * bytes_per_record
        ann_blob = data[rec_off + pre_ann_bytes: rec_off + pre_ann_bytes + ann_bytes_per_record]
        events.extend(_parse_tal_block(ann_blob, hdr.start_datetime))

    # Sort by absolute timestamp — guards against TALs out-of-order within a
    # record (rare but seen on edge-case ResMed exports).
    events.sort(key=lambda e: e.timestamp)
    return events


def _parse_tal_block(blob: bytes, session_start: datetime) -> Iterator[ParsedEvent]:
    """Decode one record's annotation bytes into ParsedEvent objects.

    TAL format (EDF+ spec, simplified for AirSense 11 usage):
        +<onset>[\\x15<duration>]\\x14<text>[\\x14<text>]*\\x14\\x00

    The first TAL of each record is the timing anchor (text is empty); we
    skip it. Subsequent TALs encode actual events.

    Padding after the final TAL is \\x00 bytes — we stop on the first \\x00
    that follows a \\x14 terminator.
    """
    i = 0
    n = len(blob)
    first_tal = True
    while i < n:
        # Skip leading padding nulls
        while i < n and blob[i] == 0x00:
            i += 1
        if i >= n:
            return

        # Each TAL starts with '+' or '-'
        if blob[i] not in (0x2B, 0x2D):
            return  # malformed; stop

        # Walk forward to the first \x14 — collect onset (and optional \x15 duration)
        start = i
        while i < n and blob[i] not in (0x14,):
            i += 1
        if i >= n:
            return  # truncated
        onset_field = blob[start:i].decode("ascii", errors="replace")
        # Onset may contain a \x15 duration separator
        if "\x15" in onset_field:
            onset_s, dur_s = onset_field.split("\x15", 1)
        else:
            onset_s, dur_s = onset_field, ""
        try:
            onset = float(onset_s)
        except ValueError:
            return
        try:
            duration = float(dur_s) if dur_s else 0.0
        except ValueError:
            duration = 0.0

        # Skip the \x14 terminator of the onset/duration block
        i += 1
        # Annotation text follows, terminated by \x14. Multiple texts can be
        # concatenated with \x14 separators; AirSense 11 generally emits one.
        text_start = i
        while i < n and blob[i] != 0x14:
            i += 1
        text = blob[text_start:i].decode("ascii", errors="replace").strip()
        # Skip the final \x14 of the text section
        if i < n and blob[i] == 0x14:
            i += 1
        # If there's a multi-text TAL (\x14\x14...), consume additional text
        # blocks until the trailing \x00 padding.
        while i < n and blob[i] not in (0x00,):
            # Either another text continuation or a new TAL marker
            if blob[i] in (0x2B, 0x2D):
                break
            extra_start = i
            while i < n and blob[i] != 0x14:
                i += 1
            extra = blob[extra_start:i].decode("ascii", errors="replace").strip()
            if extra:
                text = (text + " | " + extra) if text else extra
            if i < n and blob[i] == 0x14:
                i += 1

        # Skip the TAL's terminator null if present
        while i < n and blob[i] == 0x00:
            i += 1

        if first_tal:
            # First TAL is the record timing anchor. Per EDF+ spec it has an
            # empty annotation text. Even if a vendor packs a real event into
            # it, we'd ignore it because the offset for the anchor is the
            # record's offset, not an event's. AirSense 11 emits empty text.
            first_tal = False
            if not text:
                continue

        # Filter out session-housekeeping annotations like "Recording starts"
        if text in NON_EVENT_LABELS or not text:
            continue

        # 1.1.8 — surface unmapped raw labels so operators can report
        # them. The fallback (event_type = raw text) still happens and
        # the event still gets stored; we just log it once per process
        # so the next person hits this with a clear pointer.
        if text not in EVENT_LABEL_MAP and text not in _UNMAPPED_LABELS_LOGGED:
            _UNMAPPED_LABELS_LOGGED.add(text)
            logger.warning(
                "EDF parser: saw unmapped event label %r. "
                "Event will be stored with event_type=%r (the raw text). "
                "The Events page filter chips won't catch it. "
                "Please report this label so EVENT_LABEL_MAP can be "
                "extended (github.com/burrellka/URSA-OSCAR/issues).",
                text, text,
            )
        yield ParsedEvent(
            timestamp=session_start + timedelta(seconds=onset),
            raw_label=text,
            event_type=EVENT_LABEL_MAP.get(text, text),
            duration_seconds=duration,
        )


# --- Waveform / channel EDF reader ---------------------------------------

def parse_waveform(path: Path, *, drop_crc: bool = True) -> list[WaveformSignal]:
    """Read all data channels from a BRP / PLD / SA2 file via MNE.

    Returns a list of WaveformSignal objects (one per channel). The Crc16
    channel is dropped by default — it's an integrity check, not analysis
    data, and clutters downstream code.

    MNE is used here because pyedflib rejects ResMed's nrecords=0 placeholder
    files outright; MNE is more forgiving.
    """
    path = Path(path)
    # Quick size/header guard — zero-record files have nothing to return.
    hdr = parse_header(path)
    if hdr.n_records == 0:
        return []

    import mne                                # imported lazily — heavy dep
    raw = mne.io.read_raw_edf(path, preload=True, verbose="ERROR")
    out: list[WaveformSignal] = []
    for idx, ch_name in enumerate(raw.ch_names):
        if drop_crc and ch_name.lower().startswith("crc"):
            continue
        # MNE stores everything in SI units internally. ResMed's units
        # ('cmH2O', 'L/s', etc.) are preserved in raw.info but not applied to
        # values — we need to multiply by the EDF's physical scaling.
        # MNE's read_raw_edf already applies the physical scaling, so values
        # are in the physical unit declared in the file header.
        values = raw.get_data(picks=[idx]).flatten().astype(np.float64)
        unit = raw._raw_extras[0].get("ch_unit", [""] * raw.info["nchan"])[idx] if raw._raw_extras else ""
        sample_rate = float(raw.info["sfreq"])
        # Channels may have different sample rates in some EDFs; MNE picks
        # the highest as sfreq. For ResMed PLD with mixed-rate channels, this
        # is fine — MNE upsamples internally. We record the effective rate.
        out.append(
            WaveformSignal(
                label=ch_name,
                unit=unit,
                sample_rate_hz=sample_rate,
                start_datetime=hdr.start_datetime,
                values=values,
            )
        )
    return out


def parse_pld_signal(path: Path, signal_label: str) -> WaveformSignal | None:
    """Convenience: pull one named signal from a PLD.edf in one call.

    Useful for the summary builder which mostly needs Press.2s, Leak.2s, etc.
    individually rather than the whole bundle.
    """
    for sig in parse_waveform(path):
        if sig.label == signal_label:
            return sig
    return None


# --- Session discovery from a DATALOG day directory ----------------------

def discover_sessions(datalog_dir: Path) -> list[SessionEDFs]:
    """Group EDF files in one DATALOG date directory by session timestamp prefix.

    File naming convention (AirSense 11 firmware):
        <YYYYMMDD>_<HHMMSS>_<KIND>.edf
        where KIND ∈ {CSL, EVE, BRP, PLD, SA2}

    Sessions usually have a CSL + EVE pair (annotation files) at one prefix
    and the BRP/PLD/SA2 trio at a prefix a few seconds later (when the device
    starts logging waveforms after stabilizing). We group by temporal
    proximity: any two files whose timestamps are within
    ``SESSION_GROUPING_WINDOW_SECONDS`` of each other belong to the same
    logical session.

    1.1.3 fix — the prior implementation bucketed by HH:MM (clock-minute
    prefix), which would split a single session whenever the boot moment
    straddled a minute boundary. A common ResMed pattern (CSL/EVE at
    01:04:53, BRP/PLD/SA2 at 01:05:00, only 7s apart) produced two phantom
    sessions because 01:04 and 01:05 hashed to different buckets. Sliding-
    window clustering eliminates the boundary case.
    """
    datalog_dir = Path(datalog_dir)
    files = sorted(datalog_dir.glob("*.edf"))

    # First pass: parse each file's full timestamp.
    parsed: list[tuple[datetime, str, str, Path]] = []
    for f in files:
        stem = f.stem
        try:
            date_part, time_part, kind = stem.split("_", 2)
        except ValueError:
            continue  # unexpected filename; skip
        kind = kind.upper()
        if kind not in {"CSL", "EVE", "BRP", "PLD", "SA2"}:
            continue
        try:
            ts = datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        parsed.append((ts, f"{date_part}_{time_part}", kind, f))

    parsed.sort(key=lambda x: x[0])

    # Second pass: cluster by temporal proximity. A new cluster starts
    # whenever the next file's timestamp exceeds the prior file's by more
    # than SESSION_GROUPING_WINDOW_SECONDS. Real session restarts on a
    # ResMed AirSense 11 are minutes-to-hours apart (mask off for a
    # bathroom break, etc.), well beyond the 60s window.
    clusters: list[dict] = []
    for ts, ts_str, kind, path in parsed:
        if (
            clusters
            and (ts - clusters[-1]["_last_ts"]).total_seconds()
            <= SESSION_GROUPING_WINDOW_SECONDS
        ):
            bucket = clusters[-1]
        else:
            bucket = {"_session_id": ts_str, "_last_ts": ts}
            clusters.append(bucket)
        # Extend the cluster's trailing edge so a sequence of three files
        # spaced 30s apart still clusters together.
        bucket["_last_ts"] = ts
        # 1.1.10 — accumulate annotation files (CSL / EVE) in chronological
        # lists; ResMed can record multiple sub-sessions per mask-on period
        # and each writes its own CSL+EVE. Waveform files (BRP/PLD/SA2)
        # remain first-seen-wins because the device writes one continuous
        # waveform stream per mask-on period.
        if kind in {"CSL", "EVE"}:
            key = f"{kind}_list"
            bucket.setdefault(key, []).append(path)
        else:
            if kind not in bucket:
                bucket[kind] = path
        if ts_str < bucket["_session_id"]:
            bucket["_session_id"] = ts_str

    out: list[SessionEDFs] = []
    for bucket in clusters:
        csl_list = tuple(bucket.get("CSL_list", []))
        eve_list = tuple(bucket.get("EVE_list", []))
        out.append(
            SessionEDFs(
                session_timestamp_str=bucket["_session_id"],
                # Legacy single-path fields point at the first file in the
                # cluster so older callers continue to work. New code
                # should iterate csl_paths / eve_paths.
                csl_path=csl_list[0] if csl_list else None,
                eve_path=eve_list[0] if eve_list else None,
                brp_path=bucket.get("BRP"),
                pld_path=bucket.get("PLD"),
                sa2_path=bucket.get("SA2"),
                csl_paths=csl_list,
                eve_paths=eve_list,
            )
        )
    return out


# How close (in seconds) two EDF files must be in time to be considered
# the same session. Empirical bounds observed in the field:
#   - Within-session boot-to-waveform offset: typically 5-7 seconds
#     (CSL/EVE at boot, BRP/PLD/SA2 a few seconds later)
#   - Real session restart (failed boot, immediate retry): ~50 seconds
#     observed on the 2026-05-09 fixture
#   - Normal session restart (mask off then back on): minutes-to-hours
#
# 30 seconds threads the needle between these bounds. Wider windows
# merged the 50-second restart case into one session and lost the real
# session's duration; tighter windows would have split the boot-to-
# waveform offset into separate sessions.
SESSION_GROUPING_WINDOW_SECONDS = 30
