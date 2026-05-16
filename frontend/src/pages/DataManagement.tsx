import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Trash2, RefreshCw, Database, BarChart3 } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type {
  PreviewDeleteResult,
  DeleteRangeResult,
  SystemConfig,
} from '../api/client';

/**
 * Settings → Data Management — Phase 3 close-out sprint.
 *
 * Two operations live here, both architect-blessed as hard-delete (no
 * archive/restore):
 *
 *   A. Purge nights by date range — preview, then type-to-confirm to
 *      enable the destructive button. Optional 'also delete manual
 *      logs' flag defaults UNCHECKED per architect directive
 *      ("Recommended: keep manual logs — they represent observations
 *       independent of CPAP recordings").
 *
 *   B. Run CHECKPOINT — manual disk-space reclaim trigger. DuckDB's
 *      CHECKPOINT persists WAL into the main file; future inserts
 *      reuse the freed blocks. Useful after a large delete.
 *
 * No archive UI, no restore UI, no soft-delete.
 */
export default function DataManagement() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Range purge state
  const [start, setStart] = useState<string>('');
  const [end, setEnd] = useState<string>('');
  const [deleteManualLogs, setDeleteManualLogs] = useState(false);
  const [preview, setPreview] = useState<PreviewDeleteResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<DeleteRangeResult | null>(null);

  // Checkpoint state
  const [checkpointing, setCheckpointing] = useState(false);
  const [checkpointResult, setCheckpointResult] = useState<{
    db_size_before_mb: number | null; db_size_after_mb: number | null;
  } | null>(null);

  // Phase 6 Ticket 6.1 — analytical cache stats + clear.
  const [cacheStats, setCacheStats] = useState<Awaited<ReturnType<typeof api.getAnalyticalCacheStats>> | null>(null);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [cacheClearConfirm, setCacheClearConfirm] = useState('');
  const [cacheClearing, setCacheClearing] = useState(false);

  const refreshCacheStats = useCallback(async () => {
    setCacheLoading(true);
    try {
      const s = await api.getAnalyticalCacheStats();
      setCacheStats(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCacheLoading(false);
    }
  }, []);

  useEffect(() => {
    api.getSystemConfig().then(setConfig).catch((e: ApiError) => setError(e.message));
    refreshCacheStats();
  }, [refreshCacheStats]);

  async function handleClearCache() {
    if (cacheClearConfirm !== 'CLEAR') return;
    setCacheClearing(true);
    try {
      const r = await api.clearAnalyticalCache();
      showToast(`Cleared ${r.entries_cleared} analytical-cache entr${r.entries_cleared === 1 ? 'y' : 'ies'}.`);
      setCacheClearConfirm('');
      await refreshCacheStats();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCacheClearing(false);
    }
  }

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 5000);
  }

  async function handlePreview() {
    if (!start || !end) return;
    setPreviewing(true);
    setError(null);
    setPreview(null);
    setDeleteResult(null);
    try {
      setPreview(await api.previewDelete(start, end));
      setConfirmText('');  // reset confirmation each new preview
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }

  const expectedConfirm = start && end ? `${start} TO ${end}` : '';
  const confirmMatches = expectedConfirm !== '' && confirmText.trim() === expectedConfirm;

  async function handleDelete() {
    if (!start || !end || !confirmMatches) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await api.deleteNightsRange(start, end, deleteManualLogs);
      setDeleteResult(result);
      setPreview(null);
      setConfirmText('');
      const reclaimed = (result.db_size_before_mb ?? 0) - (result.db_size_after_mb ?? 0);
      showToast(
        `${result.nights_deleted} night(s) deleted` +
        (reclaimed > 0.1 ? ` — ${reclaimed.toFixed(1)} MB reclaimed` : ''),
      );
      // Refresh DB-size display
      api.getSystemConfig().then(setConfig).catch(() => {});
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  async function handleCheckpoint() {
    setCheckpointing(true);
    setError(null);
    try {
      const result = await api.runCheckpoint();
      setCheckpointResult(result);
      api.getSystemConfig().then(setConfig).catch(() => {});
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCheckpointing(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Data Management</h1>
        <a href="/settings" className="btn-secondary" style={{ fontSize: '0.8125rem' }}>← Back to Settings</a>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* --- Section A: Range purge ----------------------------------------- */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Trash2 size={16} color="var(--ahi-bad, #dc2626)" />
          Purge nights by date range
        </h2>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
          Permanently removes nightly summary, events, and waveform data for the date range.
          Reimportable from the original SD-card export if needed. <strong>This action cannot be undone.</strong>
        </p>

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'end', marginBottom: '0.75rem' }}>
          <div className="field" style={{ width: '11rem' }}>
            <label>Start date</label>
            <input type="date" value={start} onChange={(e) => { setStart(e.target.value); setPreview(null); setDeleteResult(null); }} />
          </div>
          <div className="field" style={{ width: '11rem' }}>
            <label>End date</label>
            <input type="date" value={end} onChange={(e) => { setEnd(e.target.value); setPreview(null); setDeleteResult(null); }} />
          </div>
          <button
            type="button"
            className="btn-secondary"
            disabled={!start || !end || previewing}
            onClick={handlePreview}
          >
            {previewing ? 'Previewing…' : 'Preview'}
          </button>
        </div>

        <label style={{ display: 'inline-flex', alignItems: 'flex-start', gap: '0.5rem', marginBottom: '0.75rem', fontSize: '0.875rem' }}>
          <input
            type="checkbox"
            checked={deleteManualLogs}
            onChange={(e) => setDeleteManualLogs(e.target.checked)}
            style={{ marginTop: '0.1875rem' }}
          />
          <span>
            Also delete manual log entries from these dates
            <br />
            <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
              Recommended: leave unchecked. Manual logs represent observations independent of the CPAP recording.
            </span>
          </span>
        </label>

        {preview && (
          <div
            style={{
              padding: '0.75rem',
              background: 'var(--bg-secondary)',
              borderRadius: '6px',
              border: '1px solid var(--border-color)',
              fontSize: '0.875rem',
              marginBottom: '0.75rem',
            }}
          >
            <strong style={{ color: 'var(--ahi-bad, #dc2626)' }}>Will permanently remove:</strong>
            <ul style={{ margin: '0.375rem 0 0 1.25rem' }}>
              <li>{preview.nights} night{preview.nights === 1 ? '' : 's'} of nightly summary data</li>
              <li>{preview.events.toLocaleString()} respiratory events</li>
              <li>{preview.timeseries_rows.toLocaleString()} waveform sample rows (~{Math.round(preview.timeseries_rows / 12500)} MB)</li>
              <li>
                {preview.manual_logs} manual log{preview.manual_logs === 1 ? '' : 's'}
                {' '}<span style={{ color: 'var(--text-muted)' }}>({deleteManualLogs ? 'will be deleted' : 'kept'})</span>
              </li>
            </ul>
            {preview.nights > 0 && (
              <div style={{ marginTop: '0.5rem' }}>
                <strong>Dates: </strong>
                <span style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                  {preview.dates.length <= 10
                    ? preview.dates.join(', ')
                    : `${preview.dates.slice(0, 5).join(', ')} … ${preview.dates.slice(-3).join(', ')}`}
                </span>
              </div>
            )}
          </div>
        )}

        {preview && preview.nights > 0 && (
          <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.75rem' }}>
            <div className="field" style={{ marginBottom: '0.5rem', maxWidth: '32rem' }}>
              <label>
                <AlertTriangle size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: '0.25rem' }} />
                Type <code>{expectedConfirm}</code> to enable the delete button
              </label>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={expectedConfirm}
                style={{ fontFamily: 'monospace' }}
              />
            </div>
            <button
              type="button"
              className="btn-primary"
              disabled={!confirmMatches || deleting}
              onClick={handleDelete}
              style={{
                background: confirmMatches ? 'var(--ahi-bad, #dc2626)' : undefined,
                borderColor: confirmMatches ? 'var(--ahi-bad, #dc2626)' : undefined,
              }}
            >
              {deleting ? 'Deleting…' : 'Delete permanently'}
            </button>
          </div>
        )}

        {deleteResult && (
          <div style={{ marginTop: '0.75rem', padding: '0.75rem', background: 'var(--bg-secondary)', borderRadius: '6px', fontSize: '0.875rem' }}>
            <span className="status-pill good" style={{ marginRight: '0.5rem' }}>completed</span>
            Deleted {deleteResult.nights_deleted} night(s), {deleteResult.events_deleted.toLocaleString()} events,
            {' '}{deleteResult.timeseries_rows_deleted.toLocaleString()} waveform rows.
            {deleteResult.manual_logs_deleted > 0 && <> {deleteResult.manual_logs_deleted} manual log(s) also removed.</>}
            {deleteResult.db_size_before_mb !== null && deleteResult.db_size_after_mb !== null && (
              <> DB size: {deleteResult.db_size_before_mb.toFixed(1)} MB → {deleteResult.db_size_after_mb.toFixed(1)} MB.</>
            )}
          </div>
        )}
      </div>

      {/* --- Section B: Current database + checkpoint ----------------------- */}
      <div className="chart-card">
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Database size={16} />
          Current database
        </h2>

        {config && (
          <table className="data-table" style={{ marginBottom: '0.75rem', maxWidth: '32rem' }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Database file</td>
                <td><code>{config.api.db_path}</code></td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Size on disk</td>
                <td>{config.api.db_size_bytes !== null ? `${(config.api.db_size_bytes / (1024 * 1024)).toFixed(1)} MB` : '—'}</td>
              </tr>
            </tbody>
          </table>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button
            type="button"
            className="btn-secondary"
            disabled={checkpointing}
            onClick={handleCheckpoint}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <RefreshCw size={14} className={checkpointing ? 'spin' : undefined} />
            {checkpointing ? 'Running…' : 'Run CHECKPOINT'}
          </button>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
            Persists pending writes to the main DB file. Useful after a large delete.
          </span>
        </div>

        {checkpointResult && (
          <div style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            DB size before: {checkpointResult.db_size_before_mb?.toFixed(1) ?? '—'} MB · after: {checkpointResult.db_size_after_mb?.toFixed(1) ?? '—'} MB
          </div>
        )}

        <div style={{ marginTop: '0.75rem', fontSize: '0.8125rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
          Note: DuckDB's CHECKPOINT persists WAL into the main file. Disk space reclamation within the
          allocator happens over time as future inserts reuse freed blocks — the file may not shrink
          immediately after a delete.
        </div>
      </div>

      {/* --- Section C: Analytical cache (Phase 6 Ticket 6.1) ---------------- */}
      <div className="chart-card" style={{ marginTop: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <BarChart3 size={16} />
          Analytical cache
        </h2>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
          Cached results from <code>analyze_multivariate_correlation</code>,
          {' '}<code>analyze_lag_correlation</code>, and future analytical
          tools. Auto-invalidates when nightly summaries or manual logs
          change in the cached date range. Manual clearing is rarely
          necessary.
        </div>

        {cacheStats && (
          <table className="data-table" style={{ marginBottom: '0.5rem', maxWidth: '32rem' }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Entries</td>
                <td>{cacheStats.total_entries}</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Total cache hits</td>
                <td>{cacheStats.total_hits}</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Hit rate</td>
                <td>{(cacheStats.cache_hit_rate * 100).toFixed(1)}%</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Oldest entry age</td>
                <td>{cacheStats.oldest_entry_age_seconds}s</td>
              </tr>
              <tr>
                <td style={{ color: 'var(--text-secondary)' }}>Largest entry</td>
                <td>{(cacheStats.largest_entry_bytes / 1024).toFixed(1)} KB</td>
              </tr>
            </tbody>
          </table>
        )}

        {cacheStats && Object.keys(cacheStats.by_tool).length > 0 && (
          <details style={{ marginBottom: '0.625rem' }}>
            <summary style={{ cursor: 'pointer', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
              Breakdown by tool ({Object.keys(cacheStats.by_tool).length})
            </summary>
            <table className="data-table" style={{ marginTop: '0.5rem', maxWidth: '40rem' }}>
              <thead>
                <tr>
                  <th>Tool</th>
                  <th style={{ textAlign: 'right' }}>Entries</th>
                  <th style={{ textAlign: 'right' }}>Hits</th>
                  <th style={{ textAlign: 'right' }}>Avg compute (ms)</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(cacheStats.by_tool).map(([name, s]) => (
                  <tr key={name}>
                    <td><code>{name}</code></td>
                    <td style={{ textAlign: 'right' }}>{s.entries}</td>
                    <td style={{ textAlign: 'right' }}>{s.hits}</td>
                    <td style={{ textAlign: 'right' }}>{s.avg_compute_ms.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn-secondary"
            disabled={cacheLoading}
            onClick={refreshCacheStats}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <RefreshCw size={14} className={cacheLoading ? 'spin' : undefined} />
            Refresh stats
          </button>
          <input
            type="text"
            value={cacheClearConfirm}
            onChange={(e) => setCacheClearConfirm(e.target.value)}
            placeholder='Type CLEAR to enable clear button'
            style={{ fontSize: '0.8125rem', maxWidth: '14rem' }}
          />
          <button
            type="button"
            className="btn-secondary"
            disabled={cacheClearConfirm !== 'CLEAR' || cacheClearing}
            onClick={handleClearCache}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.375rem',
              color: cacheClearConfirm === 'CLEAR' ? 'var(--ahi-bad, #dc2626)' : undefined,
            }}
          >
            <Trash2 size={14} />
            {cacheClearing ? 'Clearing…' : 'Clear cache'}
          </button>
        </div>

        <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
          The cache auto-invalidates when underlying data changes — clearing
          is mostly useful for debugging or after a code change that affects
          how analytical methods compute.
        </div>
      </div>

      {toast && (
        <div
          style={{
            position: 'fixed', bottom: '1.5rem', right: '1.5rem',
            padding: '0.625rem 1rem', background: 'var(--bg-elevated, white)',
            border: '1px solid var(--border-color)', borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)', fontSize: '0.875rem',
            zIndex: 1000,
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
