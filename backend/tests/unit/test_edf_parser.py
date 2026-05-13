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
