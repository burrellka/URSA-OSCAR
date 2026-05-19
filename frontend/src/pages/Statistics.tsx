import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { NightlySummary } from '../api/types';
import { formatAhi } from '../lib/format';
import Histogram from '../components/Histogram';

type Window = '7d' | '30d' | '90d' | 'all';

export default function Statistics() {
  const [nights, setNights] = useState<NightlySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [windowKey, setWindowKey] = useState<Window>('all');

  useEffect(() => {
    let cancelled = false;
    api.listNights()
      .then((rows) => { if (!cancelled) setNights(rows); })
      .catch((e: ApiError) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => filterByWindow(nights, windowKey), [nights, windowKey]);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Statistics</h1>
        <div style={{ display: 'flex', gap: '0.375rem' }}>
          {(['7d', '30d', '90d', 'all'] as Window[]).map((w) => (
            <button
              key={w}
              className={windowKey === w ? 'btn-primary' : 'btn-secondary'}
              onClick={() => setWindowKey(w)}
              style={{ padding: '0.375rem 0.75rem', fontSize: '0.8125rem' }}
            >
              {w === 'all' ? 'All' : w}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="loading">Loading nights…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && filtered.length === 0 && (
        <div className="empty-state">No nights in this window.</div>
      )}

      {!loading && !error && filtered.length > 0 && (
        <>
          <UsageBreakdown windowKey={windowKey} usedNights={filtered.length} />
          <AggregateTable nights={filtered} />
          <div className="stat-grid" style={{ marginTop: '1.5rem' }}>
            <Histogram title="Nightly AHI" values={filtered.map((n) => n.total_ahi)} digits={1} color="var(--accent-primary)" />
            <Histogram title="95% Pressure (cmH₂O)" values={filtered.map((n) => n.p95_pressure)} digits={1} color="var(--event-rera)" />
            <Histogram title="Mask-on (minutes)" values={filtered.map((n) => n.total_time_minutes)} digits={0} color="var(--tier-primary)" />
            <Histogram title="Central AHI" values={filtered.map((n) => n.central_ahi)} digits={1} color="var(--event-ca)" />
            <Histogram title="Obstructive AHI" values={filtered.map((n) => n.obstructive_ahi)} digits={1} color="var(--event-oa)" />
            <Histogram title="Leak — large-leak %" values={filtered.map((n) => n.large_leak_pct)} digits={1} color="var(--event-leak)" />
          </div>
        </>
      )}
    </div>
  );
}

function filterByWindow(nights: NightlySummary[], w: Window): NightlySummary[] {
  if (w === 'all') return nights;
  const days = w === '7d' ? 7 : w === '30d' ? 30 : 90;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days + 1);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  return nights.filter((n) => n.date >= cutoffIso);
}

// 0.13.4 — surface operator compliance alongside clinical metrics.
// For fixed windows (7/30/90 days) we can compute "used vs skipped"
// precisely from the calendar range. For the 'all' window the
// denominator is ambiguous (earliest data → today, but earliest may
// pre-date when the operator actually started using URSA-OSCAR) so we
// just show the night count without a usage percentage.
function UsageBreakdown({
  windowKey, usedNights,
}: { windowKey: Window; usedNights: number }) {
  if (windowKey === 'all') {
    return (
      <div className="chart-card" style={usageCardStyle}>
        <span style={usageMainStyle}>{usedNights}</span>
        <span style={usageLabelStyle}>
          night{usedNights === 1 ? '' : 's'} with therapy data
        </span>
      </div>
    );
  }
  const windowDays = windowKey === '7d' ? 7 : windowKey === '30d' ? 30 : 90;
  const skipped = Math.max(0, windowDays - usedNights);
  const usagePct = Math.round((usedNights / windowDays) * 1000) / 10;
  return (
    <div className="chart-card" style={usageCardStyle}>
      <span style={usageMainStyle}>{usedNights} used</span>
      <span style={usageDividerStyle}>/</span>
      <span style={usageSkippedStyle}>{skipped} skipped</span>
      <span style={usageDividerStyle}>·</span>
      <span style={usagePctStyle}>{usagePct}% usage</span>
      <span style={usageWindowStyle}>over the last {windowDays} days</span>
    </div>
  );
}

const usageCardStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.5rem',
  flexWrap: 'wrap',
  marginBottom: '1rem',
};
const usageMainStyle: React.CSSProperties = {
  fontSize: '1.125rem', fontWeight: 600, color: 'var(--text-primary)',
};
const usageSkippedStyle: React.CSSProperties = {
  fontSize: '1.125rem', fontWeight: 500, color: 'var(--text-secondary)',
};
const usagePctStyle: React.CSSProperties = {
  fontSize: '1.125rem', fontWeight: 600, color: 'var(--accent-text)',
};
const usageDividerStyle: React.CSSProperties = {
  color: 'var(--text-muted)', fontSize: '1rem',
};
const usageLabelStyle: React.CSSProperties = {
  fontSize: '0.875rem', color: 'var(--text-secondary)',
};
const usageWindowStyle: React.CSSProperties = {
  fontSize: '0.875rem', color: 'var(--text-muted)', marginLeft: '0.5rem',
};


function AggregateTable({ nights }: { nights: NightlySummary[] }) {
  const rows: Array<{ label: string; pick: (n: NightlySummary) => number | null | undefined; digits?: number }> = [
    { label: 'AHI', pick: (n) => n.total_ahi, digits: 2 },
    { label: 'Obstructive AHI', pick: (n) => n.obstructive_ahi, digits: 2 },
    { label: 'Central AHI', pick: (n) => n.central_ahi, digits: 2 },
    { label: 'Hypopnea index', pick: (n) => n.hypopnea_index, digits: 2 },
    { label: 'Median pressure (cmH₂O)', pick: (n) => n.median_pressure, digits: 2 },
    { label: '95% pressure (cmH₂O)', pick: (n) => n.p95_pressure, digits: 2 },
    { label: '95% leak (L/min)', pick: (n) => n.p95_leak, digits: 1 },
    { label: 'Mask-on (minutes)', pick: (n) => n.total_time_minutes, digits: 0 },
  ];

  return (
    <div className="chart-card" style={{ overflowX: 'auto' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.875rem' }}>
        Aggregates · {nights.length} night{nights.length === 1 ? '' : 's'}
      </h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th style={{ textAlign: 'right' }}>Mean</th>
            <th style={{ textAlign: 'right' }}>Median</th>
            <th style={{ textAlign: 'right' }}>Min</th>
            <th style={{ textAlign: 'right' }}>Max</th>
            <th style={{ textAlign: 'right' }}>Std dev</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, pick, digits = 2 }) => {
            const vals = nights.map(pick).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
            if (vals.length === 0) return (<tr key={label}><td>{label}</td><td colSpan={5} style={{ color: 'var(--text-muted)', textAlign: 'right' }}>—</td></tr>);
            const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
            const sorted = [...vals].sort((a, b) => a - b);
            const median = sorted[Math.floor(sorted.length / 2)];
            const min = sorted[0];
            const max = sorted[sorted.length - 1];
            const variance = vals.reduce((s, v) => s + (v - mean) ** 2, 0) / vals.length;
            const stdev = Math.sqrt(variance);
            return (
              <tr key={label}>
                <td>{label}</td>
                <td style={{ textAlign: 'right' }}>{formatAhi(mean)}</td>
                <td style={{ textAlign: 'right' }}>{formatAhi(median)}</td>
                <td style={{ textAlign: 'right' }}>{min.toFixed(digits)}</td>
                <td style={{ textAlign: 'right' }}>{max.toFixed(digits)}</td>
                <td style={{ textAlign: 'right' }}>{stdev.toFixed(digits)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
