import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { NightlyEvent, NightlySummary } from '../api/types';
import { formatAhi, formatMinutesAsHM } from '../lib/format';
import TimeSeriesChart from '../components/TimeSeriesChart';
import EventRug from '../components/EventRug';

const TRACK_SERIES = [
  'pressure',
  'leak',
  'flow_limit',
  'tidal_volume',
  'minute_vent',
  'resp_rate',
  'snore',
] as const;
type TrackSeries = typeof TRACK_SERIES[number];

type SeriesPayload = Record<string, { timestamps: number[]; values: (number | null)[]; secondary: (number | null)[] | null }>;

export default function Daily() {
  const { date } = useParams<{ date?: string }>();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<NightlySummary | null>(null);
  const [events, setEvents] = useState<NightlyEvent[]>([]);
  const [waveforms, setWaveforms] = useState<SeriesPayload>({});
  const [allDates, setAllDates] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [wfLoading, setWfLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    api.listNights()
      .then(async (rows) => {
        if (cancelled) return;
        const dates = rows.map((n) => n.date).sort();
        setAllDates(dates);
        const target = date || dates[dates.length - 1];
        if (!target) { setSummary(null); return; }
        if (!date && target) { navigate(`/daily/${target}`, { replace: true }); return; }
        const [n, ev] = await Promise.all([
          api.getNight(target),
          api.listEvents(target),
        ]);
        if (cancelled) return;
        setSummary(n);
        setEvents(ev);
      })
      .catch((e: ApiError) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [date, navigate]);

  // Load waveforms after the summary lands. Skipped if the night has no data.
  useEffect(() => {
    if (!date) return;
    let cancelled = false;
    setWfLoading(true);
    api.getTimeseries(date, [...TRACK_SERIES])
      .then((resp) => { if (!cancelled) setWaveforms(resp.series); })
      .catch(() => { if (!cancelled) setWaveforms({}); })
      .finally(() => { if (!cancelled) setWfLoading(false); });
    return () => { cancelled = true; };
  }, [date]);

  const idx = date ? allDates.indexOf(date) : -1;
  const prev = idx > 0 ? allDates[idx - 1] : null;
  const next = idx >= 0 && idx < allDates.length - 1 ? allDates[idx + 1] : null;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Daily View</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn-secondary" onClick={() => prev && navigate(`/daily/${prev}`)} disabled={!prev}>
            ◀ {prev ?? '—'}
          </button>
          <button className="btn-secondary" onClick={() => next && navigate(`/daily/${next}`)} disabled={!next}>
            {next ?? '—'} ▶
          </button>
        </div>
      </div>

      {loading && <div className="loading">Loading {date ?? 'most recent night'}…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && !summary && (
        <div className="empty-state">No night data yet. Use <a href="/import">Import</a> to load some.</div>
      )}

      {summary && (
        <>
          <SummaryTiles s={summary} />
          <Charts
            summary={summary}
            events={events}
            waveforms={waveforms}
            wfLoading={wfLoading}
          />
          <BottomSection s={summary} events={events} waveforms={waveforms} />
        </>
      )}
    </div>
  );
}

function SummaryTiles({ s }: { s: NightlySummary }) {
  return (
    <div className="stat-grid" style={{ marginBottom: '1.25rem' }}>
      <Tile label="Date" value={s.date} sub={`${s.session_count ?? '—'} session${s.session_count === 1 ? '' : 's'}`} />
      <Tile label="AHI" value={formatAhi(s.total_ahi)} sub={`${s.minutes_in_apnea ?? 0} min in apnea`} />
      <Tile label="Mask-on" value={formatMinutesAsHM(s.total_time_minutes)} />
      <Tile label="Median / 95% pressure" value={`${formatAhi(s.median_pressure)} / ${formatAhi(s.p95_pressure)}`} sub="cmH₂O" />
    </div>
  );
}

function Charts({
  summary, events, waveforms, wfLoading,
}: {
  summary: NightlySummary;
  events: NightlyEvent[];
  waveforms: SeriesPayload;
  wfLoading: boolean;
}) {
  const syncKey = `daily-${summary.date}`;

  const { xMin, xMax } = useMemo(() => {
    if (!summary.start_time || !summary.end_time) {
      return { xMin: 0, xMax: 1 };
    }
    return {
      xMin: new Date(summary.start_time).getTime() / 1000,
      xMax: new Date(summary.end_time).getTime() / 1000,
    };
  }, [summary]);

  // Per-track config. Units chosen to match the OSCAR Phase-2-polish work
  // order; tidal_volume is converted L -> mL via the `valueScale` field so
  // the hover readout reads "Tidal Vol.: 397.90 mL" rather than the raw
  // 0.40 L stored on disk.
  const tracks: {
    series: TrackSeries;
    label: string;
    unit?: string;
    stroke?: string;
    secondary?: { label: string; stroke?: string };
    fill?: boolean;
    /** Multiply stored values by this factor before display. Default 1. */
    valueScale?: number;
  }[] = [
    { series: 'pressure',     label: 'Pressure',     unit: 'cmH₂O', stroke: 'var(--accent-primary)', secondary: { label: 'EPAP', stroke: 'var(--event-rera)' } },
    { series: 'leak',         label: 'Leak',         unit: 'L/min', stroke: 'var(--event-leak)', fill: true },
    { series: 'flow_limit',   label: 'Flow Limit',   stroke: 'var(--text-secondary)' },
    { series: 'tidal_volume', label: 'Tidal Vol.',   unit: 'mL',    stroke: 'var(--tier-primary)', valueScale: 1000 },
    { series: 'minute_vent',  label: 'Minute Vent.', unit: 'L/min', stroke: 'var(--tier-secondary)' },
    { series: 'resp_rate',    label: 'Resp Rate',    unit: '/min',  stroke: 'var(--tier-tertiary)' },
    { series: 'snore',        label: 'Snore',        stroke: 'var(--event-h)' },
  ];

  const allEmpty = TRACK_SERIES.every((s) => (waveforms[s]?.timestamps.length ?? 0) === 0);

  if (wfLoading) {
    return (
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <div className="loading">Loading waveforms…</div>
      </div>
    );
  }

  if (allEmpty) {
    return (
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>Waveforms</h2>
        <div className="empty-state" style={{ padding: '2rem 1rem' }}>
          No time-series data on disk for this night. Re-run the importer with{' '}
          <code>include_timeseries=True</code> (default since v0.3.0) to populate.
        </div>
      </div>
    );
  }

  return (
    <div className="chart-card" style={{ marginBottom: '1.25rem', padding: '0.75rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem', padding: '0 0.5rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Waveforms · {events.length} events</h2>
        <a
          href={`/api/v1/exports/${summary.date}.csv`}
          download={`ursa-oscar-${summary.date}.csv`}
          className="btn-secondary"
          style={{ fontSize: '0.8125rem', padding: '0.375rem 0.75rem' }}
        >
          Export CSV
        </a>
      </div>

      <div style={{ display: 'grid', gap: '0.375rem' }}>
        <EventRug events={events} syncKey={syncKey} xMin={xMin} xMax={xMax} height={36} />
        {tracks.map(({ series, label, unit, stroke, secondary, fill, valueScale }) => {
          const w = waveforms[series];
          if (!w || w.timestamps.length === 0) return null;
          const scaled = valueScale && valueScale !== 1
            ? w.values.map((v) => (v == null ? null : v * valueScale))
            : w.values;
          const seriesList: Array<{ label: string; values: (number | null)[]; stroke?: string; fill?: boolean }> = [
            { label, values: scaled, stroke, fill },
          ];
          if (secondary && w.secondary && w.secondary.length === w.values.length) {
            const scaledSecondary = valueScale && valueScale !== 1
              ? w.secondary.map((v) => (v == null ? null : v * valueScale))
              : w.secondary;
            seriesList.push({ label: secondary.label, values: scaledSecondary, stroke: secondary.stroke });
          }
          return (
            <TimeSeriesChart
              key={series}
              timestamps={w.timestamps}
              series={seriesList}
              unit={unit}
              syncKey={syncKey}
              height={130}
            />
          );
        })}
      </div>
    </div>
  );
}

/**
 * Daily View bottom section — three rows of cards below the chart stack.
 *
 *   Row 1: [AHI breakdown] [Device Settings]   (2-col grid, narrow content)
 *   Row 2: [Extended Statistics]                (full-width, 5-col table)
 *   Row 3: [Session Information]                (full-width, 5-col table)
 *
 * Phase 2 polish, work order Item 2.
 */
function BottomSection({
  s, events, waveforms,
}: {
  s: NightlySummary;
  events: NightlyEvent[];
  waveforms: SeriesPayload;
}) {
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '1rem', marginBottom: '1rem' }}>
        <AhiBreakdownCard s={s} events={events} />
        <DeviceSettingsCard s={s} />
      </div>
      <div style={{ marginBottom: '1rem' }}>
        <ExtendedStatisticsCard s={s} waveforms={waveforms} />
      </div>
      <div>
        <SessionInformationCard s={s} events={events} />
      </div>
    </>
  );
}

function AhiBreakdownCard({ s, events }: { s: NightlySummary; events: NightlyEvent[] }) {
  const counts: Record<string, number> = {};
  for (const ev of events) {
    counts[ev.event_type] = (counts[ev.event_type] ?? 0) + 1;
  }
  return (
    <div className="chart-card">
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>AHI breakdown</h2>
      <table className="data-table">
        <thead>
          <tr><th>Metric</th><th style={{ textAlign: 'right' }}>events / hour</th></tr>
        </thead>
        <tbody>
          <tr><td>Obstructive AHI</td><td style={{ textAlign: 'right' }}>{formatAhi(s.obstructive_ahi)}</td></tr>
          <tr><td>Central AHI</td><td style={{ textAlign: 'right' }}>{formatAhi(s.central_ahi)}</td></tr>
          <tr><td>Hypopnea index</td><td style={{ textAlign: 'right' }}>{formatAhi(s.hypopnea_index)}</td></tr>
          <tr><td>RERA index</td><td style={{ textAlign: 'right' }}>{formatAhi(s.rera_index)}</td></tr>
          <tr><td>Large-leak %</td><td style={{ textAlign: 'right' }}>{formatAhi(s.large_leak_pct)}%</td></tr>
          <tr><td>Mask-on / over redline</td>
            <td style={{ textAlign: 'right' }}>
              {formatMinutesAsHM(s.total_time_minutes)} / {s.minutes_over_leak_redline?.toFixed(1) ?? '—'} min
            </td>
          </tr>
        </tbody>
      </table>
      {Object.keys(counts).length > 0 && (
        <>
          <h3 style={{ fontSize: '0.875rem', fontWeight: 600, margin: '0.875rem 0 0.5rem', color: 'var(--text-secondary)' }}>Event counts</h3>
          <table className="data-table">
            <tbody>
              {Object.entries(counts).map(([t, c]) => (
                <tr key={t}>
                  <td>{t}</td>
                  <td style={{ textAlign: 'right' }}>{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/**
 * Card B — Device Settings (renamed from Equipment per work order Item 2).
 *
 * Renders the eight equipment-setting columns the Phase-1.5 settings_parser
 * populates: machine_model, mode, min/max_pressure_setting, epr_level,
 * ramp_time_minutes, humidity_level, mask_type.
 *
 * The work order references a 10-field OSCAR-parity extension (Antibacterial
 * Filter, Climate Control, EPR Mode, Humidifier Status, Patient View,
 * Response, Smart Start, Temperature, Temperature Enable). Those fields are
 * NOT in the v1 nightly_summary schema and aren't parsed by
 * settings_parser.py — adding them requires a schema-v2 migration plus
 * parser work plus a backfill reimport. **Surfaced to architect chat for
 * explicit scope decision before the v2 migration lands.** For now this
 * card ships the eight fields we have.
 */
function DeviceSettingsCard({ s }: { s: NightlySummary }) {
  // Show the humidity level as a clean numeric when it parses as an integer
  // (1-8); fall back to the raw legacy value otherwise.
  const humidityNumeric = (() => {
    if (s.humidity_level == null) return null;
    const n = parseInt(s.humidity_level, 10);
    return Number.isFinite(n) ? n : null;
  })();
  return (
    <div className="chart-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Device Settings</h2>
        {s.last_updated && (
          <span
            style={{
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              fontVariantNumeric: 'tabular-nums',
            }}
            title={`Row written at ${s.last_updated} (last import for this night)`}
          >
            last imported {formatLastImported(s.last_updated)}
          </span>
        )}
      </div>
      {/* OSCAR carries the same disclaimer — CurrentSettings.json reflects
          the most-recent device state at SD-card export time, not the
          per-night history. Phase 4 (STR.edf parsing) refines this. */}
      <div style={{
        fontSize: '0.8125rem',
        color: 'var(--text-muted)',
        fontStyle: 'italic',
        marginBottom: '0.5rem',
      }}>
        Settings shown below are based on the most-recent device state at SD-card
        export time. They may not reflect per-night history if a prescription
        changed mid-week.
      </div>
      <table className="data-table">
        <tbody>
          <Row label="Machine" v={s.machine_model} />
          <Row label="Mode" v={s.mode} />
          <Row label="Pressure Min" v={s.min_pressure_setting !== null ? `${s.min_pressure_setting.toFixed(2)} cmH₂O` : null} />
          <Row label="Pressure Max" v={s.max_pressure_setting !== null ? `${s.max_pressure_setting.toFixed(2)} cmH₂O` : null} />
          <Row label="Antibacterial Filter" v={s.antibacterial_filter} />
          <Row label="Climate Control" v={s.climate_control} />
          <Row label="EPR" v={s.epr_mode} />
          <Row label="EPR Level" v={s.epr_level === null ? null : s.epr_level === 0 ? 'Off' : `${s.epr_level} cmH₂O`} />
          <Row label="Humidifier Status" v={s.humidifier_status} />
          <Row label="Humidity Level" v={humidityNumeric ?? s.humidity_level} />
          <Row label="Mask" v={s.mask_type} />
          <Row label="Patient View" v={s.patient_view} />
          <Row label="Ramp" v={s.ramp_time_minutes === 0 ? 'Off' : s.ramp_time_minutes !== null ? `${s.ramp_time_minutes} min` : null} />
          <Row label="Response" v={s.response_mode} />
          <Row label="Smart Start" v={s.smart_start} />
          <Row label="Temperature" v={s.temperature_celsius !== null ? `${s.temperature_celsius.toFixed(0)} °C` : null} />
          <Row label="Temperature Enable" v={s.temperature_enable} />
        </tbody>
      </table>
    </div>
  );
}

// Channels for Extended Statistics card. `scale` applies to all four
// percentile columns; `precision` controls decimals shown.
const STAT_CHANNELS: Array<{
  key: keyof SeriesPayload | '__inspTime' | '__expTime';
  label: string;
  unit: string;
  scale?: number;
  precision: number;
}> = [
  { key: 'pressure',     label: 'Pressure',             unit: 'cmH₂O', precision: 2 },
  // EPAP comes from the pressure timeseries' secondary column — handled
  // specially inline below.
  { key: 'pressure',     label: 'EPAP',                 unit: 'cmH₂O', precision: 2 },
  { key: 'minute_vent',  label: 'Minute Ventilation',   unit: 'L/min', precision: 2 },
  { key: 'resp_rate',    label: 'Respiratory Rate',     unit: '/min',  precision: 1 },
  { key: 'flow_limit',   label: 'Flow Limitation',      unit: '',      precision: 2 },
  { key: 'leak',         label: 'Leak Rate',            unit: 'L/min', precision: 1 },
  { key: 'snore',        label: 'Snore',                unit: '',      precision: 2 },
  { key: '__inspTime',   label: 'Inspiration Time',     unit: 's',     precision: 2 },
  { key: '__expTime',    label: 'Expiration Time',      unit: 's',     precision: 2 },
  { key: 'tidal_volume', label: 'Tidal Volume',         unit: 'mL',    scale: 1000, precision: 1 },
];

/**
 * Card A — Extended Per-Night Statistics. Min / Median / 95% / 99.5%
 * across ten channels (work order Item 2).
 *
 * Percentiles are computed client-side from the waveform arrays already
 * loaded for the chart. Sorting a 12k-sample array runs in <5ms and
 * happens once per (date, channel) inside the useMemo below.
 *
 * Inspiration Time and Expiration Time aren't parsed by the v1 importer
 * (PLD has InspTime.2s + ExpTime.2s signals; storing them would need a
 * schema-v2 addition). They render '—' per the work order's spec for
 * missing channels. Surfaced as a Phase 2.5 candidate alongside the
 * Device Settings expansion.
 *
 * Beneath the percentile table we render the two summary-level numbers
 * the work order asks for: total time in apnea (HH:MM:SS) and time over
 * leak redline (X.XXX%).
 */
function ExtendedStatisticsCard({
  s, waveforms,
}: { s: NightlySummary; waveforms: SeriesPayload }) {
  const rows = useMemo(() => {
    return STAT_CHANNELS.map((ch, idx) => {
      // 2nd channel is EPAP — pull the secondary column off the pressure series.
      const isEpap = ch.label === 'EPAP';
      if (ch.key === '__inspTime' || ch.key === '__expTime') {
        return { ...ch, idx, stats: null };
      }
      const w = waveforms[ch.key as string];
      if (!w) return { ...ch, idx, stats: null };
      const raw = isEpap ? (w.secondary ?? []) : w.values;
      const scale = ch.scale ?? 1;
      const vals = raw.filter((v): v is number => v != null && Number.isFinite(v));
      if (vals.length === 0) return { ...ch, idx, stats: null };
      const sorted = [...vals].sort((a, b) => a - b);
      const stats = {
        min: sorted[0] * scale,
        med: percentile(sorted, 0.50) * scale,
        p95: percentile(sorted, 0.95) * scale,
        p995: percentile(sorted, 0.995) * scale,
      };
      return { ...ch, idx, stats };
    });
  }, [waveforms]);

  return (
    <div className="chart-card">
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
        Extended Statistics
      </h2>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Channel</th>
            <th style={{ textAlign: 'right' }}>Min</th>
            <th style={{ textAlign: 'right' }}>Median</th>
            <th style={{ textAlign: 'right' }}>95%</th>
            <th style={{ textAlign: 'right' }}>99.5%</th>
            <th style={{ textAlign: 'left', color: 'var(--text-muted)' }}>Unit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.idx}>
              <td>{r.label}</td>
              {r.stats ? (
                <>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.stats.min.toFixed(r.precision)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.stats.med.toFixed(r.precision)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.stats.p95.toFixed(r.precision)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.stats.p995.toFixed(r.precision)}</td>
                </>
              ) : (
                <>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>—</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>—</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>—</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-muted)' }}>—</td>
                </>
              )}
              <td style={{ color: 'var(--text-muted)' }}>{r.unit}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: '2rem', marginTop: '0.875rem', fontSize: '0.875rem' }}>
        <span>
          <strong>Total time in apnea:</strong>{' '}
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {s.minutes_in_apnea !== null ? formatMinutesAsHMS(s.minutes_in_apnea) : '—'}
          </span>
        </span>
        <span>
          <strong>Time over leak redline:</strong>{' '}
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            {s.large_leak_pct !== null ? `${s.large_leak_pct.toFixed(3)}%` : '—'}
          </span>
        </span>
      </div>
    </div>
  );
}

/**
 * Card C — Session Information. One row per CPAP session for the night
 * with checkbox / date / start / end / duration columns, plus a
 * ResMed session-id caption per row (work order Item 2).
 *
 * Session bounds are derived from nightly_events: for each distinct
 * session_id, we take min/max of the event timestamps. Falls back to
 * summary.start_time / end_time when a session has zero events (rare on
 * real-world apnea-prone data; defensive).
 *
 * The "On" checkbox is display-only in Phase 2 (always checked). Phase 3
 * makes it writeable when manual logging lands.
 */
function SessionInformationCard({
  s, events,
}: { s: NightlySummary; events: NightlyEvent[] }) {
  const sessions = useMemo(() => {
    // session_id -> { start, end } derived from event timestamps.
    const m = new Map<number, { start: string; end: string }>();
    for (const ev of events) {
      if (ev.session_id == null) continue;
      const cur = m.get(ev.session_id);
      if (!cur) {
        m.set(ev.session_id, { start: ev.timestamp, end: ev.timestamp });
      } else {
        if (ev.timestamp < cur.start) cur.start = ev.timestamp;
        if (ev.timestamp > cur.end) cur.end = ev.timestamp;
      }
    }
    const out = Array.from(m.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([sid, { start, end }]) => ({ sid, start, end }));

    // Single-session night: replace event-min/max with summary.start_time /
    // end_time. Events bound the *first* and *last* respiratory event, NOT
    // the actual mask-on window — for a single session those are the same
    // human-meaningful boundaries. Multi-session nights still rely on
    // event-derived bounds until the API exposes per-session start/end
    // explicitly (Phase 3 work).
    if (out.length === 1 && s.start_time && s.end_time) {
      out[0] = { sid: out[0].sid, start: s.start_time, end: s.end_time };
    }

    // Defensive fallback for nights where events came back empty but
    // summary.session_count >= 1.
    if (out.length === 0 && (s.session_count ?? 0) > 0 && s.start_time && s.end_time) {
      out.push({ sid: 1, start: s.start_time, end: s.end_time });
    }
    return out;
  }, [events, s]);

  if (sessions.length === 0) {
    return (
      <div className="chart-card">
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>Session Information</h2>
        <div className="empty-state" style={{ padding: '1rem 0.5rem' }}>No sessions for this night.</div>
      </div>
    );
  }

  return (
    <div className="chart-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.5rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Session Information</h2>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
          {sessions.length} session{sessions.length === 1 ? '' : 's'}
        </span>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: '2rem' }}>On</th>
            <th>Date</th>
            <th>Start</th>
            <th>End</th>
            <th>Duration</th>
            <th style={{ color: 'var(--text-muted)' }}>ResMed Session</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map(({ sid, start, end }) => (
            <tr key={sid}>
              <td>
                <input
                  type="checkbox"
                  checked
                  // Phase 2: display-only. Phase 3 manual logging makes it writeable.
                  readOnly
                  aria-label={`Session ${sid} included`}
                />
              </td>
              <td>{formatShortDate(start)}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>{formatTime(start)}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>{formatTime(end)}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>{formatDuration(start, end)}</td>
              <td style={{ color: 'var(--text-muted)' }}>ResMed Session #{sid}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- helpers ---------------------------------------------------------------

/** Inclusive percentile via nearest-rank on a SORTED array. p in [0, 1]. */
function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  if (sorted.length === 1) return sorted[0];
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * p)));
  return sorted[idx];
}

function formatMinutesAsHMS(minutes: number): string {
  const totalSec = Math.round(minutes * 60);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const sec = totalSec % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
}

function formatShortDate(iso: string): string {
  // iso = "2026-05-08T22:05:23" -> "5/8/26"
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear() % 100}`;
}

function formatTime(iso: string): string {
  // 24h HH:MM:SS
  return iso.slice(11, 19);
}

function formatLastImported(iso: string): string {
  // Best-effort: render as "M/D/YY h:mm AM/PM" or "today h:mm AM/PM" if recent.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (sameDay) return `today ${time}`;
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return `yesterday ${time}`;
  const datePart = `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear() % 100}`;
  return `${datePart} ${time}`;
}

function formatDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const sec = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function Row({ label, v }: { label: string; v: string | number | null | undefined }) {
  return (
    <tr>
      <td style={{ color: 'var(--text-secondary)' }}>{label}</td>
      <td>{v === null || v === undefined ? '—' : v}</td>
    </tr>
  );
}
