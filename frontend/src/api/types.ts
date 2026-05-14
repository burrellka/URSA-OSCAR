// TypeScript mirrors of the Pydantic domain models in backend/src/ursa_oscar/models/domain.py.
// Keep in sync when the API surface changes.

export type EventType =
  | 'ClearAirway'
  | 'Obstructive'
  | 'Apnea'
  | 'Hypopnea'
  | 'RERA'
  | 'LargeLeak'
  | 'FlowLimit'
  | 'PeriodicBreathing'
  | 'CheyneStokes';

export interface NightlySummary {
  date: string; // YYYY-MM-DD
  session_count: number | null;
  start_time: string | null;
  end_time: string | null;
  total_time_minutes: number | null;

  total_ahi: number | null;
  obstructive_ahi: number | null;
  central_ahi: number | null;
  hypopnea_index: number | null;
  rera_index: number | null;

  median_pressure: number | null;
  p95_pressure: number | null;
  p995_pressure: number | null;
  median_epap: number | null;
  p95_epap: number | null;
  p995_epap: number | null;
  median_leak: number | null;
  p95_leak: number | null;
  p995_leak: number | null;

  minutes_in_apnea: number | null;
  minutes_over_leak_redline: number | null;
  cheyne_stokes_pct: number | null;
  large_leak_pct: number | null;

  machine_model: string | null;
  mode: string | null;
  min_pressure_setting: number | null;
  max_pressure_setting: number | null;
  epr_level: number | null;
  ramp_time_minutes: number | null;
  humidity_level: string | null;
  mask_type: string | null;

  // Schema v2 — Device-Settings expansion (Phase 2 polish)
  antibacterial_filter: string | null;
  climate_control: string | null;
  epr_mode: string | null;
  humidifier_status: string | null;
  patient_view: string | null;
  response_mode: string | null;
  smart_start: string | null;
  temperature_celsius: number | null;
  temperature_enable: string | null;

  last_updated: string | null;
}

export interface NightlyEvent {
  id: number | null;
  date: string;
  timestamp: string;
  session_id: number | null;
  event_type: EventType;
  duration_seconds: number | null;
  pressure_at_event: number | null;
  epap_at_event: number | null;
  flow_at_event: number | null;
  leak_at_event: number | null;
}

export interface SkippedNight {
  date: string; // YYYY-MM-DD
  reason: string;
}

export interface ImportLogEntry {
  id: number | null;
  import_timestamp: string | null;
  source_path: string;
  nights_imported: number;
  earliest_date: string | null;
  latest_date: string | null;
  // Phase 3 Item 1C — tri-state discriminator. `partial` is new in 0.5.0.
  status: 'pending' | 'running' | 'completed' | 'partial' | 'failed';
  error_message: string | null;
  // Phase 2 polish 0.4.2 — per-night resilient import. Optional/back-compat.
  nights_skipped?: number;
  skipped?: SkippedNight[];
  // 0.6.3 — skip-existing dedup. Counts nights that were already in the
  // DB and weren't re-parsed. Distinct from `nights_skipped` (which is
  // for errors / empty-sessions). Optional/back-compat.
  nights_skipped_existing?: number;
}


// Phase 4 Ticket 1 — sessions table + exclusion. The Daily View binds
// the Session Information checkbox column to these.

export interface Session {
  date: string;
  session_id: number;
  start_ts: string;
  end_ts: string;
  mask_on_minutes: number;
  excluded: boolean;
}

export interface ToggleSessionResponse {
  date: string;
  session_id: number;
  excluded: boolean;
  summary: NightlySummary;
}

// =====================================================================
// Phase 3 Item 3 — manual logs + profile + vocab.
// Mirrors the Pydantic discriminated-union in
// backend/src/ursa_oscar/models/manual_logs.py.
// =====================================================================

export type ManualLogType =
  | 'medication'
  | 'symptom'
  | 'alertness'
  | 'sleep_environment'
  | 'freeform';

export interface ManualLogBase {
  id: number | null;
  date: string; // YYYY-MM-DD
  timestamp: string; // ISO
  notes: string | null;
  last_updated: string | null;
}

export interface MedicationLog extends ManualLogBase {
  log_type: 'medication';
  name: string;
  dose: number | null;
  dose_unit: string | null;
}

export interface SymptomLog extends ManualLogBase {
  log_type: 'symptom';
  name: string;
  severity: number | null;
}

export interface AlertnessLog extends ManualLogBase {
  log_type: 'alertness';
  score: number;
}

export interface SleepEnvironmentLog extends ManualLogBase {
  log_type: 'sleep_environment';
  temperature_c: number | null;
  noise_level: 'quiet' | 'moderate' | 'loud' | null;
  light_level: 'dark' | 'dim' | 'bright' | null;
  bed_partner_present: boolean | null;
}

export interface FreeformLog extends ManualLogBase {
  log_type: 'freeform';
  title: string | null;
  body: string;
}

export type ManualLogEntry =
  | MedicationLog
  | SymptomLog
  | AlertnessLog
  | SleepEnvironmentLog
  | FreeformLog;

// Mirror of backend/src/ursa_oscar/models/profile.py
export interface DisplayPreferences {
  display_name: string | null;
  timezone: string;
  date_format: 'MM/DD/YYYY' | 'DD/MM/YYYY' | 'YYYY-MM-DD';
  pressure_unit: 'cmH2O' | 'hPa';
  temperature_unit: 'C' | 'F';
  theme: 'light' | 'dark' | 'auto';
}

export interface Diagnosis {
  name: string;
  icd10_code: string | null;
  severity: string | null;
  diagnosed_date: string | null;
  notes: string | null;
}

export interface Provider {
  name: string;
  role: 'pcp' | 'sleep_md' | 'sleep_pa' | 'ent' | 'dental_sleep' | 'cbti' | 'cardiology' | 'sleep_lab' | 'other';
  organization: string | null;
  notes: string | null;
}

export interface TreatmentGoal {
  description: string;
  target_metric: string | null;
  target_value: number | null;
  active: boolean;
  notes: string | null;
}

export interface ActiveMedication {
  name: string;
  dose: number | null;
  dose_unit: string | null;
  schedule: string | null;
  route: 'oral' | 'sublingual' | 'topical' | 'injection' | 'other';
  started_date: string | null;
  notes: string | null;
}

export interface EquipmentItem {
  item_type: 'cpap' | 'mask' | 'mad' | 'wearable' | 'other';
  model: string;
  started_date: string | null;
  active: boolean;
  notes: string | null;
}

export interface ClinicalContext {
  diagnoses: Diagnosis[];
  providers: Provider[];
  treatment_goals: TreatmentGoal[];
  active_medications: ActiveMedication[];
  equipment: EquipmentItem[];
}

export type QuickLogButton =
  | 'medication'
  | 'symptom'
  | 'alertness'
  | 'sleep_environment'
  | 'freeform';

export interface UIPersonalization {
  quick_log_buttons: QuickLogButton[];
  symptom_watchlist: string[];
  active_concerns: string[];
  notes: string | null;
}

export interface UserProfile {
  version: number;
  last_updated: string;
  display: DisplayPreferences;
  clinical: ClinicalContext;
  personalization: UIPersonalization;
}

export interface VocabAddResult {
  field: string;
  values: string[];
  profile_active_medications_updated: boolean;
}
