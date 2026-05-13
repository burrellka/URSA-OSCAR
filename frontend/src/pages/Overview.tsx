import { useEffect, useState } from 'react';
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

  return (
    <div>
      <div className="page-header" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '0.125rem' }}>
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
