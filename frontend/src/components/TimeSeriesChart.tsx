import { useEffect, useRef } from 'react';
import uPlot, { type Options } from 'uplot';
import 'uplot/dist/uPlot.min.css';

interface Series {
  label: string;
  /** Values aligned 1:1 with `timestamps`. */
  values: (number | null)[];
  /** Stroke color (CSS var or hex). */
  stroke?: string;
  /** Width in pixels. Default 1.25. */
  width?: number;
  /** If true, fill area under the line at 15% opacity. */
  fill?: boolean;
  /** Decimals shown in the live-value readout. Default 2. */
  decimals?: number;
}

interface Props {
  /** Epoch-seconds Float64 array, sorted ascending. */
  timestamps: number[];
  series: Series[];
  height?: number;
  /** Y-axis label / units (e.g. "cmH₂O"). Also rendered in the per-series header. */
  unit?: string;
  /** Imperative ref to receive the uPlot instance for orchestrating
   *  synchronized zoom/pan across stacked charts. */
  onCreate?: (u: uPlot) => void;
  /** Synchronization key — all charts with the same key share zoom + cursor. */
  syncKey?: string;
  /** Track label rendered inline at top-left (e.g. "Pressure"). When multiple
   *  series are present the live-value readouts appear next to this label. */
  title?: string;
  /** Explicit X-axis bounds (epoch seconds). Pin all stacked charts to the
   *  same window so cursor sync receivers don't re-evaluate scales when the
   *  cursor crosses charts with slightly different timestamp arrays — that
   *  recompute is what was wiping uPlot's axis labels on hover (0.7.1 bug). */
  xMin?: number;
  xMax?: number;
}

/**
 * Thin uPlot wrapper sized to fill its container width. Supports cursor +
 * scale sync across stacked instances via the `syncKey` prop.
 *
 * **Hover readouts (Phase 2 polish, Item 1):** the header above the plot
 * shows each series' live value at the cursor position. Driven by uPlot's
 * `setCursor` hook, which fires on every mouseMove. To avoid re-rendering
 * the React tree at 60fps, the readouts are span refs that the hook
 * mutates directly via `textContent`. On mouse-leave (`idx == null`) the
 * readouts clear, reverting the header to just `Label`.
 *
 * Bound to a parent ResizeObserver so the chart resizes on window changes.
 */
export default function TimeSeriesChart({
  timestamps,
  series,
  height = 130,
  unit,
  onCreate,
  syncKey,
  title,
  xMin,
  xMax,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const uplotRef = useRef<uPlot | null>(null);
  // One readout span per series (in `series` order). Mutated directly by the
  // setCursor hook below; never touches React state.
  const readoutRefs = useRef<Array<HTMLSpanElement | null>>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    if (timestamps.length === 0) return;

    const data: uPlot.AlignedData = [
      timestamps,
      ...series.map((s) => s.values),
    ];

    const opts: Options = {
      width: containerRef.current.clientWidth,
      height,
      cursor: {
        // 0.7.2 axis-label fix — the Pressure chart (2 series:
        // Pressure + EPAP) syncing to single-series charts via uPlot's
        // cursor.sync caused the receiving charts' axis labels to
        // vanish until page refresh. Two root causes, addressed
        // together so the fix is durable:
        //   1) `setSeries: true` broadcast a focused-series-index
        //      that didn't exist on receiver charts.
        //   2) Each chart auto-scaled its own X from its own
        //      timestamp array, so the cursor.sync receiver
        //      re-evaluated its X scale on every cursor move —
        //      that recompute is what wiped axis labels.
        //   3) `focus.prox` enabled proximity-based series focus
        //      dimming — another vector that touches series state
        //      on hover. Disabling it eliminates any path where
        //      hover changes the receiver's series-render state.
        sync: syncKey ? { key: syncKey } : undefined,
      },
      scales: {
        // Pin x to explicit min/max when provided so every chart in a
        // sync group shares the same X scale exactly. Without this,
        // each chart auto-fits to its own data (slightly different
        // per series), and cursor sync between mismatched scales
        // triggers the axis-relayout bug above.
        x: {
          time: true,
          ...(xMin != null && xMax != null ? { range: [xMin, xMax] as [number, number] } : {}),
        },
      },
      axes: [
        {
          stroke: 'var(--text-secondary)',
          grid: { stroke: 'var(--chart-grid)', width: 1 },
          ticks: { stroke: 'var(--chart-axis)', width: 1 },
        },
        {
          stroke: 'var(--text-secondary)',
          grid: { stroke: 'var(--chart-grid)', width: 1 },
          ticks: { stroke: 'var(--chart-axis)', width: 1 },
          size: 50,
          label: unit,
          labelGap: 4,
          labelSize: 10,
          gap: 4,
        },
      ],
      legend: { show: false },
      series: [
        { label: 'time' },
        ...series.map((s) => ({
          label: s.label,
          stroke: s.stroke ?? 'var(--chart-stroke)',
          width: s.width ?? 1.25,
          fill: s.fill ? withAlpha(s.stroke ?? '#2563eb', 0.15) : undefined,
          points: { show: false },
        })),
      ],
      padding: [12, 8, 4, 8],
      hooks: {
        // Fires on every cursor move (mouse motion or sync from another chart).
        // We DOM-mutate the per-series readout spans here — no React re-render.
        setCursor: [
          (u) => {
            const idx = u.cursor.idx;
            series.forEach((s, i) => {
              const el = readoutRefs.current[i];
              if (!el) return;
              if (idx == null || idx < 0) {
                // Mouse left the plot (or sync said no cursor). Clear readout.
                el.textContent = '';
                return;
              }
              const v = (u.data[i + 1] as Array<number | null>)[idx];
              if (v == null) {
                el.textContent = '';
                return;
              }
              const decimals = s.decimals ?? 2;
              el.textContent = `: ${v.toFixed(decimals)}`;
            });
          },
        ],
      },
    };

    const u = new uPlot(opts, data, containerRef.current);
    uplotRef.current = u;
    onCreate?.(u);

    // Resize on container changes
    const ro = new ResizeObserver(() => {
      if (containerRef.current && uplotRef.current) {
        uplotRef.current.setSize({
          width: containerRef.current.clientWidth,
          height,
        });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      u.destroy();
      uplotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timestamps, series, height, syncKey, xMin, xMax]);

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-color)',
        borderRadius: '6px',
        position: 'relative',
        padding: 0,
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: '6px',
          left: '10px',
          fontSize: '0.75rem',
          fontWeight: 500,
          color: 'var(--text-secondary)',
          zIndex: 2,
          pointerEvents: 'none',
          background: 'rgba(255,255,255,0.85)',
          padding: '0 0.375rem',
          borderRadius: '4px',
          display: 'flex',
          gap: '0.75rem',
          alignItems: 'center',
        }}
      >
        {title && <span>{title}</span>}
        {series.map((s, i) => (
          <span
            key={s.label}
            style={{
              color: s.stroke ?? 'var(--text-secondary)',
              fontVariantNumeric: 'tabular-nums',
              whiteSpace: 'nowrap',
            }}
          >
            <span style={{ fontWeight: 600 }}>{s.label}</span>
            <span
              ref={(el) => (readoutRefs.current[i] = el)}
              // Filled in by the setCursor hook above.
              style={{ marginLeft: '0.125rem' }}
            />
            {unit && <span style={{ opacity: 0.6, marginLeft: '0.25rem' }}>{unit}</span>}
          </span>
        ))}
      </div>
      <div ref={containerRef} style={{ width: '100%', height }} />
    </div>
  );
}

/** Mix a hex/rgb color with a given alpha. Falls back to rgba for unknown formats. */
function withAlpha(stroke: string, alpha: number): string {
  // var() — wrap in rgba via a CSS color-mix. Browsers without support fall
  // through to the unmodified stroke; in practice the chart still draws fine.
  if (stroke.startsWith('var(')) {
    return `color-mix(in srgb, ${stroke} ${Math.round(alpha * 100)}%, transparent)`;
  }
  if (stroke.startsWith('#') && (stroke.length === 7 || stroke.length === 4)) {
    const hex = stroke.length === 4 ? `#${stroke[1]}${stroke[1]}${stroke[2]}${stroke[2]}${stroke[3]}${stroke[3]}` : stroke;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return stroke;
}
