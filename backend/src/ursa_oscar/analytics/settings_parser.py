"""Equipment-settings parser — Identification.json + SETTINGS/CurrentSettings.json.

ResMed's AirSense 11 firmware writes two JSON files at the SD-card root that
together describe the machine, the currently-active therapy profile, and
feature toggles (EPR, ramp, humidifier, mask, etc.):

    <root>/Identification.json        — machine model, serial, firmware
    <root>/SETTINGS/CurrentSettings.json — active therapy + feature profiles

We extract just the seven fields the `nightly_summary` schema reserves for
equipment settings (per Design v1.1 § Data Model):

    machine_model        ← Identification.json -> ProductName
    mode                 ← CurrentSettings.json -> active TherapyMode
    min_pressure_setting ← CurrentSettings.json -> active profile MinPressure
    max_pressure_setting ← CurrentSettings.json -> active profile MaxPressure
                           (or SetPressure for fixed-CPAP mode)
    epr_level            ← CurrentSettings.json -> EprFeature.EprPressure
                           (only when EprEnable == 'On')
    ramp_time_minutes    ← CurrentSettings.json -> AutoRampFeature.RampTime
                           (only when RampEnable != 'Off')
    humidity_level       ← CurrentSettings.json -> ClimateFeature.HumidifierLevel
                           (or "Auto" / "Off" string when applicable)
    mask_type            ← CurrentSettings.json -> CircuitFeature.MaskType

**Per-night accuracy caveat (Phase 1.5 acceptable, Phase 4 to refine):**
CurrentSettings.json reflects the *most recent* settings at the time of SD-card
export. STR.edf contains the per-session settings history; if Kevin changed a
prescription mid-week, nights before the change carry wrong values until STR.edf
parsing lands. Phase 1.5 accepts this trade-off — all four fixture nights in
the canonical regression set predate any settings change in the test data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EquipmentSettings:
    """Equipment settings parsed from Identification.json + CurrentSettings.json.

    Originally eight fields (v1 / Phase-1.5). Schema v2 (Phase 2 polish)
    added nine more for OSCAR-parity Device Settings display:
        antibacterial_filter, climate_control, epr_mode, humidifier_status,
        patient_view, response_mode, smart_start, temperature_celsius,
        temperature_enable

    Any field may be None — the parser returns None for missing JSON paths
    rather than raising. Use `.is_empty` to test whether anything was parsed.
    """
    # v1 fields (Phase 1.5)
    machine_model: str | None = None
    mode: str | None = None
    min_pressure_setting: float | None = None
    max_pressure_setting: float | None = None
    epr_level: int | None = None
    ramp_time_minutes: int | None = None
    humidity_level: str | None = None
    mask_type: str | None = None
    # v2 fields (Phase 2 polish)
    antibacterial_filter: str | None = None
    climate_control: str | None = None
    epr_mode: str | None = None
    humidifier_status: str | None = None
    patient_view: str | None = None
    response_mode: str | None = None
    smart_start: str | None = None
    temperature_celsius: float | None = None
    temperature_enable: str | None = None

    @property
    def is_empty(self) -> bool:
        return all(
            getattr(self, f) is None
            for f in (
                "machine_model", "mode", "min_pressure_setting",
                "max_pressure_setting", "epr_level", "ramp_time_minutes",
                "humidity_level", "mask_type",
                "antibacterial_filter", "climate_control", "epr_mode",
                "humidifier_status", "patient_view", "response_mode",
                "smart_start", "temperature_celsius", "temperature_enable",
            )
        )


def parse_equipment_settings(source_root: Path) -> EquipmentSettings:
    """Parse Identification.json + SETTINGS/CurrentSettings.json under root.

    Both files are optional — missing files yield None for the affected fields
    rather than raising. Malformed JSON is logged and treated as missing.

    Args:
        source_root: The same path you'd pass to `list_night_dirs` — the
            SD-card root or DATALOG-flat directory.

    Returns:
        EquipmentSettings with every field populated where the source data
        was available + readable. `.is_empty` is True only if both files
        are missing/unreadable.
    """
    source_root = Path(source_root)

    machine_model = _read_machine_model(source_root)
    therapy = _read_therapy_settings(source_root)

    return EquipmentSettings(
        # v1
        machine_model=machine_model,
        mode=therapy.get("mode"),
        min_pressure_setting=therapy.get("min_pressure_setting"),
        max_pressure_setting=therapy.get("max_pressure_setting"),
        epr_level=therapy.get("epr_level"),
        ramp_time_minutes=therapy.get("ramp_time_minutes"),
        humidity_level=therapy.get("humidity_level"),
        mask_type=therapy.get("mask_type"),
        # v2
        antibacterial_filter=therapy.get("antibacterial_filter"),
        climate_control=therapy.get("climate_control"),
        epr_mode=therapy.get("epr_mode"),
        humidifier_status=therapy.get("humidifier_status"),
        patient_view=therapy.get("patient_view"),
        response_mode=therapy.get("response_mode"),
        smart_start=therapy.get("smart_start"),
        temperature_celsius=therapy.get("temperature_celsius"),
        temperature_enable=therapy.get("temperature_enable"),
    )


def _read_machine_model(source_root: Path) -> str | None:
    ident_path = source_root / "Identification.json"
    if not ident_path.is_file():
        return None
    try:
        with open(ident_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return _safe_dig(
        data,
        ["FlowGenerator", "IdentificationProfiles", "Product", "ProductName"],
    )


def _read_therapy_settings(source_root: Path) -> dict[str, Any]:
    settings_path = source_root / "SETTINGS" / "CurrentSettings.json"
    if not settings_path.is_file():
        return {}
    try:
        with open(settings_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    sp = _safe_dig(data, ["FlowGenerator", "SettingProfiles"]) or {}
    active = (sp.get("ActiveProfiles") or {}).get("TherapyProfile")
    therapy_profile = (sp.get("TherapyProfiles") or {}).get(active or "") or {}
    features = sp.get("FeatureProfiles") or {}

    out: dict[str, Any] = {}

    # --- Mode + pressure prescription ----------------------------------
    mode = therapy_profile.get("TherapyMode")
    out["mode"] = mode if isinstance(mode, str) else None

    min_p = therapy_profile.get("MinPressure")
    max_p = therapy_profile.get("MaxPressure")
    set_p = therapy_profile.get("SetPressure")
    # AutoSet / HerAuto use Min/Max; CPAP uses SetPressure (single value)
    if isinstance(min_p, (int, float)):
        out["min_pressure_setting"] = float(min_p)
    elif isinstance(set_p, (int, float)):
        out["min_pressure_setting"] = float(set_p)
    if isinstance(max_p, (int, float)):
        out["max_pressure_setting"] = float(max_p)
    elif isinstance(set_p, (int, float)):
        out["max_pressure_setting"] = float(set_p)

    # --- EPR --------------------------------------------------------------
    epr = features.get("EprFeature") or {}
    if epr.get("EprEnable") == "On":
        epr_level = epr.get("EprPressure")
        if isinstance(epr_level, int):
            out["epr_level"] = epr_level
    else:
        # EPR explicitly off → record as 0 so downstream chart/UI knows it
        # was intentionally disabled (vs. unknown/None).
        out["epr_level"] = 0

    # --- Ramp time --------------------------------------------------------
    ramp = features.get("AutoRampFeature") or {}
    ramp_enable = ramp.get("RampEnable")
    ramp_time = ramp.get("RampTime")
    if ramp_enable in ("On", "Auto") and isinstance(ramp_time, int):
        out["ramp_time_minutes"] = ramp_time
    elif ramp_enable == "Off":
        out["ramp_time_minutes"] = 0

    # --- Humidifier ------------------------------------------------------
    climate = features.get("ClimateFeature") or {}
    humid_enable = climate.get("HumidifierSettingEnable")
    climate_ctrl = climate.get("ClimateControl")
    humid_level = climate.get("HumidifierLevel")
    # Schema v2 (Phase 2 polish): humidity_level always holds the numeric
    # HumidifierLevel (1-8) when the humidifier is on, regardless of
    # climate-control state. v1 had collapsed "ClimateControl=Auto" into
    # humidity_level="Auto" which was lossy — the underlying level value
    # exists even in Auto mode. Climate-control state is now its own column
    # (`climate_control`) and humidifier on/off is its own column
    # (`humidifier_status`), so this column gets to be just the level.
    if humid_enable == "Off":
        out["humidity_level"] = "Off"
    elif isinstance(humid_level, int):
        out["humidity_level"] = str(humid_level)
    elif climate_ctrl == "Auto":
        # Defensive fallback for cases where HumidifierLevel wasn't set
        # but ClimateControl claims Auto.
        out["humidity_level"] = "Auto"

    # --- Mask -------------------------------------------------------------
    circuit = features.get("CircuitFeature") or {}
    mask = circuit.get("MaskType")
    if isinstance(mask, str):
        out["mask_type"] = mask

    # ------------------------------------------------------------------
    # v2 fields (Phase 2 polish — OSCAR-parity Device Settings panel).
    # All optional: missing JSON paths -> None.
    # ------------------------------------------------------------------

    # Antibacterial filter — CircuitFeature.AntiBacterialFilter ("Yes"/"No")
    ab_filter = circuit.get("AntiBacterialFilter")
    if isinstance(ab_filter, str):
        out["antibacterial_filter"] = ab_filter

    # Climate Control — separate from humidity_level. The v1 parser had
    # been collapsing "ClimateControl=Auto" into humidity_level="Auto"; now
    # surfaced as its own field so OSCAR-parity rows render correctly.
    if isinstance(climate_ctrl, str):
        out["climate_control"] = climate_ctrl

    # Humidifier Status — "On"/"Off". Separate from humidity_level (which
    # holds the numeric level when on).
    if isinstance(humid_enable, str):
        out["humidifier_status"] = humid_enable

    # EPR Mode — "FullTime" / "RampOnly" / "Off". OSCAR labels these
    # "Full Time" / "Ramp Only" / "Off" (with spaces); we mirror that.
    epr_type = epr.get("EprType")
    if isinstance(epr_type, str):
        _EPR_TYPE_DISPLAY = {"FullTime": "Full Time", "RampOnly": "Ramp Only", "Off": "Off"}
        out["epr_mode"] = _EPR_TYPE_DISPLAY.get(epr_type, epr_type)
    elif epr.get("EprEnable") == "Off":
        # If EprType is absent but EprEnable says Off, surface that.
        out["epr_mode"] = "Off"

    # Patient View — PatientViewFeature.PatientView ("Full"/"Limited"/"Off")
    patient_view_feat = features.get("PatientViewFeature") or {}
    pv = patient_view_feat.get("PatientView")
    if isinstance(pv, str):
        out["patient_view"] = pv

    # Response — derived from ComfortFeature.AutoSetComfort. OSCAR labels
    # "Soft" when AutoSet Comfort is On (gentler ramp-up curve) and
    # "Standard" when Off. Best-effort mapping; if AutoSetComfort isn't
    # present at all, leaves the column NULL.
    comfort = features.get("ComfortFeature") or {}
    auto_comfort = comfort.get("AutoSetComfort")
    if auto_comfort == "On":
        out["response_mode"] = "Soft"
    elif auto_comfort == "Off":
        out["response_mode"] = "Standard"

    # Smart Start — SmartStartStopFeature.SmartStart ("On"/"Off")
    sss = features.get("SmartStartStopFeature") or {}
    smart_start = sss.get("SmartStart")
    if isinstance(smart_start, str):
        out["smart_start"] = smart_start

    # Heated tube temperature + enable. ResMed stores temperature in
    # Celsius regardless of the user's display preference; conversion
    # happens at the display layer.
    temp = climate.get("HeatedTubeTemperature")
    if isinstance(temp, (int, float)):
        out["temperature_celsius"] = float(temp)
    temp_enable = climate.get("HeatedTubeSettingEnable")
    if isinstance(temp_enable, str):
        out["temperature_enable"] = temp_enable

    return out


def _safe_dig(d: Any, path: list[str]) -> Any:
    """Walk nested dicts, returning None on any missing key or non-dict."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
        if d is None:
            return None
    return d
