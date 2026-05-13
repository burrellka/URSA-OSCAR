import { useMemo } from 'react';

interface Props {
  /** Numeric values to bin. NaN/null skipped. */
  values: (number | null | undefined)[];
  /** Number of bins. Defaults to 10. */
  binCount?: number;
  /** Optional fixed lower / upper bounds. If not set, computed from data. */
  min?: number;
  max?: number;
  /** Color of the bars. Defaults to var(--accent-primary). */
  color?: string;
  /** Number of decimal places on the axis labels. */
  digits?: number;
  /** Inline width override. Default fills container. */
  width?: number | string;
  height?: number;
  /** Optional title rendered above the chart. */
  title?: string;
}

/**
 * Lightweight SVG histogram. No charting library — the bars + axis are
 * a few dozen rects + text elements. Suitable for Phase 2 stats where we
 * need 5-6 distributions side by side without uPlot's setup overhead.
 */
export default function Histogram({
  values,
  binCount = 10,
  min: minOverride,
  max: maxOverride,
  color = 'var(--accent-primary)',
  digits = 1,
  width = '100%',
  height = 160,
  title,
}: Props) {
  const { bins, axisMin, axisMax, maxCount } = useMemo(() => {
    const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    if (nums.length === 0) {
      return { bins: [], axisMin: 0, axisMax: 0, maxCount: 0 };
    }
    const dataMin = minOverride ?? Math.min(...nums);
    const dataMax = maxOverride ?? Math.max(...nums);
    const span = Math.max(dataMax - dataMin, 1e-6);
    const bucketWidth = span / binCount;
    const counts = new Array(binCount).fill(0);
    for (const n of nums) {
      let idx = Math.floor((n - dataMin) / bucketWidth);
      if (idx >= binCount) idx = binCount - 1;
      if (idx < 0) idx = 0;
      counts[idx]++;
    }
    const buckets = counts.map((c, i) => ({
      lo: dataMin + i * bucketWidth,
      hi: dataMin + (i + 1) * bucketWidth,
      count: c,
    }));
    return { bins: buckets, axisMin: dataMin, axisMax: dataMax, maxCount: Math.max(...counts, 1) };
  }, [values, binCount, minOverride, maxOverride]);

  if (bins.length === 0) {
    return (
      <div className="chart-card" style={{ width, height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)' }}>{title ? title + ' — ' : ''}no data</span>
      </div>
    );
  }

  const padding = { top: 14, right: 8, bottom: 22, left: 26 };
  const innerWidth = 480; // Drawn in SVG-units; element scales via viewBox.
  const innerHeight = height - padding.top - padding.bottom;
  const barWidth = (innerWidth - padding.left - padding.right) / bins.length;

  return (
    <div className="chart-card">
      {title && (
        <div style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.375rem' }}>{title}</div>
      )}
      <svg viewBox={`0 0 ${innerWidth} ${height}`} preserveAspectRatio="none" style={{ width, height, display: 'block' }}>
        {/* Y-axis count gridlines (rough — 0, mid, max) */}
        <line x1={padding.left} y1={padding.top + innerHeight} x2={innerWidth - padding.right} y2={padding.top + innerHeight} stroke="var(--chart-grid)" />
        <line x1={padding.left} y1={padding.top + innerHeight / 2} x2={innerWidth - padding.right} y2={padding.top + innerHeight / 2} stroke="var(--chart-grid)" strokeDasharray="2 4" />
        <text x={padding.left - 4} y={padding.top + 8} fontSize="10" textAnchor="end" fill="var(--text-muted)">{maxCount}</text>
        <text x={padding.left - 4} y={padding.top + innerHeight + 4} fontSize="10" textAnchor="end" fill="var(--text-muted)">0</text>

        {/* Bars */}
        {bins.map((b, i) => {
          const h = (b.count / maxCount) * innerHeight;
          const x = padding.left + i * barWidth + 1;
          const y = padding.top + innerHeight - h;
          return (
            <g key={i}>
              <rect x={x} y={y} width={Math.max(barWidth - 2, 1)} height={h} fill={color} opacity={0.85} />
              {b.count > 0 && (
                <title>{`${b.lo.toFixed(digits)}–${b.hi.toFixed(digits)} : ${b.count}`}</title>
              )}
            </g>
          );
        })}

        {/* X-axis labels (first, middle, last bin edges) */}
        <text x={padding.left} y={height - 6} fontSize="10" textAnchor="start" fill="var(--text-muted)">{axisMin.toFixed(digits)}</text>
        <text x={padding.left + (innerWidth - padding.left - padding.right) / 2} y={height - 6} fontSize="10" textAnchor="middle" fill="var(--text-muted)">{((axisMin + axisMax) / 2).toFixed(digits)}</text>
        <text x={innerWidth - padding.right} y={height - 6} fontSize="10" textAnchor="end" fill="var(--text-muted)">{axisMax.toFixed(digits)}</text>
      </svg>
    </div>
  );
}
