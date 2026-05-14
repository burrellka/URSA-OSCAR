import { useEffect, useRef, useState } from 'react';
import { Upload as UploadIcon } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { ImportJob, SkippedNight } from '../api/types';

export default function ImportPage() {
  const [path, setPath] = useState('/cpap-import');
  // `submitting` covers the time between the user clicking Start /
  // Choose folder and the API returning the enqueued job. Brief — the
  // POST itself is sub-second.
  const [submitting, setSubmitting] = useState(false);
  // 0.8.0 — `latestJob` is the job from the most recent submission in
  // this session. We poll it until it reaches a terminal state. Result
  // tile renders from latestJob.result_json once status='completed'.
  const [latestJob, setLatestJob] = useState<ImportJob | null>(null);
  const [activeJobs, setActiveJobs] = useState<ImportJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  // 0.6.3 — force re-parse toggle. Default OFF so re-uploading the same
  // SD card is cheap (importer skips known nights). User opts in only
  // when they actually want to re-process — typically after an importer
  // bug fix that changed how a metric is calculated.
  const [forceReimport, setForceReimport] = useState(false);

  // Phase 3 Item 2 — folder upload UI state.
  const [uploadProgress, setUploadProgress] = useState<{ sent: number; total: number } | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  // 0.8.0 polling loop — while there's any in-flight work (latestJob
  // hasn't reached terminal yet, OR there are active jobs from a
  // different session / tab / CLI), poll every 2s. Stop when nothing's
  // active.
  useEffect(() => {
    const shouldPoll =
      (latestJob && (latestJob.status === 'queued' || latestJob.status === 'running'))
      || activeJobs.length > 0;
    if (!shouldPoll) return;
    let cancelled = false;
    let timer: number | null = null;

    const tick = async () => {
      try {
        const fresh = await api.listImportJobs({ active_only: true });
        if (cancelled) return;
        setActiveJobs(fresh);
        // If we're tracking a specific job, refresh its state too. If
        // it's no longer active (finished while we were sleeping),
        // fetch the terminal row so the result tile populates.
        if (latestJob) {
          const stillActive = fresh.find((j) => j.id === latestJob.id);
          if (stillActive) {
            setLatestJob(stillActive);
          } else if (latestJob.status === 'queued' || latestJob.status === 'running') {
            const finished = await api.getImportJob(latestJob.id);
            if (!cancelled) setLatestJob(finished);
          }
        }
      } catch {
        // Polling failures are transient — just retry on the next tick.
      }
      if (!cancelled) timer = window.setTimeout(tick, 2000);
    };

    tick();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [latestJob, activeJobs.length]);

  async function go() {
    setSubmitting(true);
    setError(null);
    setLatestJob(null);
    try {
      const job = await api.triggerImport(path, forceReimport);
      setLatestJob(job);
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

  async function uploadFolder(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setSubmitting(true);
    setError(null);
    setLatestJob(null);
    setUploadProgress({ sent: 0, total: 0 });
    try {
      const job = await api.uploadFolder(
        Array.from(files),
        (sent, total) => setUploadProgress({ sent, total }),
        forceReimport,
      );
      setLatestJob(job);
    } catch (e2) {
      if (e2 instanceof ApiError) {
        setError(`${e2.message}${e2.body ? ` — ${JSON.stringify(e2.body)}` : ''}`);
      } else {
        setError(String(e2));
      }
    } finally {
      setSubmitting(false);
      setUploadProgress(null);
      // Reset input so the same folder can be re-picked.
      if (folderInputRef.current) folderInputRef.current.value = '';
    }
  }

  // Convenience derivations used to drive button/banner states.
  const inFlight = latestJob && (latestJob.status === 'queued' || latestJob.status === 'running');
  const running = submitting || !!inFlight;
  const result = latestJob?.result_json ?? null;

  // Active jobs we DIDN'T trigger from this page session — e.g., a CLI
  // import in another shell, or a Phase 4 Ticket 3 watcher-triggered
  // import. Surfaces here so the operator can see what's running.
  const otherActiveJobs = activeJobs.filter((j) => j.id !== latestJob?.id);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Import</h1>
      </div>

      {otherActiveJobs.length > 0 && (
        <div className="chart-card" style={{ maxWidth: '720px', marginBottom: '1rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginTop: 0, marginBottom: '0.5rem' }}>
            Active jobs
          </h2>
          <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
            Background imports currently running. Started from another tab,
            the CLI, or the watcher.
          </div>
          {otherActiveJobs.map((j) => (
            <div
              key={j.id}
              style={{
                display: 'flex', alignItems: 'baseline', gap: '0.5rem',
                padding: '0.4rem 0', borderTop: '1px solid var(--border-color)',
              }}
            >
              <span className="status-pill warn">{j.status}</span>
              <code style={{ fontSize: '0.8125rem' }}>#{j.id}</code>
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.8125rem' }}>
                {j.source_path
                  ? <>path <code>{j.source_path}</code></>
                  : <>folder upload</>}
              </span>
              {j.force_reimport && (
                <span style={{ fontSize: '0.75rem', color: 'var(--ahi-warn, #d97706)' }}>
                  force
                </span>
              )}
            </div>
          ))}
        </div>
      )}

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

        {/* 0.6.3 — Force re-import toggle. Applies to both the path-based
            import above and the folder-upload below. Default off, since
            most re-imports are "I plugged the card back in, ingest the
            new nights" and re-parsing the already-known ones is just
            wasted time. */}
        <div style={{ marginTop: '0.75rem' }}>
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8125rem', cursor: running ? 'not-allowed' : 'pointer', color: 'var(--text-secondary)' }}
          >
            <input
              type="checkbox"
              checked={forceReimport}
              onChange={(e) => setForceReimport(e.target.checked)}
              disabled={running}
            />
            Force re-parse nights already in the database
          </label>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: '1.25rem', marginTop: '0.125rem' }}>
            Leave unchecked for a fast incremental import. Check to re-process
            known nights (after an importer change or to refresh a
            specific date range).
          </div>
        </div>

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

        {/* 0.8.0 — in-flight state. The endpoint returns a queued job
            immediately; the worker takes over from there. This panel
            shows status until the job lands at completed/failed. */}
        {inFlight && latestJob && (
          <div
            style={{
              marginTop: '1rem',
              padding: '0.6rem 0.85rem',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              borderRadius: '6px',
              fontSize: '0.875rem',
              color: 'var(--text-secondary)',
            }}
          >
            <span
              className={`status-pill ${latestJob.status === 'running' ? 'warn' : 'warn'}`}
              style={{ marginRight: '0.5rem' }}
            >
              {latestJob.status}
            </span>
            Import job <code>#{latestJob.id}</code>
            {latestJob.status === 'queued' && ' — waiting for the worker to pick up…'}
            {latestJob.status === 'running' && ' — processing nights, parsing EDF…'}
          </div>
        )}

        {/* 0.8.0 — failed job surface. Worker caught an exception;
            the diagnostic lands in error_message rather than crashing. */}
        {latestJob && latestJob.status === 'failed' && (
          <div className="error-banner" style={{ marginTop: '1rem' }}>
            <strong>Import failed</strong>
            {latestJob.error_message && (
              <div style={{
                marginTop: '0.5rem',
                fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {latestJob.error_message}
              </div>
            )}
          </div>
        )}

        {/* 0.8.0 — orphaned: the API restarted while this job was in
            flight. Show it so the operator can re-trigger if needed. */}
        {latestJob && latestJob.status === 'orphaned' && (
          <div className="error-banner" style={{ marginTop: '1rem' }}>
            Import job was orphaned by an API restart. Re-trigger if the data
            isn't visible.
          </div>
        )}

        {result && latestJob?.status === 'completed' && (
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
            {(result.nights_skipped_existing ?? 0) > 0 && (
              <> · <span style={{ color: 'var(--text-secondary)' }}>{result.nights_skipped_existing} already known</span></>
            )}
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
