import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { NightlySummary } from '../api/types';
import { formatAhi, formatMinutesAsHM } from '../lib/format';
import CalendarHeatmap from '../components/CalendarHeatmap';

export default function Overview() {
  const [nights, setNights] = useState<NightlySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.listNights()
      .then((rows) => { if (!cancelled) setNights(rows); })
      .catch((e: ApiError) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const sortedDesc = [...nights].sort((a, b) => (a.date < b.date ? 1 : -1));
  const latest = sortedDesc[0];
  const week = sortedDesc.slice(0, 7);
  const avgAhi = week.length > 0
    ? week.reduce((sum, n) => sum + (n.total_ahi ?? 0), 0) / week.length
    : null;
  const totalSessions = nights.reduce((sum, n) => sum + (n.session_count ?? 0), 0);
  const totalMinutes = nights.reduce((sum, n) => sum + (n.total_time_minutes ?? 0), 0);

  const earliest = sortedDesc.length > 0 ? sortedDesc[sortedDesc.length - 1].date : '';
  const latestDate = sortedDesc.length > 0 ? sortedDesc[0].date : '';
  const [showExportModal, setShowExportModal] = useState(false);

  return (
    <div>
      <div className="page-header" style={{ alignItems: 'flex-start' }}>
        <div style={{ flexDirection: 'column', display: 'flex', gap: '0.125rem' }}>
          <h1 className="page-title" style={{ marginBottom: 0 }}>URSA-OSCAR</h1>
          <div
            style={{
              fontSize: '0.875rem',
              color: 'var(--text-muted)',
              fontWeight: 400,
            }}
          >
            Unified Rest &amp; Somatic Analytics — built on OSCAR&rsquo;s analytics core
          </div>
        </div>
        {nights.length > 0 && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowExportModal(true)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <Download size={14} />
            Export range
          </button>
        )}
      </div>

      {showExportModal && (
        <ExportRangeModal
          earliest={earliest}
          latest={latestDate}
          onClose={() => setShowExportModal(false)}
        />
      )}

      {loading && <div className="loading">Loading nights…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && (
        <>
          <div className="stat-grid" style={{ marginBottom: '1.5rem' }}>
            <StatTile
              label="Nights with data"
              value={nights.length.toString()}
              sub={
                nights.length > 0
                  ? `${sortedDesc[sortedDesc.length - 1].date} → ${sortedDesc[0].date}`
                  : 'no imports yet'
              }
            />
            <StatTile
              label="Latest AHI"
              value={latest ? formatAhi(latest.total_ahi) : '—'}
              sub={latest ? latest.date : ''}
            />
            <StatTile
              label="7-night avg AHI"
              value={avgAhi !== null ? formatAhi(avgAhi) : '—'}
              sub={`${week.length} night${week.length === 1 ? '' : 's'}`}
            />
            <StatTile
              label="Total mask-on"
              value={formatMinutesAsHM(totalMinutes)}
              sub={`${totalSessions} sessions across ${nights.length} nights`}
            />
          </div>

          {nights.length === 0 ? (
            <div className="empty-state">
              No CPAP data yet. Head to <a href="/import">Import</a> to load nights.
            </div>
          ) : (
            <>
              <div style={{ marginBottom: '1.5rem' }}>
                <CalendarHeatmap nights={sortedDesc} days={90} />
              </div>
              <RecentNightsTable nights={sortedDesc} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function ExportRangeModal({ earliest, latest, onClose }: {
  earliest: string; latest: string; onClose: () => void;
}) {
  const [start, setStart] = useState(earliest);
  const [end, setEnd] = useState(latest);

  function downloadCsv() {
    if (!start || !end) return;
    // Direct navigation — browser triggers download via Content-Disposition.
    window.location.href = api.bulkExportUrl(start, end);
    onClose();
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
        zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: '8px', padding: '1.5rem',
          width: 'min(28rem, 92vw)', boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
        }}
      >
        <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginTop: 0, marginBottom: '0.75rem' }}>
          Export range to CSV
        </h2>
        <p style={{ marginBottom: '0.75rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          One row per night, OSCAR-compatible column shape. Downloads directly to your browser.
        </p>
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem' }}>
          <div className="field" style={{ flex: 1 }}>
            <label>Start date</label>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>End date</label>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn-primary"
            onClick={downloadCsv}
            disabled={!start || !end || start > end}
          >
            Download CSV
          </button>
        </div>
      </div>
    </div>
  );
}


function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function RecentNightsTable({ nights }: { nights: NightlySummary[] }) {
  return (
    <div className="chart-card" style={{ overflowX: 'auto' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.875rem' }}>
        Recent nights
      </h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>AHI</th>
            <th>Sessions</th>
            <th>Mask-on</th>
            <th>Median pressure</th>
            <th>95% pressure</th>
            <th>Mode</th>
          </tr>
        </thead>
        <tbody>
          {nights.slice(0, 30).map((n) => (
            <tr key={n.date}>
              <td><a href={`/daily/${n.date}`}>{n.date}</a></td>
              <td><AhiPill ahi={n.total_ahi} /></td>
              <td>{n.session_count ?? '—'}</td>
              <td>{formatMinutesAsHM(n.total_time_minutes)}</td>
              <td>{formatAhi(n.median_pressure)}</td>
              <td>{formatAhi(n.p95_pressure)}</td>
              <td>{n.mode ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AhiPill({ ahi }: { ahi: number | null }) {
  if (ahi === null) return <span className="status-pill">—</span>;
  let cls = 'good';
  if (ahi > 15) cls = 'bad';
  else if (ahi > 5) cls = 'warn';
  return <span className={`status-pill ${cls}`}>{formatAhi(ahi)}</span>;
}
