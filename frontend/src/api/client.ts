// Typed API client for URSA-OSCAR's backend.
// Raw `fetch()` per ADR-001 (no TanStack Query) — small surface, single user.

import type {
  AiConfigPatch,
  AiMaskedConfig,
  AiMessage,
  AiProviderPreset,
  AiStreamEvent,
  AiTestResult,
  ImportJob,
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

// Phase 6.4 — auth-aware response handling.
//
// Two changes from the pre-auth client:
//   1. credentials: 'include' so the httpOnly session cookie travels
//      with every fetch (same-origin in production behind nginx;
//      cross-origin in dev via Vite proxy where Vite forwards cookies
//      correctly).
//   2. 401 → automatic redirect to /login (with the current pathname
//      saved in sessionStorage so post-login we route back). Auth
//      endpoints + the /login + /setup pages themselves are exempt;
//      they handle 401 inline.
//
// The ApiError is still thrown for callers that want to handle it,
// but the redirect happens in parallel — most callers don't need to
// know.

const _AUTH_PATHS = ['/api/v1/auth/'];
const _NO_REDIRECT_LOCATIONS = ['/login', '/setup'];

function _maybeRedirectOn401(path: string): void {
  if (_AUTH_PATHS.some((p) => path.startsWith(p))) return;
  if (typeof window === 'undefined') return;
  const here = window.location.pathname + window.location.search;
  if (_NO_REDIRECT_LOCATIONS.some((p) => window.location.pathname.startsWith(p))) {
    return;
  }
  try {
    sessionStorage.setItem('ursa_oscar_return_to', here);
  } catch { /* sessionStorage disabled — fine, default redirect to / */ }
  window.location.assign('/login');
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
  const res = await fetch(url, {
    ...init,
    headers,
    credentials: 'include',  // 0.13.0 — session cookie
  });
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = await res.text().catch(() => undefined); }
    if (res.status === 401) {
      _maybeRedirectOn401(path);
    }
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
   * Fetch one or more waveform channels for a single night.
   *
   * 0.7.1 timezone fix — server now returns naive ISO 8601 strings
   * (matching the events endpoint's convention). We parse them with
   * ``new Date(iso)``, which interprets naive ISO as local time —
   * the right behavior for ResMed devices that record wall-clock with
   * no timezone awareness. The resulting epoch seconds align with the
   * events endpoint's local-parsed timestamps + summary.start_time
   * bounds, so the X axis, the EventRug, and the hover tooltip all
   * show the recording's wall-clock value.
   */
  getTimeseries: async (date: string, series: string[]) => {
    type RawSeries = { timestamps: string[]; values: (number | null)[]; secondary: (number | null)[] | null };
    type RawResp = { date: string; series: Record<string, RawSeries> };
    const resp = await request<RawResp>(`${BASE}/timeseries/${date}`, {}, { series });
    const out: Record<string, { timestamps: number[]; values: (number | null)[]; secondary: (number | null)[] | null }> = {};
    for (const [k, s] of Object.entries(resp.series)) {
      out[k] = {
        timestamps: s.timestamps.map((iso) => new Date(iso).getTime() / 1000),
        values: s.values,
        secondary: s.secondary,
      };
    }
    return { date: resp.date, series: out };
  },

  // 0.8.0 — /imports now enqueues asynchronously. Returns the
  // ImportJob (status='queued'); poll getImportJob until the worker
  // completes. listActiveImportJobs powers the Import page polling.
  triggerImport: (source_path: string, force = false) =>
    request<ImportJob>(
      `${BASE}/imports`,
      { method: 'POST', body: JSON.stringify({ source_path }) },
      { force: force ? 'true' : undefined },
    ),

  getImportJob: (job_id: number) =>
    request<ImportJob>(`${BASE}/imports/jobs/${job_id}`),

  listImportJobs: (params?: { active_only?: boolean; limit?: number }) =>
    request<ImportJob[]>(`${BASE}/imports/jobs`, {}, {
      ...(params?.active_only ? { active_only: 'true' } : {}),
      ...(params?.limit ? { limit: String(params.limit) } : {}),
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
  // Phase 6 Ticket 6.1 — multivariate + lag + cache stats.
  // =====================================================================

  multivariateCorrelation: (body: {
    target_metric: string;
    predictor_metrics: string[];
    start_date: string;
    end_date: string;
    recompute?: boolean;
  }) =>
    request<{
      ok: boolean;
      data: {
        method: string;
        target_metric: string;
        predictors: Array<{
          metric: string;
          partial_r: number | null;
          p_value: number | null;
          ci_95: [number | null, number | null];
          interpretation: string;
          note?: string;
        }>;
        controlled_for: string[];
        n_observations: number;
        confidence_level?: string;
        sample_caveat?: string | null;
        bootstrap_samples?: number;
        multicollinear_pairs?: Array<{ metric_a: string; metric_b: string; r: number; note: string }>;
        cache_age_seconds?: number;
        computed_at?: string;
        code?: string;
        error?: string;
      };
    }>(`${BASE}/analytics/multivariate-correlation`, {
      method: 'POST', body: JSON.stringify(body),
    }),

  lagCorrelation: (body: {
    metric_a: string;
    metric_b: string;
    start_date: string;
    end_date: string;
    lag_range_days?: [number, number];
    bootstrap_samples?: number;
    recompute?: boolean;
  }) =>
    request<{
      ok: boolean;
      data: {
        method: string;
        metric_a: string;
        metric_b: string;
        lag_range: [number, number];
        lag_correlations: Array<{
          lag_days: number;
          r: number | null;
          p_value: number | null;
          ci_95: [number | null, number | null];
          n_aligned: number;
          note?: string;
        }>;
        peak_lag_days: number | null;
        peak_correlation: number | null;
        peak_p_value: number | null;
        interpretation: string;
        clinical_note: string | null;
        n_observations: number;
        confidence_level?: string;
        sample_caveat?: string | null;
        bootstrap_samples?: number;
        cache_age_seconds?: number;
        computed_at?: string;
        code?: string;
        error?: string;
      };
    }>(`${BASE}/analytics/lag-correlation`, {
      method: 'POST', body: JSON.stringify(body),
    }),

  // Phase 6 Ticket 6.2 — predictive modeling + counterfactuals.
  predict: (body: {
    target_metric: string;
    predictor_metrics: string[];
    training_start_date: string;
    training_end_date: string;
    counterfactual_inputs?: Record<string, number> | null;
    recompute?: boolean;
  }) =>
    request<{
      ok: boolean;
      data: {
        method: string;
        target_metric: string;
        predictor_metrics: string[];
        training_date_range: { start: string; end: string };
        n_training_nights: number;
        confidence_level?: string;
        sample_caveat?: string | null;
        prediction: {
          point_estimate: number;
          prediction_interval_95: [number | null, number | null];
          prediction_interval_50: [number | null, number | null];
        };
        model_details: {
          selected_alpha: number;
          cross_validation_r2: number;
          predictor_coefficients: Array<{
            predictor: string;
            coefficient: number;
            abs_importance: number;
          }>;
          intercept: number;
          baseline_inputs: Record<string, number>;
          quantiles_fitted: number[];
        };
        counterfactual: {
          baseline_prediction: number;
          counterfactual_prediction: number;
          counterfactual_prediction_intervals: {
            prediction_interval_95: [number | null, number | null];
            prediction_interval_50: [number | null, number | null];
          };
          delta: number;
          delta_relative_pct: number | null;
          overridden_predictors: string[];
          interpretation: string;
        } | null;
        cache_age_seconds?: number;
        computed_at?: string;
        code?: string;
        error?: string;
      };
    }>(`${BASE}/analytics/predict`, {
      method: 'POST', body: JSON.stringify(body),
    }),

  getAnalyticalCacheStats: () =>
    request<{
      total_entries: number;
      total_hits: number;
      cache_hit_rate: number;
      oldest_entry_age_seconds: number;
      largest_entry_bytes: number;
      by_tool: Record<string, { entries: number; hits: number; avg_compute_ms: number }>;
    }>(`${BASE}/analytics/cache/stats`),

  clearAnalyticalCache: () =>
    request<{ entries_cleared: number }>(`${BASE}/analytics/cache/clear`, {
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    }),

  // =====================================================================
  // Phase 6 Ticket 6.3 — provider PDF reports.
  // =====================================================================

  /** Preview metadata: what's in the PDF before generating. */
  previewReportMetadata: (params: {
    template: 'full_clinical_report' | 'summary_report' | 'analytical_report';
    start_date: string;
    end_date: string;
  }) =>
    request<{
      template: string;
      template_label: string;
      estimated_page_count: number;
      sections_included: string[];
      sections_with_insufficient_data: string[];
      n_nights_in_range: number;
      confidence_level_for_predictions: string | null;
      methods_used: string[];
      methodology_section_includes: string[];
      pdf_bytes: number;
      generated_at: string;
      date_range_start: string;
      date_range_end: string;
    }>(`${BASE}/reports/preview-metadata`, {}, params),

  /** Build the URL for the PDF download (browser navigates to it). */
  generateReportUrl: () => `${BASE}/reports/generate`,

  /** Trigger generation via fetch + download as a Blob. Used by the
   *  UI's "Generate PDF" button so we can show progress + handle
   *  errors instead of relying on the browser's blind navigation. */
  generateReportBlob: async (body: {
    template: 'full_clinical_report' | 'summary_report' | 'analytical_report';
    start_date: string;
    end_date: string;
    recompute?: boolean;
  }) => {
    const res = await fetch(`${BASE}/reports/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail: unknown;
      try { detail = await res.json(); } catch { detail = await res.text(); }
      throw new ApiError(res.status, `POST /reports/generate -> ${res.status}`, detail);
    }
    const cd = res.headers.get('content-disposition') ?? '';
    const m = /filename="([^"]+)"/.exec(cd);
    const filename = m?.[1] ?? 'URSA-OSCAR-report.pdf';
    const blob = await res.blob();
    return { blob, filename };
  },

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
    // 0.8.0 — the endpoint now returns an ImportJob (queued); the worker
    // processes off-thread. Caller polls getImportJob for the result.
    return new Promise<ImportJob>((resolve, reject) => {
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

  // ===================================================================
  // 0.9.7 — OSCAR-compatible CSV export endpoints.
  // ===================================================================

  /** Build a URL the browser can navigate to to download an OSCAR-shape
   *  CSV. Optional date range; omit both for the most-recent-day default. */
  oscarExportUrl: (
    exportType: 'summary' | 'sessions' | 'daily',
    range?: { start_date: string; end_date: string },
  ) => {
    const path = `${BASE}/exports/oscar/${exportType}.csv`;
    if (!range) return path;
    return `${path}?start_date=${range.start_date}&end_date=${range.end_date}`;
  },

  /** Write the OSCAR-shape CSV to the API container's EXPORTS_PATH on
   *  disk. Returns the saved filename + container path + byte count. */
  oscarServerExport: (req: {
    export_type: 'summary' | 'sessions' | 'daily';
    start_date?: string;
    end_date?: string;
  }) =>
    request<{ filename: string; path: string; bytes: number; rows: number }>(
      `${BASE}/exports/oscar/server`,
      { method: 'POST', body: JSON.stringify(req) },
    ),

  // =====================================================================
  // Phase 5 — AI proxy. Five endpoints; the chat path uses SSE not fetch.
  // =====================================================================

  listAiProviders: () =>
    request<{ providers: AiProviderPreset[] }>(`${BASE}/ai/providers`),

  getAiConfig: () => request<AiMaskedConfig>(`${BASE}/ai/config`),

  patchAiConfig: (patch: AiConfigPatch) =>
    request<AiMaskedConfig>(`${BASE}/ai/config`, {
      method: 'POST',
      body: JSON.stringify(patch),
    }),

  // 0.9.10 — file-backed editable system-prompt template.
  // GET returns the current content + a `source` flag ('default' on a
  // fresh install, 'file' after the operator has saved one). PUT
  // persists the new content; subsequent chat sessions without a
  // per-provider override read from it at runtime.
  getSystemPromptTemplate: () =>
    request<{ template: string; source: 'default' | 'file' }>(
      `${BASE}/ai/system-prompt/template`,
    ),

  setSystemPromptTemplate: (template: string) =>
    request<{ template: string; source: 'default' | 'file' }>(
      `${BASE}/ai/system-prompt/template`,
      { method: 'PUT', body: JSON.stringify({ template }) },
    ),

  // 0.11.1 — reset to the in-code DEFAULT_TEMPLATE shipped with the
  // running API image. Drops the operator's saved file, so subsequent
  // GETs return source='default'. Useful when a new image ships
  // richer template content and the operator wants to adopt the
  // upstream version rather than stay forked on their old saved file.
  resetSystemPromptTemplateToDefault: () =>
    request<{ template: string; source: 'default' | 'file' }>(
      `${BASE}/ai/system-prompt/template`,
      { method: 'DELETE' },
    ),

  testAiProvider: (provider_id: string) =>
    request<AiTestResult>(`${BASE}/ai/test`, {
      method: 'POST',
      body: JSON.stringify({ provider_id }),
    }),

  /**
   * Open an SSE chat stream. Returns an async generator over parsed
   * AiStreamEvent objects. Caller iterates with `for await (...)`.
   *
   * We use ``fetch + ReadableStream`` rather than ``EventSource``
   * because EventSource doesn't support POST bodies.
   *
   * 0.9.2 hardening (after operator hit a "stuck at running" bug):
   *   - Accept BOTH ``\\n\\n`` and ``\\r\\n\\r\\n`` frame separators
   *     (some proxies normalize line endings)
   *   - Flush the TextDecoder + drain any remaining buffered frame
   *     after the read loop exits (so a final frame that's not
   *     terminated with the separator still gets parsed)
   *   - Skip SSE comment lines (``: keepalive``) cleanly — these are
   *     emitted by the backend to keep the connection alive during
   *     quiet stretches but aren't data frames
   *   - Log frame counts to the browser console when
   *     ``localStorage.ursa_oscar_chat_debug === '1'`` so operators
   *     can verify they're seeing the events they expect
   */
  chatStream: async function* (
    messages: AiMessage[],
    context: { current_date?: string; include_profile?: boolean } = {},
    signal?: AbortSignal,
  ): AsyncGenerator<AiStreamEvent, void, void> {
    const resp = await fetch(`${BASE}/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ messages, context }),
      signal,
    });
    if (!resp.ok || !resp.body) {
      let detail = '';
      try { detail = (await resp.json()).detail ?? ''; } catch { /* ignore */ }
      throw new ApiError(resp.status, `POST /ai/chat -> ${resp.status}`, detail);
    }

    const debug = typeof localStorage !== 'undefined'
      && localStorage.getItem('ursa_oscar_chat_debug') === '1';
    const dlog = (...args: unknown[]) => {
      if (debug) console.log('[ursa chat sse]', ...args);
    };

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let frameCount = 0;

    /** Split buffer on the FIRST frame separator (\n\n OR \r\n\r\n).
     *  Returns [frame, rest] or null if no separator found. */
    function pluck(): [string, string] | null {
      const lf = buffer.indexOf('\n\n');
      const crlf = buffer.indexOf('\r\n\r\n');
      // Use whichever separator appears FIRST in the buffer.
      let idx = -1;
      let sepLen = 0;
      if (lf !== -1 && (crlf === -1 || lf < crlf)) {
        idx = lf; sepLen = 2;
      } else if (crlf !== -1) {
        idx = crlf; sepLen = 4;
      }
      if (idx === -1) return null;
      return [buffer.slice(0, idx), buffer.slice(idx + sepLen)];
    }

    function* drain(): Generator<AiStreamEvent, void, void> {
      while (true) {
        const split = pluck();
        if (!split) break;
        const [frame, rest] = split;
        buffer = rest;
        // Skip empty frames + SSE comment lines (": keepalive").
        const trimmed = frame.trim();
        if (!trimmed) continue;
        if (trimmed.startsWith(':')) {
          dlog('comment:', trimmed.slice(0, 40));
          continue;
        }
        if (!trimmed.startsWith('data:')) {
          dlog('non-data frame skipped:', trimmed.slice(0, 80));
          continue;
        }
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          const event = JSON.parse(payload) as AiStreamEvent;
          frameCount += 1;
          dlog(`event ${frameCount}:`, event.event_type, event.payload);
          yield event;
        } catch (e) {
          dlog('malformed JSON:', payload.slice(0, 100), e);
        }
      }
    }

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          // Flush the decoder (drains any partial UTF-8 sequence).
          buffer += decoder.decode();
          // Drain any final frame that might not have been terminated
          // with the separator. We append the separator first so the
          // pluck() loop catches the last fragment if any.
          if (buffer && !buffer.endsWith('\n\n') && !buffer.endsWith('\r\n\r\n')) {
            buffer += '\n\n';
          }
          yield* drain();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        yield* drain();
      }
    } finally {
      dlog(`stream ended after ${frameCount} events`);
    }
  },

  // =====================================================================
  // Phase 6.4 — auth.
  //
  // Backend cookie + JWT model:
  //   - login/bootstrap/change-password issue a session JWT (24h) and
  //     ALSO set it as an httpOnly cookie. Browser requests ride the
  //     cookie; the `token` in the response body is only useful when a
  //     non-browser client is calling these endpoints (the MCP server
  //     and watcher use generateApiToken instead).
  //   - generateApiToken returns a 90d JWT meant for service-to-service
  //     bearer usage. Server does NOT store it — its validity is the
  //     HS256 signature.
  // =====================================================================

  bootstrapStatus: () =>
    request<BootstrapStatusResponse>(`${BASE}/auth/bootstrap-status`),

  bootstrap: (password: string) =>
    request<AuthTokenResponse>(`${BASE}/auth/bootstrap`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  login: (password: string) =>
    request<AuthTokenResponse>(`${BASE}/auth/login`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  logout: () =>
    request<{ ok: boolean }>(`${BASE}/auth/logout`, { method: 'POST' }),

  sessionInfo: () =>
    request<AuthSessionResponse>(`${BASE}/auth/session`),

  changePassword: (current_password: string, new_password: string) =>
    request<AuthTokenResponse>(`${BASE}/auth/change-password`, {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),

  generateApiToken: () =>
    request<AuthTokenResponse>(`${BASE}/auth/generate-api-token`, {
      method: 'POST',
    }),
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
    // 1.1.3 — every field can now be null. API introspects all four
    // (importlib.metadata for self, /version probe for MCP, shared
    // /data/versions/watcher.txt for watcher). Web stays null unless
    // the operator sets an explicit display override; the bundle's
    // baked __URSA_WEB_VERSION__ is the fallback the UI reads.
    api: string | null;
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

// =====================================================================
// Phase 6.4 — auth response shapes (mirror backend TokenResponse /
// SessionResponse).
// =====================================================================

export interface AuthTokenResponse {
  ok: boolean;
  token: string;
  /** "session" (24h, cookie + body) or "api" (90d, body only). */
  token_kind: 'session' | 'api';
  expires_at_iso: string;
}

export interface AuthSessionResponse {
  user: string;
  token_kind: 'session' | 'api';
  issued_at_iso: string;
  expires_at_iso: string;
  expires_in_seconds: number;
}

// 0.13.3 — connection diagnostic returned with bootstrap-status so
// the /login and /setup pages can render a warning when a reverse
// proxy is misconfigured (HTTPS at the browser, plain HTTP + no
// X-Forwarded-Proto reaching the API container).
export interface ConnectionDiagnostic {
  detected_https: boolean;
  detection_source: 'url' | 'x-forwarded-proto' | 'origin' | 'referer' | 'none';
  /** Non-null only when there's an actionable misconfiguration —
   *  HTTPS detected via the Origin/Referer fallback rather than the
   *  canonical X-Forwarded-Proto signal. */
  warning: string | null;
}

export interface BootstrapStatusResponse {
  bootstrapped: boolean;
  connection?: ConnectionDiagnostic;
}
