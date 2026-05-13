import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import type { NightlySummary } from '../api/types';
import { ahiSeverity, formatAhi, isoDateOnly } from '../lib/format';

interface Props {
  nights: NightlySummary[];
  /** How many days back from today to render. Default 90. */
  days?: number;
}

/**
 * GitHub-contribution-style calendar heatmap.
 * 7 rows (Sun..Sat) × N weekly columns. Each cell colored by AHI severity.
 * Click navigates to /daily/{date}.
 */
export default function CalendarHeatmap({ nights, days = 90 }: Props) {
  const navigate = useNavigate();

  const { weeks, monthLabels } = useMemo(() => buildGrid(nights, days), [nights, days]);

  return (
    <div className="chart-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.75rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>{days}-day AHI calendar</h2>
        <Legend />
      </div>

      <div style={{ overflowX: 'auto', paddingBottom: '0.5rem' }}>
        <div style={{ display: 'inline-grid', gridTemplateRows: 'auto auto', gap: '0.5rem' }}>
          <MonthLabels labels={monthLabels} />
          <div style={{ display: 'grid', gridAutoFlow: 'column', gridTemplateRows: 'repeat(7, 14px)', gap: '3px' }}>
            {weeks.map((week, wi) =>
              week.map((cell, di) => (
                <Cell
                  key={`${wi}-${di}`}
                  cell={cell}
                  onClick={() => cell?.night && navigate(`/daily/${cell.night.date}`)}
                />
              )),
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

interface Cell {
  date: string;
  night: NightlySummary | undefined;
}

function buildGrid(nights: NightlySummary[], days: number): { weeks: (Cell | null)[][]; monthLabels: { col: number; label: string }[] } {
  const nightsByDate = new Map<string, NightlySummary>();
  for (const n of nights) nightsByDate.set(n.date, n);

  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - days + 1);
  // Round back to the previous Sunday so columns are full weeks
  const startWeekday = start.getDay(); // 0=Sun
  start.setDate(start.getDate() - startWeekday);

  const totalDays = Math.ceil((today.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)) + 1;
  const totalWeeks = Math.ceil(totalDays / 7);

  const weeks: (Cell | null)[][] = [];
  const cursor = new Date(start);
  for (let w = 0; w < totalWeeks; w++) {
    const week: (Cell | null)[] = [];
    for (let d = 0; d < 7; d++) {
      const date = new Date(cursor);
      // Hide future days (cursor passed today)
      if (date > today) {
        week.push(null);
      } else {
        const iso = isoDateOnly(date);
        week.push({ date: iso, night: nightsByDate.get(iso) });
      }
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(week);
  }

  // Build month labels: one per column where the month name changes
  const monthLabels: { col: number; label: string }[] = [];
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  let lastMonth = -1;
  for (let w = 0; w < weeks.length; w++) {
    const firstCell = weeks[w].find((c) => c !== null);
    if (!firstCell) continue;
    const m = new Date(firstCell.date).getMonth();
    if (m !== lastMonth) {
      monthLabels.push({ col: w, label: monthNames[m] });
      lastMonth = m;
    }
  }

  return { weeks, monthLabels };
}

function MonthLabels({ labels }: { labels: { col: number; label: string }[] }) {
  // Render in same column grid as the cells so positions align.
  const maxCol = Math.max(0, ...labels.map((l) => l.col));
  const cols = Array.from({ length: maxCol + 1 }, (_, i) => labels.find((l) => l.col === i)?.label ?? '');
  return (
    <div style={{ display: 'grid', gridAutoFlow: 'column', gap: '3px', fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
      {cols.map((label, i) => (
        <div key={i} style={{ width: '14px' }}>{label}</div>
      ))}
    </div>
  );
}

function Cell({ cell, onClick }: { cell: Cell | null; onClick: () => void }) {
  if (cell === null) {
    return <div style={{ width: '14px', height: '14px' }} />;
  }
  const severity = ahiSeverity(cell.night?.total_ahi);
  const colorClass = severity === 'good' ? 'ahi-good'
    : severity === 'warn' ? 'ahi-warn'
    : severity === 'bad' ? 'ahi-bad'
    : 'ahi-empty';
  const title = cell.night
    ? `${cell.date} — AHI ${formatAhi(cell.night.total_ahi)} (${cell.night.session_count ?? '?'} session${cell.night.session_count === 1 ? '' : 's'})`
    : `${cell.date} — no data`;
  return (
    <button
      type="button"
      className={colorClass}
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={!cell.night}
      style={{
        width: '14px',
        height: '14px',
        border: '1px solid rgba(15,23,42,0.05)',
        borderRadius: '2px',
        cursor: cell.night ? 'pointer' : 'default',
        padding: 0,
      }}
    />
  );
}

function Legend() {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
      <span>AHI</span>
      <span className="ahi-empty" style={{ width: 12, height: 12, borderRadius: 2, display: 'inline-block' }} />
      <span>—</span>
      <span className="ahi-good" style={{ width: 12, height: 12, borderRadius: 2, display: 'inline-block' }} />
      <span>≤ 5</span>
      <span className="ahi-warn" style={{ width: 12, height: 12, borderRadius: 2, display: 'inline-block' }} />
      <span>5–15</span>
      <span className="ahi-bad" style={{ width: 12, height: 12, borderRadius: 2, display: 'inline-block' }} />
      <span>&gt; 15</span>
    </div>
  );
}
