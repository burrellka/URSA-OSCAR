// Typed API client for URSA-OSCAR's backend.
// Raw `fetch()` per ADR-001 (no TanStack Query) — small surface, single user.

import type {
  ImportLogEntry,
  ManualLogEntry,
  ManualLogType,
  NightlyEvent,
  NightlySummary,
  Session,
  ToggleSessionResponse,
  UserProfile,
  VocabAddResult,
} from './types';

const BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  body?: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  params?: Record<string, string | number | string[] | undefined>,
): Promise<T> {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) {
        for (const item of v) qs.append(k, String(item));
      } else {
        qs.set(k, String(v));
      }
    }
    const search = qs.toString();
    if (search) url += `?${search}`;
  }
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !('Content-Type' in headers)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = await res.text().catch(() => undefined); }
    throw new ApiError(res.status, `${init.method ?? 'GET'} ${path} -> ${res.status}`, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  healthz: () => request<{ ok: boolean; service: string }>('/healthz'),

  listNights: (params?: { start?: string; end?: string }) =>
    request<NightlySummary[]>(`${BASE}/nights`, {}, params),

  getNight: (date: string) =>
    request<NightlySummary>(`${BASE}/night/${date}`),

  // Phase 4 Ticket 1 — session-level data + exclusion toggle.
  listSessions: (date: string) =>
    request<Session[]>(`${BASE}/nights/${date}/sessions`),

  toggleSession: (date: string, session_id: number) =>
    request<ToggleSessionResponse>(
      `${BASE}/nights/${date}/sessions/${session_id}/toggle`,
      { method: 'POST' },
    ),

  listEvents: (date: string, eventTypes?: string[]) =>
    request<NightlyEvent[]>(`${BASE}/events`, {}, { date, event_type: eventTypes }),

  /**
   * Fetch one or more waveform channels for a single night. Server returns
   * epoch-ms timestamps; this client converts to epoch-seconds for uPlot.
   */
  getTimeseries: async (date: string, series: string[]) => {
    type RawSeries = { timestamps: number[]; values: (number | null)[]; secondary: (number | null)[] | null };
    type RawResp = { date: string; series: Record<string, RawSeries> };
    const resp = await request<RawResp>(`${BASE}/timeseries/${date}`, {}, { series });
    const out: Record<string, { timestamps: number[]; values: (number | null)[]; secondary: (number | null)[] | null }> = {};
    for (const [k, s] of Object.entries(resp.series)) {
      out[k] = {
        timestamps: s.timestamps.map((ms) => ms / 1000),
        values: s.values,
        secondary: s.secondary,
      };
    }
    return { date: resp.date, series: out };
  },

  triggerImport: (source_path: string, force = false) =>
    request<ImportLogEntry>(
      `${BASE}/imports`,
      { method: 'POST', body: JSON.stringify({ source_path }) },
      { force: force ? 'true' : undefined },
    ),

  /** Settings page (Phase 2 polish Item 5). Server-side masking guaranteed. */
  getSystemConfig: () => request<SystemConfig>(`${BASE}/system/config`),
  verifyMcp: () =>
    request<VerifyMcpResult>(`${BASE}/system/verify-mcp`, { method: 'POST' }),

  // =====================================================================
  // Phase 3 Item 3 — manual logs.
  // =====================================================================
  listManualLogs: (params?: {
    start?: string;
    end?: string;
    log_type?: ManualLogType;
    category?: string;
  }) =>
    request<ManualLogEntry[]>(`${BASE}/manual-logs`, {}, params),

  createManualLog: (entry: Omit<ManualLogEntry, 'id' | 'last_updated'>) =>
    request<ManualLogEntry>(`${BASE}/manual-logs`, {
      method: 'POST',
      body: JSON.stringify(entry),
    }),

  patchManualLog: (id: number, patch: Record<string, unknown>) =>
    request<ManualLogEntry>(`${BASE}/manual-logs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  deleteManualLog: (id: number) =>
    request<void>(`${BASE}/manual-logs/${id}`, { method: 'DELETE' }),

  // =====================================================================
  // Phase 3 Item 3 — profile.
  // =====================================================================
  getProfile: () => request<UserProfile>(`${BASE}/profile`),

  putProfile: (profile: UserProfile) =>
    request<UserProfile>(`${BASE}/profile`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  patchProfile: (diff: Record<string, unknown>) =>
    request<UserProfile>(`${BASE}/profile`, {
      method: 'PATCH',
      body: JSON.stringify(diff),
    }),

  // =====================================================================
  // Phase 3 Item 3 — vocab.
  // =====================================================================
  getVocab: () => request<Record<string, unknown>>(`${BASE}/manual-logs/vocab`),

  getVocabField: (field: string) =>
    request<string[]>(`${BASE}/manual-logs/vocab/${field}`),

  addVocabValue: (log_type: ManualLogType, field: string, value: string) =>
    request<VocabAddResult>(`${BASE}/manual-logs/vocab`, {
      method: 'POST',
      body: JSON.stringify({ log_type, field, value }),
    }),

  // =====================================================================
  // Phase 3 Items 5A-D — analytics + 6 — Trends.
  // =====================================================================
  getAvailableMetrics: () =>
    request<{ nightly_metrics: string[]; manual_metrics: string[] }>(
      `${BASE}/analytics/available-metrics`,
    ),

  comparePeriods: (params: {
    period_a_start: string; period_a_end: string;
    period_b_start: string; period_b_end: string;
    metrics?: string[];
  }) => request<ComparePeriodsResult>(`${BASE}/analytics/compare-periods`, {}, params),

  getCorrelation: (params: {
    metric_a: string; metric_b: string;
    start_date: string; end_date: string;
    lag_days?: number;
  }) => request<CorrelationResult>(`${BASE}/analytics/correlation`, {}, params),

  getTrend: (params: {
    metric: string; start_date: string; end_date: string;
    projection_days?: number;
  }) => request<TrendResult>(`${BASE}/analytics/trend`, {}, params),

  getManualLogSummary: (params: {
    date?: string; start_date?: string; end_date?: string; log_type?: string;
  }) => request<ManualLogSummaryResult>(`${BASE}/analytics/manual-log-summary`, {}, params),

  // =====================================================================
  // Phase 3 hard-delete purge.
  // =====================================================================
  previewDelete: (start_date: string, end_date: string) =>
    request<PreviewDeleteResult>(`${BASE}/nights/preview-delete`, {
      method: 'POST',
      body: JSON.stringify({ start_date, end_date }),
    }),

  deleteNight: (date: string, delete_manual_logs = false) =>
    request<DeleteNightResult>(
      `${BASE}/nights/${date}`,
      { method: 'DELETE' },
      { delete_manual_logs: delete_manual_logs ? 'true' : 'false' },
    ),

  deleteNightsRange: (
    start_date: string, end_date: string, delete_manual_logs = false,
  ) => request<DeleteRangeResult>(
    `${BASE}/nights`,
    { method: 'DELETE' },
    { start_date, end_date, delete_manual_logs: delete_manual_logs ? 'true' : 'false' },
  ),

  runCheckpoint: () =>
    request<{ db_size_before_mb: number | null; db_size_after_mb: number | null }>(
      `${BASE}/admin/checkpoint`,
      { method: 'POST' },
    ),

  // =====================================================================
  // Phase 3 Item 2 — folder upload, Item 7 — bulk export.
  // =====================================================================
  uploadFolder: async (
    files: File[],
    onProgress?: (sent: number, total: number) => void,
    force = false,
  ) => {
    const fd = new FormData();
    let total = 0;
    for (const f of files) {
      // webkitdirectory inputs populate `webkitRelativePath` on each File
      // — preserves the user's folder structure so the importer can find
      // DATALOG/YYYYMMDD inside the temp dir.
      const path = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      fd.append('files', f, path);
      total += f.size;
    }
    return new Promise<ImportLogEntry>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const url = force
        ? `${BASE}/imports/upload?force=true`
        : `${BASE}/imports/upload`;
      xhr.open('POST', url);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded, total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { reject(new Error('Bad JSON in upload response')); }
        } else {
          reject(new ApiError(xhr.status, `Upload failed: ${xhr.status}`, xhr.responseText));
        }
      };
      xhr.onerror = () => reject(new Error('Upload network error'));
      xhr.send(fd);
    });
  },

  bulkExportUrl: (start_date: string, end_date: string) =>
    `${BASE}/exports?start_date=${start_date}&end_date=${end_date}&format=csv`,
};


// =====================================================================
// Phase 3 analytics result types (mirror of backend shapes).
// =====================================================================

export interface ComparePeriodsResult {
  period_a: { start: string; end: string; n_nights: number };
  period_b: { start: string; end: string; n_nights: number };
  metrics: Record<string, {
    period_a: { n: number; mean: number | null; median: number | null;
                std: number | null; min: number | null; max: number | null };
    period_b: { n: number; mean: number | null; median: number | null;
                std: number | null; min: number | null; max: number | null };
    absolute_delta: number | null;
    relative_delta_pct: number | null;
    interpretation: string;
  }>;
  summary: string;
}

export interface CorrelationResult {
  metric_a: string;
  metric_b: string;
  date_range: { start: string; end: string };
  lag_days: number;
  n_pairs: number;
  pearson_r: number | null;
  p_value: number | null;
  interpretation: string;
  interpretation_text: string;
  sample_size_warning: string | null;
}

export interface TrendResult {
  metric: string;
  date_range: { start: string; end: string };
  n_nights: number;
  slope_per_day: number | null;
  intercept: number | null;
  r_squared: number | null;
  p_value: number | null;
  current_value_estimate: number | null;
  projection: {
    projection_days: number;
    projection_date: string;
    projected_value: number;
  } | null;
  interpretation: string;
  interpretation_text: string;
}

export interface ManualLogSummaryResult {
  date_range: { start: string; end: string };
  total_entries: number;
  by_type: Record<string, Record<string, unknown>>;
}

export interface PreviewDeleteResult {
  nights: number;
  events: number;
  timeseries_rows: number;
  manual_logs: number;
  dates: string[];
}

export interface DeleteNightResult {
  date: string;
  events_deleted: number;
  timeseries_rows_deleted: number;
  manual_logs_deleted: number;
}

export interface DeleteRangeResult extends DeleteNightResult {
  nights_deleted: number;
  dates: string[];
  db_size_before_mb: number | null;
  db_size_after_mb: number | null;
}

export interface SystemConfig {
  mcp: {
    base_url: string | null;
    bearer_token_masked: string | null;
    oauth_client_id_masked: string | null;
    oauth_client_secret: { set: boolean };
    internal_url: string;
  };
  api: {
    internal_url: string;
    db_path: string;
    db_size_bytes: number | null;
    dev_bypass_enabled: boolean;
  };
  images: {
    api: string;
    mcp: string | null;
    web: string | null;
    watcher: string | null;
  };
}

export interface VerifyMcpCheck {
  name: string;
  status: 'pass' | 'fail' | 'error';
  detail: string;
}

export interface VerifyMcpResult {
  checks: VerifyMcpCheck[];
  all_passed: boolean;
  ran_at: string;
}
