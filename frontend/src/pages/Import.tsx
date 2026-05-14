import { useRef, useState } from 'react';
import { Upload as UploadIcon } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { ImportLogEntry, SkippedNight } from '../api/types';

export default function ImportPage() {
  const [path, setPath] = useState('/cpap-import');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ImportLogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Phase 3 Item 2 — folder upload UI state.
  const [uploadProgress, setUploadProgress] = useState<{ sent: number; total: number } | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

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

  async function uploadFolder(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setUploadProgress({ sent: 0, total: 0 });
    try {
      const r = await api.uploadFolder(
        Array.from(files),
        (sent, total) => setUploadProgress({ sent, total }),
      );
      setResult(r);
    } catch (e2) {
      if (e2 instanceof ApiError) {
        setError(`${e2.message}${e2.body ? ` — ${JSON.stringify(e2.body)}` : ''}`);
      } else {
        setError(String(e2));
      }
    } finally {
      setRunning(false);
      setUploadProgress(null);
      // Reset input so the same folder can be re-picked.
      if (folderInputRef.current) folderInputRef.current.value = '';
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

        {/* Phase 3 Item 2 — folder upload alternative. */}
        <div style={{ marginTop: '1.25rem', paddingTop: '1rem', borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-muted)', fontSize: '0.8125rem', marginBottom: '0.5rem' }}>
            <span style={{ flex: 1, height: 1, background: 'var(--border-color)' }} />
            <span>OR</span>
            <span style={{ flex: 1, height: 1, background: 'var(--border-color)' }} />
          </div>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
            Upload SD card folder from this computer
            <br />
            <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
              Pick the SD card root or a DATALOG folder. The browser uploads it to the API container;
              the same importer runs against the uploaded copy. Only <code>.edf / .crc / .json / .jnl</code>
              files under 10 MB each are accepted.
            </span>
          </label>
          <input
            ref={folderInputRef}
            type="file"
            // @ts-expect-error — webkitdirectory + directory are non-standard but
            // widely supported (Chrome, Edge, Safari). Type defs don't include them.
            webkitdirectory=""
            directory=""
            multiple
            onChange={uploadFolder}
            disabled={running}
            style={{ display: 'none' }}
            id="ursa-folder-upload-input"
          />
          <button
            type="button"
            className="btn-secondary"
            onClick={() => folderInputRef.current?.click()}
            disabled={running}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <UploadIcon size={14} />
            {running && uploadProgress ? 'Uploading…' : 'Choose folder…'}
          </button>
          {uploadProgress && uploadProgress.total > 0 && (
            <div style={{ marginTop: '0.5rem', maxWidth: '24rem' }}>
              <div style={{
                height: '6px', background: 'var(--bg-secondary)',
                borderRadius: '3px', overflow: 'hidden',
              }}>
                <div style={{
                  width: `${(uploadProgress.sent / uploadProgress.total) * 100}%`,
                  height: '100%',
                  background: 'var(--accent-primary)',
                  transition: 'width 100ms linear',
                }} />
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem', fontVariantNumeric: 'tabular-nums' }}>
                {(uploadProgress.sent / 1024 / 1024).toFixed(1)} MB /
                {' '}{(uploadProgress.total / 1024 / 1024).toFixed(1)} MB
                {' '}({Math.round((uploadProgress.sent / uploadProgress.total) * 100)}%)
              </div>
            </div>
          )}
        </div>

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
