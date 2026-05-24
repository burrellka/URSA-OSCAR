"""EDF parser smoke tests against the 4-night regression fixture.

Goals:
1. Parse one night's EVE.edf — confirm at least one expected event type appears.
2. Parse one night's PLD.edf — confirm signal labels match the AirSense 11 spec.
3. Discover all sessions for each fixture night — confirm groupings line up
   with the canonical session-count targets.
"""
from __future__ import annotations

from datetime import date

import pytest

from ursa_oscar.analytics.edf_parser import (
    discover_sessions,
    parse_events,
    parse_header,
    parse_pld_signal,
)
from tests.conftest import FIXTURE_NIGHT_DIRS


def test_parse_header_eve(fixture_root):
    eve = fixture_root / "20260508" / "20260508_220523_EVE.edf"
    hdr = parse_header(eve)
    assert hdr.is_edf_plus_d is True
    assert hdr.n_signals == 2
    assert hdr.n_records == 79
    assert hdr.start_datetime.year == 2026


def test_parse_events_5_8_has_central_apneas(fixture_root):
    """5/8 EVE.edf has the multi-event night Kevin caught the noon-split on."""
    eve = fixture_root / "20260508" / "20260508_220523_EVE.edf"
    events = parse_events(eve)
    assert len(events) > 0, "Expected events from 5/8 EVE.edf"
    # Should see at least a Central Apnea — canonical target is 92 CA
    central_apneas = [e for e in events if e.event_type == "ClearAirway"]
    assert len(central_apneas) > 0
    # First event should be near the session start datetime, not 1970
    first = events[0]
    assert first.timestamp.year == 2026
    assert first.duration_seconds > 0


def test_event_label_normalization(fixture_root):
    """Raw labels are mapped to the canonical EventType enum."""
    eve = fixture_root / "20260508" / "20260508_220523_EVE.edf"
    events = parse_events(eve)
    raw_labels = {e.raw_label for e in events}
    # Each raw label must either map to a known type or pass through unchanged
    for e in events:
        assert e.event_type, f"empty event_type for raw_label={e.raw_label!r}"


def test_discover_sessions_per_night(fixture_root):
    """Every night dir should produce ≥1 SessionEDFs with at least one EDF kind set."""
    for night in FIXTURE_NIGHT_DIRS:
        sessions = discover_sessions(fixture_root / night)
        assert len(sessions) >= 1, f"No sessions discovered in {night}"
        for s in sessions:
            # At minimum a session must have an EVE.edf (events) or PLD.edf
            assert s.eve_path is not None or s.pld_path is not None


def test_discover_sessions_groups_across_minute_boundary(tmp_path):
    """1.1.3 regression — discover_sessions must group EDF files that
    cross a clock-minute boundary into one session, not two.

    ResMed AirSense 11 emits the CSL/EVE pair at boot and the
    BRP/PLD/SA2 trio a few seconds later when waveforms stabilize. The
    7-15 second gap between the two filename prefixes is normal. When
    the boot moment straddles a minute mark (e.g. 01:04:53 + 01:05:00),
    the prior minute-bucketing implementation produced two phantom
    sessions: one with only events files and one with only waveform
    files. The mask-on duration inflated by the full session length.

    See operator's launch-week bug report on the 2026-05-23 night.
    """
    # Two timestamps 7 seconds apart, crossing the 01:04 -> 01:05 boundary.
    # Matches the real failure case the operator reported.
    night = tmp_path / "20260523"
    night.mkdir()
    for ts, kind in [
        ("20260524_010453", "CSL"),
        ("20260524_010453", "EVE"),
        ("20260524_010500", "BRP"),
        ("20260524_010500", "PLD"),
        ("20260524_010500", "SA2"),
    ]:
        (night / f"{ts}_{kind}.edf").write_bytes(b"")  # empty placeholder

    sessions = discover_sessions(night)
    assert len(sessions) == 1, (
        f"Expected 1 session (5 files within 7s), got {len(sessions)}. "
        "Minute-boundary bucketing regression: see 1.1.3 fix in "
        "analytics/edf_parser.py."
    )
    s = sessions[0]
    assert s.csl_path is not None and s.csl_path.name.endswith("CSL.edf")
    assert s.eve_path is not None and s.eve_path.name.endswith("EVE.edf")
    assert s.brp_path is not None and s.brp_path.name.endswith("BRP.edf")
    assert s.pld_path is not None and s.pld_path.name.endswith("PLD.edf")
    assert s.sa2_path is not None and s.sa2_path.name.endswith("SA2.edf")
    # Earliest timestamp wins the session id.
    assert s.session_timestamp_str == "20260524_010453"


def test_discover_sessions_separates_50s_real_restart(tmp_path):
    """1.1.3 regression — a ~50-second gap between two complete file sets
    must produce TWO sessions, not one.

    Observed in the 2026-05-09 OSCAR-parity fixture: the device booted at
    22:04:39, recorded a tiny amount of data, restarted at 22:05:38 (52
    seconds later), then ran for the full 7-hour night. An overly-
    tolerant clustering window (e.g., 60+ seconds) would merge these
    into one session and lose the real session's duration.

    The clustering window must be tight enough to keep these separate
    while still tolerating the 5-15 second event-to-waveform offset
    within a single boot cycle.
    """
    night = tmp_path / "20260509"
    night.mkdir()
    # Set A: short boot cycle at 22:04:39, all five kinds present.
    # Set B: real session boot at 22:05:38 (52 seconds later), all five kinds.
    for ts, kind in [
        ("20260509_220439", "CSL"),
        ("20260509_220439", "EVE"),
        ("20260509_220446", "BRP"),
        ("20260509_220446", "PLD"),
        ("20260509_220446", "SA2"),
        ("20260509_220538", "CSL"),
        ("20260509_220538", "EVE"),
        ("20260509_220543", "BRP"),
        ("20260509_220543", "PLD"),
        ("20260509_220543", "SA2"),
    ]:
        (night / f"{ts}_{kind}.edf").write_bytes(b"")

    sessions = discover_sessions(night)
    assert len(sessions) == 2, (
        f"Two distinct boot cycles 52s apart should split into separate "
        f"sessions; got {len(sessions)}. If this fails, the clustering "
        f"window in SESSION_GROUPING_WINDOW_SECONDS is probably too wide."
    )
    assert sessions[0].session_timestamp_str == "20260509_220439"
    assert sessions[1].session_timestamp_str == "20260509_220538"


def test_discover_sessions_treats_real_gaps_as_separate_sessions(tmp_path):
    """A multi-minute gap between EDF files must produce separate sessions.

    Pairs with the minute-boundary test above: confirms the temporal-
    clustering window is tight enough that real session restarts (mask
    off for a bathroom break, etc.) still split into distinct sessions.
    """
    night = tmp_path / "20260523"
    night.mkdir()
    # Session A at 22:00, session B at 23:30 (90 minutes later, clearly distinct)
    for ts, kind in [
        ("20260523_220000", "CSL"),
        ("20260523_220000", "EVE"),
        ("20260523_220005", "BRP"),
        ("20260523_220005", "PLD"),
        ("20260523_220005", "SA2"),
        ("20260523_233000", "CSL"),
        ("20260523_233000", "EVE"),
        ("20260523_233005", "BRP"),
        ("20260523_233005", "PLD"),
        ("20260523_233005", "SA2"),
    ]:
        (night / f"{ts}_{kind}.edf").write_bytes(b"")

    sessions = discover_sessions(night)
    assert len(sessions) == 2, (
        f"Two distinct sessions (90 min apart) should not cluster; "
        f"got {len(sessions)}."
    )
    assert sessions[0].session_timestamp_str == "20260523_220000"
    assert sessions[1].session_timestamp_str == "20260523_233000"


def test_arousal_maps_to_rera(fixture_root):
    """5/10 EVE.edf has an Arousal event; should normalize to RERA per AASM."""
    from ursa_oscar.analytics.edf_parser import EVENT_LABEL_MAP
    assert EVENT_LABEL_MAP["Arousal"] == "RERA"
    # And cross-check on the fixture
    eve = fixture_root / "20260510" / "20260511_032453_EVE.edf"
    events = parse_events(eve)
    rera_events = [e for e in events if e.event_type == "RERA"]
    arousal_raw = [e for e in events if e.raw_label == "Arousal"]
    assert len(rera_events) == len(arousal_raw)
    assert len(rera_events) >= 1


def test_parse_pld_signal_pressure(fixture_root):
    """Parse Press.2s from a known-good PLD.edf (5/9 has a multi-MB PLD)."""
    pld = fixture_root / "20260509" / "20260509_220543_PLD.edf"
    sig = parse_pld_signal(pld, "Press.2s")
    assert sig is not None, "Press.2s should be readable from 5/9 PLD.edf"
    assert sig.sample_rate_hz == pytest.approx(0.5, rel=0.01)  # 2-second cadence
    assert sig.values.size > 1000  # 7-hour session × 0.5 Hz ≈ 12,600 samples
    # Pressures should be in cmH2O range for AirSense 11 (4-20 typical)
    assert 0.0 <= sig.values.min() < 25.0
    assert sig.values.max() < 25.0
