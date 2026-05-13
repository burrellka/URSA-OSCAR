"""Equipment-settings parser unit tests against the fixture root."""
from __future__ import annotations

from pathlib import Path

import pytest

from ursa_oscar.analytics.settings_parser import (
    EquipmentSettings,
    parse_equipment_settings,
)
from tests.conftest import FIXTURE_ROOT


def test_parses_machine_model(fixture_root: Path):
    s = parse_equipment_settings(fixture_root)
    assert s.machine_model == "AirSense11AutoSet"


def test_parses_therapy_profile(fixture_root: Path):
    s = parse_equipment_settings(fixture_root)
    # CurrentSettings.json declares ActiveProfiles.TherapyProfile == AutoSetProfile
    # which has TherapyMode=AutoSet, MinPressure=6.4, MaxPressure=12.0.
    assert s.mode == "AutoSet"
    assert s.min_pressure_setting == 6.4
    assert s.max_pressure_setting == 12.0


def test_parses_feature_settings(fixture_root: Path):
    s = parse_equipment_settings(fixture_root)
    # EprFeature.EprEnable="On", EprPressure=3
    assert s.epr_level == 3
    # AutoRampFeature.RampEnable="Off" -> normalized to 0
    assert s.ramp_time_minutes == 0
    # Schema v2 (Phase 2 polish, 0.4.1): humidity_level now always holds
    # the numeric HumidifierLevel (e.g. "4") when the humidifier is on,
    # regardless of climate-control state. ClimateControl="Auto" is
    # surfaced separately as s.climate_control.
    assert s.humidity_level == "4"
    assert s.climate_control == "Auto"
    assert s.humidifier_status == "On"
    # CircuitFeature.MaskType="Pillows"
    assert s.mask_type == "Pillows"


def test_missing_files_yield_none(tmp_path: Path):
    # Empty dir -> no files -> all None, is_empty True
    s = parse_equipment_settings(tmp_path)
    assert s.is_empty is True
    assert s.machine_model is None
    assert s.mode is None


def test_partial_files_handled(tmp_path: Path):
    # Identification present, SETTINGS missing
    (tmp_path / "Identification.json").write_text(
        '{"FlowGenerator":{"IdentificationProfiles":{"Product":{"ProductName":"AirSense11"}}}}',
        encoding="utf-8",
    )
    s = parse_equipment_settings(tmp_path)
    assert s.machine_model == "AirSense11"
    assert s.mode is None
    assert s.is_empty is False


def test_malformed_json_treated_as_missing(tmp_path: Path):
    (tmp_path / "Identification.json").write_text("not json", encoding="utf-8")
    s = parse_equipment_settings(tmp_path)
    assert s.machine_model is None
    assert s.is_empty is True
