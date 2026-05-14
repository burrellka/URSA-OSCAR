"""User Profile schema — Phase 3 Work Order Item 0G.

Profile is the per-instance authoritative source of:
1. Identity & display preferences (timezone, units, format)
2. Clinical context (diagnoses, providers, treatment goals, active
   medications, equipment timeline)
3. UI personalization (quick-log buttons, symptom watchlist, active
   concerns)

Stored as JSON at ``/data/profile.json`` on the mounted volume. Never in
git — only the ``profile.json.community-default`` empty-stub ships in
the public repo. Exposed to the URSA agent at session start via the
Tier-1 MCP tool ``get_user_profile`` (Item 5E).

Bidirectional sync with ``vocab.json``:
- ``clinical.active_medications`` is the authoritative source for the
  Manual Logs medication autocomplete. See
  ``services/profile_vocab_sync.py`` (Phase 3 Item 3D).
- Discontinued medications stay in vocab so historical logs render but
  drop out of Profile's active list.

Security discipline (Work Order § Security Note): Profile contains
diagnoses and provider names. Treat with the same masking rigor as
tokens/secrets — never log full contents (log only ``last_updated`` +
which section was modified), never transit unauthenticated endpoints,
mask any future contact-info fields in MCP responses by default.

Schema version starts at 1. Backwards-compatible field additions don't
bump the version; breaking changes do, with a migration shim.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Display & preferences (Tab 1 in the Profile UI)
# ----------------------------------------------------------------------

class DeviceClock(BaseModel):
    """Phase 4 Ticket 4 — operator's CPAP device clock configuration.

    ResMed AirSense 11 (and most CPAP firmware) doesn't auto-adjust for
    DST. The operator sets the device clock once and it stays on that
    offset year-round. URSA reads the EDF wall-clock timestamps as-is
    (the data is canonical) and applies this configuration at display
    time to render the operator's ACTUAL local wall-clock value.

    Three modes:
      "none"   — display exactly what the device recorded; no shift.
                 The right choice when the device clock already follows
                 the operator's local time including DST adjustments.
      "auto"   — device is on a fixed offset; URSA dynamically computes
                 the shift per-timestamp by comparing the browser's
                 local offset for that date against ``device_utc_offset_minutes``.
                 Handles spring-forward and fall-back automatically.
      "static" — apply a fixed shift in minutes from ``manual_offset_minutes``
                 to every displayed time. Escape hatch for setups
                 where ``auto`` produces wrong values.
    """

    country: str | None = Field(
        default=None,
        description="Operator's country (informational). Used by the URSA "
                    "agent for session context; not used by display math.",
    )
    mode: Literal["none", "auto", "static"] = Field(
        default="none",
        description="Strategy for converting recorded device-clock time "
                    "into displayed wall-clock. 'none' (default) preserves "
                    "the recorded value. 'auto' uses device_utc_offset_minutes "
                    "+ browser TZ to compute the shift per-date (DST-aware). "
                    "'static' applies a fixed manual_offset_minutes shift.",
    )
    device_utc_offset_minutes: int | None = Field(
        default=None,
        description="The device's static offset from UTC, in minutes. "
                    "Example: -300 = the device is set to US EST year-round "
                    "(UTC-5). Used only when mode='auto'. None when mode "
                    "is 'none' or 'static'.",
    )
    manual_offset_minutes: int = Field(
        default=0,
        description="Fixed shift in minutes (positive or negative). Used "
                    "only when mode='static'. Example: +60 bumps every "
                    "displayed timestamp forward one hour.",
    )


class DisplayPreferences(BaseModel):
    """Identity + UI rendering preferences."""

    display_name: str | None = Field(
        default=None,
        description='Friendly name shown in the UI header (e.g., "Kevin"). Optional.',
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone string (e.g., 'America/New_York'). "
                    "Used by Daily View date math, the URSA agent's session-start context, "
                    "and timestamp formatting.",
    )
    date_format: Literal["MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"] = Field(
        default="YYYY-MM-DD",
        description="Display format for dates throughout the UI.",
    )
    pressure_unit: Literal["cmH2O", "hPa"] = Field(
        default="cmH2O",
        description="Pressure unit on chart axes + readouts. ResMed reports cmH2O natively.",
    )
    temperature_unit: Literal["C", "F"] = Field(
        default="F",
        description="Heated-tube temperature unit. Conversion happens at the display layer; "
                    "the device always stores Celsius.",
    )
    theme: Literal["light", "dark", "auto"] = Field(
        default="auto",
        description="UI theme. Phase 2 ships light only; this field is a forward-looking "
                    "placeholder for Phase 4+ theming work.",
    )
    device_clock: DeviceClock = Field(
        default_factory=DeviceClock,
        description="Phase 4 Ticket 4 — display-time shift to compensate "
                    "for a CPAP device clock that doesn't auto-adjust for DST.",
    )


# ----------------------------------------------------------------------
# Clinical context (Tab 2 in the Profile UI)
# ----------------------------------------------------------------------

class Diagnosis(BaseModel):
    """One clinical diagnosis the user carries."""

    name: str = Field(description='Free-text name, e.g., "Obstructive Sleep Apnea".')
    icd10_code: str | None = Field(
        default=None,
        description='Optional ICD-10 code (e.g., "G47.33"). Surfaced to the URSA agent so '
                    'it can reason about codified diagnoses if asked.',
    )
    severity: str | None = Field(
        default=None,
        description='Optional severity label (e.g., "mild", "moderate", "severe"). Free text — '
                    'no enforced vocabulary in v1.',
    )
    diagnosed_date: date_t | None = None
    notes: str | None = None


class Provider(BaseModel):
    """One clinical provider the user sees."""

    name: str = Field(description="Provider's name, e.g., 'Dr. Smith'.")
    role: Literal[
        "pcp",            # Primary care physician
        "sleep_md",       # Sleep medicine physician
        "sleep_pa",       # Sleep medicine PA / NP
        "ent",            # Otolaryngologist
        "dental_sleep",   # Dental sleep specialist (MAD prescriptions)
        "cbti",           # CBT-I therapist
        "cardiology",
        "sleep_lab",      # PSG / HSAT lab
        "other",
    ]
    organization: str | None = Field(
        default=None,
        description="Practice / hospital / lab name. Optional.",
    )
    notes: str | None = None
    # Forward-looking discipline (Work Order § Security Note): if Phase 4+
    # ever adds `phone`, `email`, or other PII contact fields here, those
    # fields must be masked in MCP responses by default. Today the schema
    # carries none of that.


class TreatmentGoal(BaseModel):
    """An objective the user is working toward (e.g., AHI < 5)."""

    description: str = Field(
        description='Free-text goal description, e.g., "Maintain AHI under 5".',
    )
    target_metric: str | None = Field(
        default=None,
        description='Optional metric identifier (e.g., "ahi", "total_ahi", "central_ahi"). '
                    'When set together with target_value, lets the URSA agent compute '
                    '"on track / off track" automatically.',
    )
    target_value: float | None = None
    active: bool = Field(
        default=True,
        description="False = retired goal (kept for history but not surfaced as a current target).",
    )
    notes: str | None = Field(
        default=None,
        description="Free-text context — why the goal exists, what's been tried, etc. "
                    "Visible to the URSA agent for clinical-reasoning context.",
    )


class ActiveMedication(BaseModel):
    """A medication the user is currently taking.

    Authoritative for the Manual Logs medication autocomplete. The
    ``services/profile_vocab_sync`` service keeps vocab.json in sync
    bidirectionally — adding a med here adds it to autocomplete;
    autocomplete-add via Manual Logs adds it here with minimal data
    (name only, other fields null until the user fills them in).
    """

    name: str = Field(description='Canonical medication name, e.g., "Doxepin".')
    dose: float | None = Field(default=None, description="Current standing dose value.")
    dose_unit: str | None = Field(default=None, description='e.g., "mg", "mL", "puffs".')
    schedule: str | None = Field(
        default=None,
        description='Free-text dosing schedule, e.g., "0.3 mL at bedtime", "20 mg morning".',
    )
    route: Literal[
        "oral", "sublingual", "topical", "injection", "other",
    ] = "oral"
    started_date: date_t | None = None
    notes: str | None = None


class EquipmentItem(BaseModel):
    """A piece of therapy hardware the user uses or has used."""

    item_type: Literal[
        "cpap",       # The pressure-delivery machine itself
        "mask",
        "mad",        # Mandibular advancement device
        "wearable",   # Fitbit, Apple Watch, Oura, etc.
        "other",
    ]
    model: str = Field(description='Make + model, e.g., "ResMed AirSense 11 AutoSet".')
    started_date: date_t | None = None
    active: bool = Field(
        default=True,
        description="False = no longer in use (e.g., old mask). Kept for history.",
    )
    notes: str | None = None


class ClinicalContext(BaseModel):
    """All clinically-meaningful profile data, grouped together so the
    URSA agent can pull just this section via ``get_user_profile(section='clinical')``.
    """

    diagnoses: list[Diagnosis] = Field(default_factory=list)
    providers: list[Provider] = Field(default_factory=list)
    treatment_goals: list[TreatmentGoal] = Field(default_factory=list)
    active_medications: list[ActiveMedication] = Field(default_factory=list)
    equipment: list[EquipmentItem] = Field(default_factory=list)


# ----------------------------------------------------------------------
# UI personalization (Tab 3 in the Profile UI)
# ----------------------------------------------------------------------

QuickLogButton = Literal[
    "medication",
    "symptom",
    "alertness",
    "sleep_environment",
    "freeform",
]


class UIPersonalization(BaseModel):
    """UI behavior preferences. Tweakable from the Profile page."""

    quick_log_buttons: list[QuickLogButton] = Field(
        default_factory=lambda: [
            "medication", "symptom", "alertness", "sleep_environment", "freeform",
        ],
        description="Which quick-log buttons appear on the Manual Logs page, in display order. "
                    "Users disable buttons they don't use by removing them from this list.",
    )
    symptom_watchlist: list[str] = Field(
        default_factory=list,
        description="Symptom names the user wants surfaced at the top of the symptom-picker "
                    "dropdown (e.g., recurring symptoms they want quick access to log).",
    )
    active_concerns: list[str] = Field(
        default_factory=list,
        description="Narrative concerns the URSA agent should be aware of (e.g., "
                    "'Investigating whether evening alcohol affects AHI'). Surfaced "
                    "to the agent at session start and on the Manual Logs page header.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-form notes about the user's current state, goals, or experiments. "
                    "Visible to the URSA agent.",
    )


# ----------------------------------------------------------------------
# Top-level profile envelope
# ----------------------------------------------------------------------

class UserProfile(BaseModel):
    """The user's complete profile.

    Stored as a single JSON document at ``/data/profile.json``. Updated
    via:
    - ``PUT /api/v1/profile`` for full replace (validates against this
      schema, bumps ``last_updated``).
    - ``PATCH /api/v1/profile`` for partial update (e.g., just
      ``display.timezone``).

    The URSA agent reads this at session start via the Tier-1 MCP tool
    ``get_user_profile``.
    """

    version: int = Field(
        default=1,
        description="Profile schema version. Bumped on breaking changes. "
                    "v1 is the Phase-3 baseline.",
    )
    last_updated: datetime = Field(
        description="When this profile was last written. Updated by every PUT/PATCH. "
                    "The URSA agent uses this to decide whether to re-fetch.",
    )
    display: DisplayPreferences = Field(default_factory=DisplayPreferences)
    clinical: ClinicalContext = Field(default_factory=ClinicalContext)
    personalization: UIPersonalization = Field(default_factory=UIPersonalization)


__all__ = [
    "DeviceClock",
    "DisplayPreferences",
    "Diagnosis",
    "Provider",
    "TreatmentGoal",
    "ActiveMedication",
    "EquipmentItem",
    "ClinicalContext",
    "QuickLogButton",
    "UIPersonalization",
    "UserProfile",
]
