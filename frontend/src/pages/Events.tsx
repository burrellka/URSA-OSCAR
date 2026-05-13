import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { NightlyEvent, NightlySummary } from '../api/types';

const EVENT_TYPES: { value: string; label: string }[] = [
  { value: 'ClearAirway', label: 'Central (CA)' },
  { value: 'Obstructive', label: 'Obstructive (OA)' },
  { value: 'Apnea', label: 'Apnea (A)' },
  { value: 'Hypopnea', label: 'Hypopnea (H)' },
  { value: 'RERA', label: 'RERA' },
  { value: 'LargeLeak', label: 'Large Leak' },
];

type SortKey = 'timestamp' | 'duration_seconds' | 'event_type';

export default function Events() {
  const [params, setParams] = useSearchParams();
  const [nights, setNights] = useState<NightlySummary[]>([]);
  const [events, setEvents] = useState<NightlyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventLoading, setEventLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedDate = params.get('date') || '';
  const selectedTypes = params.getAll('type');
  // Default 10s matches AASM clinical scoring (an event needs >= 10s to be a
  // counted respiratory event). Users can manually drop below if they want
  // to see sub-clinical events.
  const MIN_DURATION_DEFAULT = 10;
  const minDurationParam = params.get('min_dur');
  const minDuration = minDurationParam === null
    ? MIN_DURATION_DEFAULT
    : Number(minDurationParam);
  const sortKey = (params.get('sort') as SortKey) || 'timestamp';
  const sortDesc = params.get('desc') === '1';

  useEffect(() => {
    let cancelled = false;
    api.listNights()
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows].sort((a, b) => (a.date < b.date ? 1 : -1));
        setNights(sorted);
        if (!params.get('date') && sorted[0]) {
          const next = new URLSearchParams(params);
          next.set('date', sorted[0].date);
          setParams(next, { replace: true });
        }
      })
      .catch((e: ApiError) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedDate) return;
    let cancelled = false;
    setEventLoading(true);
    setError(null);
    api.listEvents(selectedDate, selectedTypes.length ? selectedTypes : undefined)
      .then((rows) => { if (!cancelled) setEvents(rows); })
      .catch((e: ApiError) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setEventLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate, params.toString()]);

  const filtered = useMemo(() => {
    const list = events.filter((e) => (e.duration_seconds ?? 0) >= minDuration);
    list.sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDesc ? -cmp : cmp;
    });
    return list;
  }, [events, minDuration, sortKey, sortDesc]);

  function update(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null || value === '') next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: true });
  }

  function toggleType(t: string) {
    const next = new URLSearchParams(params);
    const cur = next.getAll('type');
    next.delete('type');
    const after = cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t];
    for (const v of after) next.append('type', v);
    setParams(next, { replace: true });
  }

  function toggleSort(k: SortKey) {
    const next = new URLSearchParams(params);
    if (sortKey === k) next.set('desc', sortDesc ? '0' : '1');
    else { next.set('sort', k); next.set('desc', '0'); }
    setParams(next, { replace: true });
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Events</h1>
      </div>

      {loading && <div className="loading">Loading nights…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && nights.length === 0 && (
        <div className="empty-state">No nights imported yet.</div>
      )}

      {!loading && !error && nights.length > 0 && (
        <>
          <div className="chart-card" style={{ marginBottom: '1rem', display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: '1rem', alignItems: 'end' }}>
            <div className="field">
              <label>Date</label>
              <select value={selectedDate} onChange={(e) => update('date', e.target.value)}>
                {nights.map((n) => (
                  <option key={n.date} value={n.date}>{n.date}</option>
                ))}
              </select>
            </div>

            <div className="field">
              <label>Min duration (s)</label>
              <input
                type="number"
                min={0}
                value={Number.isFinite(minDuration) ? minDuration : ''}
                onChange={(e) => update('min_dur', e.target.value)}
                placeholder="0"
                style={{ width: '80px' }}
              />
            </div>

            <div className="field">
              <label>Event types ({selectedTypes.length || 'all'})</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                {EVENT_TYPES.map(({ value, label }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleType(value)}
                    className={selectedTypes.includes(value) ? 'btn-primary' : 'btn-secondary'}
                    style={{ padding: '0.25rem 0.625rem', fontSize: '0.75rem' }}
                  >
                    {label}
                  </button>
                ))}
                {selectedTypes.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      const next = new URLSearchParams(params);
                      next.delete('type');
                      setParams(next, { replace: true });
                    }}
                    className="icon-btn"
                    title="Clear type filters"
                    style={{ width: 'auto', padding: '0 0.5rem' }}
                  >
                    clear
                  </button>
                )}
              </div>
            </div>
          </div>

          {eventLoading ? (
            <div className="loading">Loading events…</div>
          ) : (
            <div className="chart-card" style={{ overflowX: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem' }}>
                <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>
                  {filtered.length} event{filtered.length === 1 ? '' : 's'}
                </h2>
                <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>{selectedDate}</span>
              </div>
              {filtered.length === 0 ? (
                <div className="empty-state">No matching events.</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <SortHeader label="Time" k="timestamp" current={sortKey} desc={sortDesc} onClick={toggleSort} />
                      <SortHeader label="Type" k="event_type" current={sortKey} desc={sortDesc} onClick={toggleSort} />
                      <SortHeader label="Duration (s)" k="duration_seconds" current={sortKey} desc={sortDesc} onClick={toggleSort} align="right" />
                      <th style={{ textAlign: 'right' }}>Pressure</th>
                      <th style={{ textAlign: 'right' }}>Leak</th>
                      <th>Session</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.slice(0, 500).map((e, i) => (
                      <tr key={(e.id ?? i).toString()}>
                        <td>{e.timestamp.replace('T', ' ').slice(0, 19)}</td>
                        <td><EventBadge t={e.event_type} /></td>
                        <td style={{ textAlign: 'right' }}>{e.duration_seconds?.toFixed(1) ?? '—'}</td>
                        <td style={{ textAlign: 'right' }}>{e.pressure_at_event?.toFixed(2) ?? '—'}</td>
                        <td style={{ textAlign: 'right' }}>{e.leak_at_event?.toFixed(2) ?? '—'}</td>
                        <td>{e.session_id ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {filtered.length > 500 && (
                <div style={{ marginTop: '0.5rem', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
                  Showing first 500 of {filtered.length}. Tighten filters to narrow.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SortHeader({ label, k, current, desc, onClick, align = 'left' }: {
  label: string; k: SortKey; current: SortKey; desc: boolean; onClick: (k: SortKey) => void; align?: 'left' | 'right';
}) {
  const isActive = current === k;
  return (
    <th
      style={{ cursor: 'pointer', textAlign: align, userSelect: 'none' }}
      onClick={() => onClick(k)}
    >
      {label} {isActive ? (desc ? '▼' : '▲') : ''}
    </th>
  );
}

const TYPE_COLOR: Record<string, string> = {
  ClearAirway: 'var(--event-ca)',
  Obstructive: 'var(--event-oa)',
  Apnea: 'var(--event-a)',
  Hypopnea: 'var(--event-h)',
  RERA: 'var(--event-rera)',
  LargeLeak: 'var(--event-leak)',
  CheyneStokes: 'var(--event-csr)',
  PeriodicBreathing: 'var(--event-pb)',
  FlowLimit: 'var(--text-secondary)',
};

function EventBadge({ t }: { t: string }) {
  const color = TYPE_COLOR[t] ?? 'var(--text-secondary)';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.375rem',
        fontSize: '0.8125rem',
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: 4, background: color }} />
      {t}
    </span>
  );
}

function sortValue(e: NightlyEvent, k: SortKey): string | number {
  if (k === 'timestamp') return e.timestamp;
  if (k === 'duration_seconds') return e.duration_seconds ?? 0;
  return e.event_type;
}
