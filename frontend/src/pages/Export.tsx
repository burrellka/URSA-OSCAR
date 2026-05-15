/**
 * Export page — 0.9.7.
 *
 * OSCAR-shape CSV export with the same quick-range presets the OSCAR
 * desktop tool ships:
 *   Most Recent Day (default), Last Week, Last Fortnight, Last Month,
 *   Last 6 Months, Last Year, Everything, Custom.
 *
 * Three export types map to OSCAR's three CSV shapes (Summary, Sessions,
 * Daily). Destination is either "Download to browser" (issues an
 * attachment via the GET endpoint) or "Save to server" (POST that
 * writes the CSV under EXPORTS_PATH inside the API container so the
 * operator can pick it up from the bound volume).
 */
import { useEffect, useMemo, useState } from 'react';
import { Download as DownloadIcon, HardDrive } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { NightlySummary } from '../api/types';


type QuickRange =
  | 'most_recent_day'
  | 'last_week'
  | 'last_fortnight'
  | 'last_month'
  | 'last_6_months'
  | 'last_year'
  | 'everything'
  | 'custom';

const QUICK_RANGE_OPTIONS: { value: QuickRange; label: string; days: number | 'all' | 'recent' }[] = [
  { value: 'most_recent_day', label: 'Most Recent Day', days: 'recent' },
  { value: 'last_week', label: 'Last Week', days: 7 },
  { value: 'last_fortnight', label: 'Last Fortnight', days: 14 },
  { value: 'last_month', label: 'Last Month', days: 30 },
  { value: 'last_6_months', label: 'Last 6 Months', days: 180 },
  { value: 'last_year', label: 'Last Year', days: 365 },
  { value: 'everything', label: 'Everything', days: 'all' },
  { value: 'custom', label: 'Custom', days: 'all' },  // 'days' unused for custom
];

type ExportType = 'summary' | 'sessions' | 'daily';
type Destination = 'download' | 'server';


function isoMinusDays(iso: string, days: number): string {
  // iso = "YYYY-MM-DD" — local-date arithmetic is fine here since
  // OSCAR dates are calendar days, not instants. Date.parse() would
  // interpret as UTC midnight which can shift across DST; we use the
  // explicit constructor to stay in the local-day domain.
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() - days);
  const yyyy = dt.getFullYear();
  const mm = String(dt.getMonth() + 1).padStart(2, '0');
  const dd = String(dt.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}


export default function ExportPage() {
  const [nights, setNights] = useState<NightlySummary[]>([]);
  const [loadingNights, setLoadingNights] = useState(true);
  const [nightsError, setNightsError] = useState<string | null>(null);

  const [quickRange, setQuickRange] = useState<QuickRange>('most_recent_day');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [exportType, setExportType] = useState<ExportType>('summary');
  const [destination, setDestination] = useState<Destination>('download');

  const [submitting, setSubmitting] = useState(false);
  const [serverResult, setServerResult] = useState<{
    filename: string; path: string; bytes: number; rows: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load nights once so we know the date bounds for the range presets.
  useEffect(() => {
    let cancelled = false;
    setLoadingNights(true);
    api.listNights()
      .then((rows) => { if (!cancelled) setNights(rows); })
      .catch((e) => {
        if (!cancelled) {
          setNightsError(
            e instanceof ApiError ? e.message : String(e),
          );
        }
      })
      .finally(() => { if (!cancelled) setLoadingNights(false); });
    return () => { cancelled = true; };
  }, []);

  // The resolved (start, end) pair for the current selection. Null when
  // we don't have enough info yet (e.g., custom + missing dates, or
  // nights still loading).
  const resolvedRange = useMemo<{ start: string; end: string } | null>(() => {
    if (quickRange === 'custom') {
      if (!customStart || !customEnd) return null;
      if (customEnd < customStart) return null;
      return { start: customStart, end: customEnd };
    }
    if (nights.length === 0) return null;
    // listNights returns ascending; pull the latest.
    const latest = nights[nights.length - 1].date;
    const earliest = nights[0].date;
    const preset = QUICK_RANGE_OPTIONS.find((o) => o.value === quickRange);
    if (!preset) return null;
    if (preset.days === 'recent') {
      return { start: latest, end: latest };
    }
    if (preset.days === 'all') {
      return { start: earliest, end: latest };
    }
    // N-day windows are "the latest N calendar days inclusive of the
    // latest night". Matches OSCAR's behavior.
    return { start: isoMinusDays(latest, preset.days - 1), end: latest };
  }, [quickRange, customStart, customEnd, nights]);

  // Filename preview — same logic the backend uses.
  const filenamePreview = useMemo(() => {
    if (!resolvedRange) return null;
    const label = exportType === 'summary' ? 'Summary'
      : exportType === 'sessions' ? 'Sessions'
      : 'Daily';
    if (resolvedRange.start === resolvedRange.end) {
      return `URSA-OSCAR_${label}_${resolvedRange.start}.csv`;
    }
    return `URSA-OSCAR_${label}_${resolvedRange.start}_to_${resolvedRange.end}.csv`;
  }, [resolvedRange, exportType]);

  // How many nights / sessions / events would this export cover? For
  // Summary and Sessions we can give an accurate row preview from the
  // nights list (sessions = sum of session_count). For Daily, the
  // events count isn't in the nights summary, so we omit.
  const rowPreview = useMemo<string | null>(() => {
    if (!resolvedRange) return null;
    const inRange = nights.filter(
      (n) => n.date >= resolvedRange.start && n.date <= resolvedRange.end,
    );
    if (exportType === 'summary') {
      return `${inRange.length} night${inRange.length === 1 ? '' : 's'}`;
    }
    if (exportType === 'sessions') {
      const total = inRange.reduce((sum, n) => sum + (n.session_count ?? 0), 0);
      return `${total} session${total === 1 ? '' : 's'} across ${inRange.length} night${inRange.length === 1 ? '' : 's'}`;
    }
    // Daily — show night count only; events-per-night isn't in the summary.
    return `${inRange.length} night${inRange.length === 1 ? '' : 's'} of event data`;
  }, [resolvedRange, exportType, nights]);

  async function runExport() {
    if (!resolvedRange) return;
    setError(null);
    setServerResult(null);

    if (destination === 'download') {
      // Issue a browser-side navigation to the GET endpoint. The
      // Content-Disposition header makes the browser save it.
      const url = api.oscarExportUrl(exportType, {
        start_date: resolvedRange.start,
        end_date: resolvedRange.end,
      });
      window.location.href = url;
      return;
    }

    // Save-to-server path.
    setSubmitting(true);
    try {
      const result = await api.oscarServerExport({
        export_type: exportType,
        start_date: resolvedRange.start,
        end_date: resolvedRange.end,
      });
      setServerResult(result);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`${e.message}${e.body ? ` — ${JSON.stringify(e.body)}` : ''}`);
      } else {
        setError(String(e));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const canRun = resolvedRange !== null && !submitting && !loadingNights;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Export</h1>
      </div>

      <div
        className="chart-card"
        style={{ maxWidth: '720px', marginBottom: '1rem' }}
      >
        <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          Export your imported data as OSCAR-shape CSV. Three CSV types
          mirror OSCAR's own outputs (Summary, Sessions, Daily events),
          so anything that consumes an OSCAR CSV — SleepHQ, the
          oscar-parity scripts, your own R/Python notebooks — works
          drop-in.
        </div>

        {/* --- Quick Range --- */}
        <div className="field" style={{ marginBottom: '1rem' }}>
          <label htmlFor="quick-range">Quick Range</label>
          <select
            id="quick-range"
            value={quickRange}
            onChange={(e) => setQuickRange(e.target.value as QuickRange)}
            disabled={loadingNights}
          >
            {QUICK_RANGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        {quickRange === 'custom' && (
          <div
            style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr',
              gap: '0.75rem', marginBottom: '1rem',
            }}
          >
            <div className="field">
              <label htmlFor="custom-start">Start date</label>
              <input
                id="custom-start"
                type="date"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
                max={customEnd || undefined}
              />
            </div>
            <div className="field">
              <label htmlFor="custom-end">End date</label>
              <input
                id="custom-end"
                type="date"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
                min={customStart || undefined}
              />
            </div>
          </div>
        )}

        {/* Resolved-range preview line. */}
        {resolvedRange && (
          <div
            style={{
              fontSize: '0.8125rem',
              color: 'var(--text-muted)',
              marginBottom: '1rem',
              padding: '0.5rem 0.75rem',
              background: 'var(--bg-secondary, #f3f4f6)',
              borderRadius: '6px',
            }}
          >
            <span style={{ color: 'var(--text-secondary)' }}>Range: </span>
            <strong style={{ color: 'var(--text-primary)' }}>
              {resolvedRange.start === resolvedRange.end
                ? resolvedRange.start
                : `${resolvedRange.start} → ${resolvedRange.end}`}
            </strong>
            {rowPreview && (
              <span style={{ marginLeft: '0.75rem' }}>· {rowPreview}</span>
            )}
          </div>
        )}

        {/* --- Export type --- */}
        <div className="field" style={{ marginBottom: '1rem' }}>
          <label>Export type</label>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {([
              { v: 'summary' as const, label: 'Summary', hint: 'One row per night' },
              { v: 'sessions' as const, label: 'Sessions', hint: 'One row per session' },
              { v: 'daily' as const, label: 'Daily', hint: 'One row per event' },
            ]).map((opt) => (
              <button
                key={opt.v}
                type="button"
                onClick={() => setExportType(opt.v)}
                className={exportType === opt.v ? 'btn-primary' : 'btn-secondary'}
                style={{
                  flex: 1, padding: '0.625rem 0.75rem',
                  textAlign: 'left', lineHeight: 1.2,
                }}
              >
                <div style={{ fontWeight: 600 }}>{opt.label}</div>
                <div style={{ fontSize: '0.75rem', opacity: 0.8, marginTop: '0.125rem' }}>
                  {opt.hint}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* --- Destination --- */}
        <div className="field" style={{ marginBottom: '1rem' }}>
          <label>Destination</label>
          <div style={{ display: 'flex', gap: '1.25rem' }}>
            <label
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}
            >
              <input
                type="radio"
                name="destination"
                value="download"
                checked={destination === 'download'}
                onChange={() => setDestination('download')}
              />
              <DownloadIcon size={14} /> Download to browser
            </label>
            <label
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}
            >
              <input
                type="radio"
                name="destination"
                value="server"
                checked={destination === 'server'}
                onChange={() => setDestination('server')}
              />
              <HardDrive size={14} /> Save to server
            </label>
          </div>
          {destination === 'server' && (
            <div
              style={{
                fontSize: '0.75rem',
                color: 'var(--text-muted)',
                marginTop: '0.375rem',
              }}
            >
              Writes to the API container's <code>EXPORTS_PATH</code>
              {' '}(default <code>/data/exports</code>). Visible on
              the host wherever you've bound that volume.
            </div>
          )}
        </div>

        {/* Filename preview */}
        {filenamePreview && (
          <div
            style={{
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              marginBottom: '1rem',
            }}
          >
            Filename: <code>{filenamePreview}</code>
          </div>
        )}

        <button
          type="button"
          className="btn-primary"
          onClick={runExport}
          disabled={!canRun}
        >
          {submitting ? 'Saving…' : destination === 'download' ? 'Download' : 'Save to server'}
        </button>

        {!resolvedRange && !loadingNights && (
          <div
            style={{
              marginTop: '0.625rem',
              fontSize: '0.75rem',
              color: 'var(--ahi-warn, #d97706)',
            }}
          >
            {quickRange === 'custom'
              ? 'Pick a start and end date to enable export.'
              : 'No nights have been imported yet — import some data first.'}
          </div>
        )}
      </div>

      {/* --- Result tile (server-save success) --- */}
      {serverResult && (
        <div className="chart-card" style={{ maxWidth: '720px', marginBottom: '1rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginTop: 0, marginBottom: '0.5rem' }}>
            Saved on server
          </h2>
          <div style={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
            <div><strong>File:</strong> <code>{serverResult.filename}</code></div>
            <div><strong>Path (in container):</strong> <code>{serverResult.path}</code></div>
            <div><strong>Rows:</strong> {serverResult.rows.toLocaleString()}</div>
            <div><strong>Size:</strong> {(serverResult.bytes / 1024).toFixed(1)} KB</div>
          </div>
        </div>
      )}

      {/* --- Error banner --- */}
      {error && (
        <div className="chart-card error-banner" style={{ maxWidth: '720px' }}>
          {error}
        </div>
      )}
      {nightsError && (
        <div className="chart-card error-banner" style={{ maxWidth: '720px' }}>
          Couldn't load the night list: {nightsError}
        </div>
      )}
    </div>
  );
}
