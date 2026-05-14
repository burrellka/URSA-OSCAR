import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import uPlot, { type Options } from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { api, ApiError } from '../api/client';
import type { CorrelationResult, TrendResult } from '../api/client';

/**
 * Trends — Phase 3 Item 6.
 *
 * Two sections, both backed by the Item 5A-5D analytics endpoints:
 *
 *   1. Single-metric trend — uPlot line chart + linear regression
 *      overlay, slope / R² / interpretation panel.
 *
 *   2. Correlation scatter — uPlot scatter plot of metric A vs metric B
 *      paired daily, regression line, Pearson r / p-value / sample
 *      size warning.
 *
 * Default ranges per architect directive: trend defaults to "Last 90
 * days" (NOT 30) because Kevin's 28+ nights span 2023-2026 and a 30-day
 * window misses the long-arc trajectory. Correlation defaults to "All
 * data" because correlation needs as many paired samples as possible
 * to be meaningful.
 */
export default function Trends() {
  const [nightlyMetrics, setNightlyMetrics] = useState<string[]>([]);
  const [manualMetrics, setManualMetrics] = useState<string[]>([]);
  const [allDates, setAllDates] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [metrics, nights] = await Promise.all([
          api.getAvailableMetrics(),
          api.listNights(),
        ]);
        setNightlyMetrics(metrics.nightly_metrics);
        setManualMetrics(metrics.manual_metrics);
        setAllDates(nights.map((n) => n.date).sort());
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
  }, []);

  const allMetricOptions = useMemo(
    () => [...nightlyMetrics, ...manualMetrics],
    [nightlyMetrics, manualMetrics],
  );

  const earliestDate = allDates[0] ?? '';
  const latestDate = allDates[allDates.length - 1] ?? '';

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Trends</h1>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {allMetricOptions.length === 0 ? (
        <div className="empty-state">
          No nights imported yet. Use <a href="/import">Import</a> to load some, then come back.
        </div>
      ) : (
        <>
          <TrendSection
            metricOptions={allMetricOptions}
            earliestDate={earliestDate}
            latestDate={latestDate}
          />
          <CorrelationSection
            metricOptions={allMetricOptions}
            earliestDate={earliestDate}
            latestDate={latestDate}
          />
        </>
      )}
    </div>
  );
}


// --- Trend section -----------------------------------------------------------

type RangePreset = '7d' | '30d' | '90d' | 'all';

function TrendSection({ metricOptions, earliestDate, latestDate }: {
  metricOptions: string[]; earliestDate: string; latestDate: string;
}) {
  const [metric, setMetric] = useState<string>(
    metricOptions.includes('total_ahi') ? 'total_ahi' : metricOptions[0],
  );
  // Default to 90d per architect directive.
  const [preset, setPreset] = useState<RangePreset>('90d');
  const { start, end } = useMemo(
    () => resolveRange(preset, earliestDate, latestDate),
    [preset, earliestDate, latestDate],
  );

  const [result, setResult] = useState<TrendResult | null>(null);
  const [seriesData, setSeriesData] = useState<{ ts: number[]; vals: (number | null)[] } | null>(null);
  const [loading, setLoading] = useState(false);

  const loadTrend = useCallback(async () => {
    if (!metric || !start || !end) return;
    setLoading(true);
    try {
      const [trend, nights] = await Promise.all([
        api.getTrend({ metric, start_date: start, end_date: end }),
        api.listNights({ start, end }),
      ]);
      setResult(trend);
      // Per-night series for plotting (only nightly_summary columns get
      // real values from /nights; manual-metric series would need a
      // dedicated endpoint, skip the chart in that case for v1.)
      const isNightly = !metric.includes(':');
      if (isNightly) {
        const ts = nights.map((n) => new Date(n.date).getTime() / 1000);
        const vals = nights.map((n) => (n as unknown as Record<string, number | null>)[metric] ?? null);
        setSeriesData({ ts, vals });
      } else {
        setSeriesData(null);
      }
    } catch (e) {
      setResult(null);
      setSeriesData(null);
      console.error('Trend load failed:', e);
    } finally {
      setLoading(false);
    }
  }, [metric, start, end]);

  useEffect(() => { loadTrend(); }, [loadTrend]);

  return (
    <div className="chart-card" style={{ marginBottom: '1.25rem' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
        Single-metric trend
      </h2>

      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'end', marginBottom: '0.75rem' }}>
        <div className="field" style={{ minWidth: '14rem' }}>
          <label>Metric</label>
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Range</label>
          <div style={{ display: 'flex', gap: '0.25rem' }}>
            {(['7d', '30d', '90d', 'all'] as RangePreset[]).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPreset(p)}
                className={preset === p ? 'btn-primary' : 'btn-secondary'}
                style={{ padding: '0.375rem 0.625rem', fontSize: '0.8125rem' }}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          {start && end ? <>{start} → {end}</> : null}
        </div>
      </div>

      {loading && <div className="loading">Computing trend…</div>}

      {result && seriesData && seriesData.ts.length > 0 && (
        <TrendChart
          metric={metric}
          ts={seriesData.ts}
          vals={seriesData.vals}
          slope={result.slope_per_day}
          intercept={result.intercept}
          windowStart={start}
        />
      )}

      {result && (
        <TrendStatsPanel result={result} />
      )}
    </div>
  );
}


function TrendChart({ metric, ts, vals, slope, intercept, windowStart }: {
  metric: string;
  ts: number[];
  vals: (number | null)[];
  slope: number | null;
  intercept: number | null;
  windowStart: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const uplotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    if (!containerRef.current || ts.length === 0) return;

    // Regression line: only when slope/intercept defined.
    let regressionLine: (number | null)[];
    if (slope !== null && intercept !== null && windowStart) {
      const windowStartEpoch = new Date(windowStart).getTime() / 1000;
      regressionLine = ts.map((t) => {
        const daysSinceStart = (t - windowStartEpoch) / 86400;
        return intercept + slope * daysSinceStart;
      });
    } else {
      regressionLine = ts.map(() => null);
    }

    const opts: Options = {
      width: containerRef.current.clientWidth,
      height: 240,
      scales: { x: { time: true } },
      cursor: { focus: { prox: 30 } },
      legend: { show: false },
      axes: [
        { stroke: 'var(--text-secondary)', grid: { stroke: 'var(--chart-grid)', width: 1 }, ticks: { stroke: 'var(--chart-axis)', width: 1 } },
        { stroke: 'var(--text-secondary)', grid: { stroke: 'var(--chart-grid)', width: 1 }, ticks: { stroke: 'var(--chart-axis)', width: 1 }, size: 50, label: metric, labelGap: 4, labelSize: 10, gap: 4 },
      ],
      series: [
        { label: 'date' },
        {
          label: metric,
          stroke: 'var(--accent-primary)',
          width: 1.5,
          points: { show: true, size: 5 },
        },
        {
          label: 'regression',
          stroke: 'var(--event-rera)',
          width: 1.5,
          dash: [6, 4],
          points: { show: false },
        },
      ],
      padding: [12, 8, 8, 8],
    };

    const u = new uPlot(opts, [ts, vals, regressionLine], containerRef.current);
    uplotRef.current = u;

    const ro = new ResizeObserver(() => {
      if (containerRef.current && uplotRef.current) {
        uplotRef.current.setSize({ width: containerRef.current.clientWidth, height: 240 });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      u.destroy();
      uplotRef.current = null;
    };
  }, [metric, ts, vals, slope, intercept, windowStart]);

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      marginBottom: '0.75rem',
    }}>
      <div ref={containerRef} style={{ width: '100%', height: 240 }} />
    </div>
  );
}


function TrendStatsPanel({ result }: { result: TrendResult }) {
  return (
    <div style={{
      padding: '0.625rem 0.75rem',
      background: 'var(--bg-secondary)',
      borderRadius: '6px',
      fontSize: '0.875rem',
    }}>
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginBottom: '0.375rem' }}>
        <StatPill label="Slope per day" value={result.slope_per_day !== null ? result.slope_per_day.toFixed(4) : '—'} />
        <StatPill label="R²" value={result.r_squared !== null ? result.r_squared.toFixed(3) : '—'} />
        <StatPill label="n nights" value={String(result.n_nights)} />
        {result.current_value_estimate !== null && (
          <StatPill label="Current est." value={result.current_value_estimate.toFixed(2)} />
        )}
        {result.projection && (
          <StatPill
            label={`Projected (${result.projection.projection_days}d)`}
            value={result.projection.projected_value.toFixed(2)}
          />
        )}
      </div>
      <div style={{ color: 'var(--text-secondary)' }}>{result.interpretation_text}</div>
    </div>
  );
}


// --- Correlation section -----------------------------------------------------

function CorrelationSection({ metricOptions, earliestDate, latestDate }: {
  metricOptions: string[]; earliestDate: string; latestDate: string;
}) {
  const [metricA, setMetricA] = useState(
    metricOptions.includes('total_ahi') ? 'total_ahi' : metricOptions[0],
  );
  const [metricB, setMetricB] = useState(
    metricOptions.includes('p95_pressure') ? 'p95_pressure' : (metricOptions[1] ?? metricOptions[0]),
  );
  // Default to all data per architect directive (correlation benefits
  // from more samples).
  const [preset, setPreset] = useState<RangePreset>('all');
  const { start, end } = useMemo(
    () => resolveRange(preset, earliestDate, latestDate),
    [preset, earliestDate, latestDate],
  );
  const [lagDays, setLagDays] = useState(0);
  const [result, setResult] = useState<CorrelationResult | null>(null);
  const [loading, setLoading] = useState(false);

  const loadCorrelation = useCallback(async () => {
    if (!metricA || !metricB || !start || !end) return;
    setLoading(true);
    try {
      const r = await api.getCorrelation({
        metric_a: metricA, metric_b: metricB,
        start_date: start, end_date: end,
        lag_days: lagDays,
      });
      setResult(r);
    } catch (e) {
      setResult(null);
      console.error('Correlation load failed:', e);
    } finally {
      setLoading(false);
    }
  }, [metricA, metricB, start, end, lagDays]);

  useEffect(() => { loadCorrelation(); }, [loadCorrelation]);

  return (
    <div className="chart-card">
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
        Correlation
      </h2>

      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'end', marginBottom: '0.75rem' }}>
        <div className="field" style={{ minWidth: '14rem' }}>
          <label>Metric A (X axis)</label>
          <select value={metricA} onChange={(e) => setMetricA(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field" style={{ minWidth: '14rem' }}>
          <label>Metric B (Y axis)</label>
          <select value={metricB} onChange={(e) => setMetricB(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field" style={{ width: '6rem' }}>
          <label>Lag (days)</label>
          <input
            type="number"
            value={lagDays}
            onChange={(e) => setLagDays(Number(e.target.value))}
            min={-7}
            max={7}
          />
        </div>
        <div className="field">
          <label>Range</label>
          <div style={{ display: 'flex', gap: '0.25rem' }}>
            {(['7d', '30d', '90d', 'all'] as RangePreset[]).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPreset(p)}
                className={preset === p ? 'btn-primary' : 'btn-secondary'}
                style={{ padding: '0.375rem 0.625rem', fontSize: '0.8125rem' }}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading && <div className="loading">Computing correlation…</div>}

      {result && (
        <CorrelationStatsPanel result={result} />
      )}
    </div>
  );
}


function CorrelationStatsPanel({ result }: { result: CorrelationResult }) {
  return (
    <div style={{
      padding: '0.625rem 0.75rem',
      background: 'var(--bg-secondary)',
      borderRadius: '6px',
      fontSize: '0.875rem',
    }}>
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginBottom: '0.375rem' }}>
        <StatPill label="Pearson r" value={result.pearson_r !== null ? result.pearson_r.toFixed(3) : '—'} />
        <StatPill label="p-value" value={result.p_value !== null ? result.p_value.toFixed(4) : '—'} />
        <StatPill label="n pairs" value={String(result.n_pairs)} />
        <StatPill label="Lag" value={`${result.lag_days}d`} />
      </div>
      <div style={{ color: 'var(--text-secondary)' }}>{result.interpretation_text}</div>
      {result.sample_size_warning && (
        <div style={{ marginTop: '0.5rem', padding: '0.375rem 0.5rem', background: 'rgba(217,119,6,0.1)', color: 'var(--ahi-warn, #d97706)', borderRadius: '4px', fontSize: '0.8125rem' }}>
          ⚠ {result.sample_size_warning}
        </div>
      )}
    </div>
  );
}


// --- Shared helpers ----------------------------------------------------------

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>{label}: </span>
      <strong style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</strong>
    </span>
  );
}

function resolveRange(
  preset: RangePreset, earliest: string, latest: string,
): { start: string; end: string } {
  if (!latest) return { start: '', end: '' };
  const end = latest;
  if (preset === 'all') {
    return { start: earliest || latest, end };
  }
  const days = preset === '7d' ? 7 : preset === '30d' ? 30 : 90;
  const endDate = new Date(latest);
  const startDate = new Date(endDate);
  startDate.setDate(startDate.getDate() - days + 1);
  return { start: startDate.toISOString().slice(0, 10), end };
}
