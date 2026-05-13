// Typed API client for URSA-OSCAR's backend.
// Raw `fetch()` per ADR-001 (no TanStack Query) — small surface, single user.

import type {
  ImportLogEntry,
  ManualLogEntry,
  ManualLogType,
  NightlyEvent,
  NightlySummary,
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

  triggerImport: (source_path: string) =>
    request<ImportLogEntry>(`${BASE}/imports`, {
      method: 'POST',
      body: JSON.stringify({ source_path }),
    }),

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
};

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
