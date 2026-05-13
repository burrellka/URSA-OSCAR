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
  status: 'pending' | 'running' | 'completed' | 'failed';
  error_message: string | null;
  // Phase 2 polish 0.4.2 — per-night resilient import. Optional/back-compat.
  nights_skipped?: number;
  skipped?: SkippedNight[];
}
