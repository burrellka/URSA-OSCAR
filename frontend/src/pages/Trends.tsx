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
          <MultivariateSection
            metricOptions={allMetricOptions}
            earliestDate={earliestDate}
            latestDate={latestDate}
          />
          <LagSection
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


// =====================================================================
// Phase 6 Ticket 6.1 — Multivariate (partial) correlation section.
// =====================================================================


function MultivariateSection({ metricOptions, earliestDate, latestDate }: {
  metricOptions: string[]; earliestDate: string; latestDate: string;
}) {
  const [target, setTarget] = useState<string>(
    metricOptions.includes('total_ahi') ? 'total_ahi' : metricOptions[0],
  );
  const [predictors, setPredictors] = useState<string[]>(
    metricOptions.slice(0, 3).filter((m) => m !== 'total_ahi').slice(0, 2),
  );
  const [preset, setPreset] = useState<RangePreset>('90d');
  const { start, end } = useMemo(
    () => resolveRange(preset, earliestDate, latestDate),
    [preset, earliestDate, latestDate],
  );
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.multivariateCorrelation>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  function togglePredictor(metric: string) {
    if (predictors.includes(metric)) {
      setPredictors(predictors.filter((p) => p !== metric));
    } else if (predictors.length < 5) {
      setPredictors([...predictors, metric]);
    }
  }

  async function run() {
    if (!target || predictors.length < 2 || !start || !end) return;
    setLoading(true); setError(null);
    try {
      const r = await api.multivariateCorrelation({
        target_metric: target,
        predictor_metrics: predictors,
        start_date: start,
        end_date: end,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const data = result?.data;
  const refused = result && !result.ok;

  return (
    <div className="chart-card" style={{ marginTop: '1rem' }}>
      <h2 style={{ fontSize: '1.0625rem', fontWeight: 600, marginTop: 0, marginBottom: '0.25rem' }}>
        Multivariate analysis
      </h2>
      <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
        Partial correlation of each predictor with the target, controlling for
        the others. Answers "is X really driving Y, or is something else doing
        the work?"
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <div className="field">
          <label>Target metric</label>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Range preset</label>
          <select value={preset} onChange={(e) => setPreset(e.target.value as RangePreset)}>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>

      <div className="field" style={{ marginBottom: '0.75rem' }}>
        <label>
          Predictor metrics (pick 2-5)
          {' '}<span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
            — selected: {predictors.length}
          </span>
        </label>
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: '0.375rem',
          maxHeight: '7.5rem', overflowY: 'auto',
          padding: '0.5rem', border: '1px solid var(--border-color, #e5e7eb)', borderRadius: '6px',
        }}>
          {metricOptions
            .filter((m) => m !== target)
            .map((m) => {
              const selected = predictors.includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => togglePredictor(m)}
                  className={selected ? 'btn-primary' : 'btn-secondary'}
                  style={{
                    fontSize: '0.75rem', padding: '0.25rem 0.5rem',
                    fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                  }}
                  disabled={!selected && predictors.length >= 5}
                >
                  {m}
                </button>
              );
            })}
        </div>
      </div>

      <button
        type="button"
        className="btn-primary"
        onClick={run}
        disabled={loading || predictors.length < 2 || !target}
      >
        {loading ? 'Analyzing…' : 'Analyze'}
      </button>

      {error && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{error}</div>}

      {refused && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem 0.75rem',
                      background: 'var(--bg-secondary, #f3f4f6)', borderRadius: '6px',
                      fontSize: '0.8125rem' }}>
          <strong>Insufficient data:</strong> {data?.error}
        </div>
      )}

      {data && !refused && (
        <div style={{ marginTop: '0.75rem' }}>
          <table style={{
            width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem',
          }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary, #f3f4f6)' }}>
                <th style={{ padding: '0.4rem', textAlign: 'left' }}>Predictor</th>
                <th style={{ padding: '0.4rem', textAlign: 'right' }}>Partial r</th>
                <th style={{ padding: '0.4rem', textAlign: 'center' }}>95% CI</th>
                <th style={{ padding: '0.4rem', textAlign: 'right' }}>p-value</th>
                <th style={{ padding: '0.4rem', textAlign: 'left' }}>Interpretation</th>
              </tr>
            </thead>
            <tbody>
              {(data.predictors || []).map((p) => (
                <tr key={p.metric} style={{ borderTop: '1px solid var(--border-color, #e5e7eb)' }}>
                  <td style={{ padding: '0.4rem', fontFamily: 'var(--font-mono, ui-monospace, monospace)' }}>
                    {p.metric}
                  </td>
                  <td style={{ padding: '0.4rem', textAlign: 'right' }}>
                    {p.partial_r != null ? p.partial_r.toFixed(3) : '—'}
                  </td>
                  <td style={{ padding: '0.4rem', textAlign: 'center', fontSize: '0.75rem' }}>
                    {p.ci_95[0] != null && p.ci_95[1] != null
                      ? `[${p.ci_95[0]!.toFixed(2)}, ${p.ci_95[1]!.toFixed(2)}]`
                      : '—'}
                  </td>
                  <td style={{ padding: '0.4rem', textAlign: 'right' }}>
                    {p.p_value != null ? p.p_value.toFixed(3) : '—'}
                  </td>
                  <td style={{ padding: '0.4rem' }}>{p.interpretation}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{
            marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)',
          }}>
            Method: {data.method} · n = {data.n_observations} · Confidence:{' '}
            <strong>{data.confidence_level ?? '—'}</strong>
            {data.cache_age_seconds != null && data.cache_age_seconds > 0 && (
              <> · <em>cached {data.cache_age_seconds}s ago</em></>
            )}
          </div>
          {data.sample_caveat && (
            <div style={{ marginTop: '0.375rem', fontSize: '0.75rem', color: 'var(--ahi-warn, #d97706)' }}>
              {data.sample_caveat}
            </div>
          )}
          {data.multicollinear_pairs && data.multicollinear_pairs.length > 0 && (
            <div style={{ marginTop: '0.375rem', fontSize: '0.75rem', color: 'var(--ahi-warn, #d97706)' }}>
              <strong>Multicollinearity:</strong>{' '}
              {data.multicollinear_pairs.map((pair, i) => (
                <span key={i}>
                  {pair.metric_a} ↔ {pair.metric_b} (r={pair.r.toFixed(2)})
                  {i < data.multicollinear_pairs!.length - 1 ? '; ' : ''}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// =====================================================================
// Phase 6 Ticket 6.1 — Time-shifted lag correlation section.
// =====================================================================


function LagSection({ metricOptions, earliestDate, latestDate }: {
  metricOptions: string[]; earliestDate: string; latestDate: string;
}) {
  const [metricA, setMetricA] = useState<string>(metricOptions[0]);
  const [metricB, setMetricB] = useState<string>(
    metricOptions.includes('total_ahi') ? 'total_ahi' : metricOptions[1] || metricOptions[0],
  );
  const [preset, setPreset] = useState<RangePreset>('90d');
  const [lagLo, setLagLo] = useState<number>(-3);
  const [lagHi, setLagHi] = useState<number>(7);
  const { start, end } = useMemo(
    () => resolveRange(preset, earliestDate, latestDate),
    [preset, earliestDate, latestDate],
  );

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.lagCorrelation>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!metricA || !metricB || !start || !end) return;
    setLoading(true); setError(null);
    try {
      const r = await api.lagCorrelation({
        metric_a: metricA,
        metric_b: metricB,
        start_date: start,
        end_date: end,
        lag_range_days: [lagLo, lagHi],
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const data = result?.data;
  const refused = result && !result.ok;
  // Find the largest |r| for scaling the bar visualization.
  const maxAbsR = useMemo(() => {
    if (!data?.lag_correlations) return 1;
    const rs = data.lag_correlations
      .map((c) => c.r)
      .filter((v): v is number => v != null);
    return rs.length > 0 ? Math.max(...rs.map(Math.abs), 0.1) : 1;
  }, [data]);

  return (
    <div className="chart-card" style={{ marginTop: '1rem' }}>
      <h2 style={{ fontSize: '1.0625rem', fontWeight: 600, marginTop: 0, marginBottom: '0.25rem' }}>
        Lag analysis
      </h2>
      <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
        Cross-correlation function with bootstrap 95% CIs at each lag. Answers
        "how long after X happens does the effect on Y show up?"
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <div className="field">
          <label>Cause (metric A)</label>
          <select value={metricA} onChange={(e) => setMetricA(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Effect (metric B)</label>
          <select value={metricB} onChange={(e) => setMetricB(e.target.value)}>
            {metricOptions.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Range preset</label>
          <select value={preset} onChange={(e) => setPreset(e.target.value as RangePreset)}>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <div className="field">
          <label>Lag range — lower (days, neg = sanity check)</label>
          <input
            type="number"
            value={lagLo}
            min={-30} max={lagHi - 1}
            onChange={(e) => setLagLo(parseInt(e.target.value, 10) || 0)}
          />
        </div>
        <div className="field">
          <label>Lag range — upper (days)</label>
          <input
            type="number"
            value={lagHi}
            min={lagLo + 1} max={30}
            onChange={(e) => setLagHi(parseInt(e.target.value, 10) || 0)}
          />
        </div>
      </div>

      <button
        type="button"
        className="btn-primary"
        onClick={run}
        disabled={loading || !metricA || !metricB || metricA === metricB}
      >
        {loading ? 'Analyzing…' : 'Analyze'}
      </button>

      {error && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{error}</div>}

      {refused && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem 0.75rem',
                      background: 'var(--bg-secondary, #f3f4f6)', borderRadius: '6px',
                      fontSize: '0.8125rem' }}>
          <strong>Insufficient data:</strong> {data?.error}
        </div>
      )}

      {data && !refused && data.lag_correlations.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          {data.clinical_note && (
            <div style={{
              marginBottom: '0.5rem', padding: '0.5rem 0.75rem',
              background: 'var(--bg-secondary, #f3f4f6)', borderRadius: '6px',
              fontSize: '0.8125rem',
            }}>
              <strong>Peak:</strong> {data.clinical_note}
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary, #f3f4f6)' }}>
                <th style={{ padding: '0.4rem', textAlign: 'right' }}>Lag</th>
                <th style={{ padding: '0.4rem', textAlign: 'right' }}>r</th>
                <th style={{ padding: '0.4rem', textAlign: 'left' }}>Bar (relative)</th>
                <th style={{ padding: '0.4rem', textAlign: 'center' }}>95% CI</th>
                <th style={{ padding: '0.4rem', textAlign: 'right' }}>n</th>
              </tr>
            </thead>
            <tbody>
              {data.lag_correlations.map((c) => {
                const isPeak = c.lag_days === data.peak_lag_days;
                const widthPct = c.r != null ? Math.abs(c.r) / maxAbsR * 50 : 0;
                const barColor = c.r != null && c.r < 0 ? 'var(--ahi-bad, #dc2626)' : 'var(--accent-primary, #2563eb)';
                return (
                  <tr key={c.lag_days} style={{
                    borderTop: '1px solid var(--border-color, #e5e7eb)',
                    background: isPeak ? 'var(--bg-secondary, #f3f4f6)' : undefined,
                    fontWeight: isPeak ? 600 : undefined,
                  }}>
                    <td style={{ padding: '0.4rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {c.lag_days > 0 ? `+${c.lag_days}` : c.lag_days}d
                    </td>
                    <td style={{ padding: '0.4rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {c.r != null ? c.r.toFixed(3) : '—'}
                    </td>
                    <td style={{ padding: '0.4rem' }}>
                      {c.r != null && (
                        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                          <div style={{ width: '50%', borderRight: '1px solid var(--text-muted)', height: '0.625rem', position: 'relative' }}>
                            {c.r < 0 && (
                              <div style={{
                                position: 'absolute', right: 0, top: 0, height: '100%',
                                width: `${widthPct * 2}%`, background: barColor,
                              }} />
                            )}
                          </div>
                          <div style={{ width: '50%', height: '0.625rem', position: 'relative' }}>
                            {c.r >= 0 && (
                              <div style={{
                                position: 'absolute', left: 0, top: 0, height: '100%',
                                width: `${widthPct * 2}%`, background: barColor,
                              }} />
                            )}
                          </div>
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '0.4rem', textAlign: 'center', fontSize: '0.75rem' }}>
                      {c.ci_95[0] != null && c.ci_95[1] != null
                        ? `[${c.ci_95[0]!.toFixed(2)}, ${c.ci_95[1]!.toFixed(2)}]`
                        : '—'}
                    </td>
                    <td style={{ padding: '0.4rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {c.n_aligned}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Method: {data.method} · n = {data.n_observations} · Confidence:{' '}
            <strong>{data.confidence_level ?? '—'}</strong>
            {data.cache_age_seconds != null && data.cache_age_seconds > 0 && (
              <> · <em>cached {data.cache_age_seconds}s ago</em></>
            )}
          </div>
          {data.sample_caveat && (
            <div style={{ marginTop: '0.375rem', fontSize: '0.75rem', color: 'var(--ahi-warn, #d97706)' }}>
              {data.sample_caveat}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
