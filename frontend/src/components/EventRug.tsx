import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { NightlyEvent } from '../api/types';

const TYPE_COLOR: Record<string, string> = {
  ClearAirway: '#7c3aed',
  Obstructive: '#dc2626',
  Apnea: '#fb923c',
  Hypopnea: '#d97706',
  RERA: '#2563eb',
  LargeLeak: '#ea580c',
  CheyneStokes: '#6366f1',
  PeriodicBreathing: '#9333ea',
  FlowLimit: '#94a3b8',
};

interface Props {
  events: NightlyEvent[];
  /** Sync with the same key the time-series charts use. */
  syncKey: string;
  /** Cover the same X range as the parent. */
  xMin: number; // epoch seconds
  xMax: number;
  height?: number;
}

/**
 * Custom uPlot draw plugin that paints a colored vertical tick at each
 * event timestamp, color-coded by event_type. Sits above the stacked
 * time-series charts and zooms/pans in sync via the same syncKey.
 */
export default function EventRug({ events, syncKey, xMin, xMax, height = 38 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const data: uPlot.AlignedData = [
      [xMin, xMax],
      [0, 0],
    ];

    const opts: uPlot.Options = {
      width: containerRef.current.clientWidth,
      height,
      cursor: { sync: { key: syncKey, setSeries: true }, focus: { prox: 30 }, drag: { x: true, y: false } },
      scales: { x: { time: true }, y: { range: [0, 1] } },
      axes: [
        {
          stroke: 'var(--text-secondary)',
          grid: { stroke: 'var(--chart-grid)', width: 1 },
        },
        { show: false },
      ],
      legend: { show: false },
      series: [{}, { label: '', stroke: 'transparent' }],
      padding: [12, 8, 4, 8],
      hooks: {
        drawClear: [
          (u) => {
            const ctx = u.ctx;
            const yTop = u.bbox.top;
            const yBot = u.bbox.top + u.bbox.height;
            for (const ev of events) {
              const t = epochSeconds(ev.timestamp);
              if (t < u.scales.x.min! || t > u.scales.x.max!) continue;
              const x = Math.round(u.valToPos(t, 'x', true));
              ctx.save();
              ctx.strokeStyle = TYPE_COLOR[ev.event_type] || '#94a3b8';
              ctx.lineWidth = 1.5;
              ctx.beginPath();
              ctx.moveTo(x, yTop);
              ctx.lineTo(x, yBot);
              ctx.stroke();
              ctx.restore();
            }
          },
        ],
      },
    };

    const u = new uPlot(opts, data, containerRef.current);

    const ro = new ResizeObserver(() => {
      u.setSize({ width: containerRef.current!.clientWidth, height });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      u.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, syncKey, xMin, xMax]);

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-color)',
        borderRadius: '6px',
        position: 'relative',
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
        }}
      >
        Events
      </div>
      <div ref={containerRef} style={{ width: '100%', height }} />
    </div>
  );
}

function epochSeconds(iso: string): number {
  return new Date(iso).getTime() / 1000;
}
