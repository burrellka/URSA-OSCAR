import { useState } from 'react';
import { api, ApiError } from '../api/client';
import type { ImportLogEntry, SkippedNight } from '../api/types';

export default function ImportPage() {
  const [path, setPath] = useState('/cpap-import');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ImportLogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.triggerImport(path);
      setResult(r);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`${e.message}${e.body ? ` — ${JSON.stringify(e.body)}` : ''}`);
      } else {
        setError(String(e));
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Import</h1>
      </div>

      <div className="chart-card" style={{ maxWidth: '720px' }}>
        <div className="field" style={{ marginBottom: '1rem' }}>
          <label>Source path (container-side)</label>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/cpap-import"
            disabled={running}
          />
          <span className="stat-sub" style={{ color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            Points at a DATALOG dir or SD-card root mounted into the API container. Default
            <code> /cpap-import </code> maps to the bind-mounted CPAP source on TrueNAS.
          </span>
        </div>

        <button className="btn-primary" onClick={go} disabled={running}>
          {running ? 'Importing…' : 'Start import'}
        </button>

        {error && (
          <div className="error-banner" style={{ marginTop: '1rem' }}>
            {error}
          </div>
        )}

        {result && (
          <div style={{ marginTop: '1rem' }}>
            <span
              className={`status-pill ${
                // Phase 3 Item 1C — tri-state visual mapping:
                // completed → green, partial → orange, failed → red.
                // pending / running retain the neutral 'warn' look used
                // by Phase 4's async-job surface.
                result.status === 'completed' ? 'good' :
                result.status === 'partial' ? 'warn' :
                result.status === 'failed' ? 'bad' :
                'warn'
              }`}
              style={{ marginRight: '0.5rem' }}
            >
              {result.status}
            </span>
            <strong>{result.nights_imported}</strong> night{result.nights_imported === 1 ? '' : 's'} imported
            {(result.nights_skipped ?? 0) > 0 && (
              <> · <strong style={{ color: 'var(--ahi-warn, #d97706)' }}>{result.nights_skipped} skipped</strong></>
            )}
            {result.earliest_date && result.latest_date && (
              <> · range <code>{result.earliest_date}</code> → <code>{result.latest_date}</code></>
            )}
            {result.error_message && (
              <div
                className="error-banner"
                style={{
                  marginTop: '0.75rem',
                  fontSize: '0.8125rem',
                  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {result.error_message}
              </div>
            )}
            {result.skipped && result.skipped.length > 0 && (
              <SkippedNightsTable skipped={result.skipped} />
            )}
            <div style={{ marginTop: '0.5rem' }}>
              <a href="/" className="btn-secondary" style={{ marginRight: '0.5rem' }}>
                See Overview
              </a>
              {result.latest_date && (
                <a href={`/daily/${result.latest_date}`} className="btn-secondary">
                  Open {result.latest_date}
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Per-night skip reasons surfaced under the import-result tile. Collapsible
 * — open by default when there are <= 10 skips, collapsed otherwise so we
 * don't blow up the page on a 60-night SD-card import.
 */
function SkippedNightsTable({ skipped }: { skipped: SkippedNight[] }) {
  const initiallyOpen = skipped.length <= 10;
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div
      style={{
        marginTop: '0.75rem',
        border: '1px solid var(--border-color)',
        borderRadius: '6px',
        background: 'var(--bg-secondary)',
        padding: '0.5rem 0.75rem',
        maxWidth: '100%',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'transparent',
          border: 0,
          padding: 0,
          font: 'inherit',
          cursor: 'pointer',
          color: 'var(--text-secondary)',
          marginBottom: open ? '0.5rem' : 0,
        }}
      >
        {open ? '▼' : '▶'}{' '}
        <strong>{skipped.length} night{skipped.length === 1 ? '' : 's'} skipped</strong>{' '}
        <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
          (click to {open ? 'hide' : 'expand'})
        </span>
      </button>
      {open && (
        <table className="data-table" style={{ fontSize: '0.8125rem' }}>
          <thead>
            <tr>
              <th style={{ width: '7rem' }}>Date</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {skipped.map((s) => (
              <tr key={s.date}>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{s.date}</td>
                <td style={{ color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{s.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
