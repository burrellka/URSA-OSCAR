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


// Phase 4 Ticket 2 — async import queue. POST /imports and
// POST /imports/upload now return an ImportJob instead of blocking
// to completion; the operator (or the Import page) polls
// GET /imports/jobs/{id} to await the worker.

export type ImportJobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'orphaned';

export interface ImportJob {
  id: number;
  status: ImportJobStatus;
  source_path: string | null;
  upload_dir: string | null;
  force_reimport: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  /** Serialized ImportLogEntry when status='completed'. */
  result_json: ImportLogEntry | null;
  error_message: string | null;
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

// Mirror of backend/src/ursa_oscar/models/profile.py.
// Phase 4 Ticket 4 — DeviceClock describes how URSA shifts displayed
// timestamps to compensate for a CPAP clock that doesn't auto-DST.
export interface DeviceClock {
  country: string | null;
  mode: 'none' | 'auto' | 'static';
  device_utc_offset_minutes: number | null;
  manual_offset_minutes: number;
}

export interface DisplayPreferences {
  display_name: string | null;
  timezone: string;
  date_format: 'MM/DD/YYYY' | 'DD/MM/YYYY' | 'YYYY-MM-DD';
  pressure_unit: 'cmH2O' | 'hPa';
  temperature_unit: 'C' | 'F';
  theme: 'light' | 'dark' | 'auto';
  device_clock: DeviceClock;
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


// =====================================================================
// Phase 5 — AI proxy types. Mirror the backend's ai_proxy module.
// =====================================================================

export interface AiProviderPreset {
  id: string;
  label: string;
  adapter: 'claude' | 'openai_compat';
  default_endpoint: string;
  default_models: string[];
  auth_header_name: string;
  auth_header_format: string;
  notes: string;
  supports_local_routing?: boolean;
}

export interface AiToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface AiMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls?: AiToolCall[];
  tool_call_id?: string;
}

export interface AiMaskedConfig {
  enabled: boolean;
  provider_id: string | null;
  model: string;
  endpoint_url: string;
  routing_mode: string;
  proxy_endpoint_url: string | null;
  custom_system_prompt: string | null;
  // 1.1.11 — operator's stored request timeout in seconds. null = "use
  // provider-family default"; effective_timeout_seconds reveals what
  // that default actually resolves to (300s for local, 120s cloud).
  timeout_seconds: number | null;
  effective_timeout_seconds: number;
  // 1.1.14 — operator's stored output-token cap. null = "use provider-
  // family default"; effective_max_output_tokens reveals what that
  // resolves to (4000 for local; null for cloud = the provider's own
  // large default, so long cloud answers aren't truncated).
  max_output_tokens: number | null;
  effective_max_output_tokens: number | null;
  api_key_set: boolean;
  api_keys_set: Record<string, boolean>;
}

export interface AiConfigPatch {
  enabled?: boolean;
  provider_id?: string;
  model?: string;
  endpoint_url?: string;
  routing_mode?: string;
  proxy_endpoint_url?: string | null;
  custom_system_prompt?: string | null;
  timeout_seconds?: number | null;
  max_output_tokens?: number | null;
  api_key?: string;  // never returned, only sent
}

export type AiStreamEventType =
  | 'text'
  | 'reasoning'              // 1.1.3 — chain-of-thought from thinking-mode models
  | 'tool_call_start'
  | 'tool_call_input'
  | 'tool_call_complete'
  | 'tool_result'
  | 'complete'
  | 'error';

export interface AiStreamEvent {
  event_type: AiStreamEventType;
  payload: Record<string, unknown>;
}

/** 1.1.14 — per-turn observability meta, attached to the terminal
 *  `complete` event's payload (and to a MODEL_TRUNCATED error). Powers
 *  the per-turn line + expandable context breakdown under a message.
 *  All token buckets are chars/4 estimates; `tokens.estimated` is true
 *  when the provider returned no usage and we fell back to the estimate. */
export interface AiTurnMeta {
  model: string | null;
  provider_id: string | null;
  rounds: number;
  elapsed_ms: number;
  finish_reason: string | null;
  tools_used: string[];
  tokens: {
    prompt: number | null;
    completion: number | null;
    total: number | null;
    estimated: boolean;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
  breakdown: {
    system: number;
    tools: number;
    tool_results: number;
    history: number;
    total: number;
  };
}

export interface AiTestResult {
  ok: boolean;
  error?: string;
  model_info?: Record<string, unknown>;
}
