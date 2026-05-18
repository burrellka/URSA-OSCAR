/**
 * Phase 6 Ticket 6.3 — Reports page.
 *
 * Two-column layout per the work order:
 *   Left:  Configure (template radio, date range preset + custom,
 *          Preview button)
 *   Right: Preview metadata (sections, methods, confidence) + Generate
 *          button. After generation, surface a "Download again" affordance.
 *
 * The PDF download flow: fetch the blob, create an object URL, trigger
 * a click on a hidden <a>. No new browser tab — the file just lands in
 * the user's Downloads folder with the server-supplied filename.
 *
 * Per Decision 6.3-E: insufficient-data warnings show up in the preview
 * panel as a list of section names with explanations, but Generate is
 * still enabled — the operator can choose to generate a PDF that's
 * honest about the gaps.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { api, ApiError } from '../api/client';


type TemplateKey =
  | 'full_clinical_report'
  | 'summary_report'
  | 'analytical_report';

type RangePreset = '30d' | '90d' | '180d' | 'all';


const TEMPLATE_OPTIONS: { key: TemplateKey; label: string; description: string }[] = [
  {
    key: 'full_clinical_report',
    label: 'Full Clinical Report',
    description:
      '8-12 pages. Comprehensive overview, trends, pairwise correlations, multivariate analysis, lag analysis, predictions, and methodology. For annual reviews or major treatment-change consultations.',
  },
  {
    key: 'summary_report',
    label: 'Summary Report',
    description:
      '2-3 pages. Key metrics, recent trends, top 3 correlations, and methodology. Suitable for routine follow-up appointments.',
  },
  {
    key: 'analytical_report',
    label: 'Analytical Report',
    description:
      '4-6 pages. Skips OSA boilerplate; focuses on multivariate correlation, lag analysis, predictions, and methodology. For established care where analytical updates are the conversation.',
  },
];


function resolveRange(preset: RangePreset, latest: string): { start: string; end: string } {
  if (!latest) return { start: '', end: '' };
  const end = latest;
  if (preset === 'all') {
    // Heuristic: pull "all" as last 5 years from latest. The server
    // doesn't reject wide ranges and the cache is keyed by exact dates,
    // so even a "too wide" preset is fine.
    const endDate = new Date(latest);
    const startDate = new Date(endDate);
    startDate.setFullYear(startDate.getFullYear() - 5);
    return { start: startDate.toISOString().slice(0, 10), end };
  }
  const days = preset === '30d' ? 30 : preset === '90d' ? 90 : 180;
  const endDate = new Date(latest);
  const startDate = new Date(endDate);
  startDate.setDate(startDate.getDate() - days + 1);
  return { start: startDate.toISOString().slice(0, 10), end };
}


type PreviewState = Awaited<ReturnType<typeof api.previewReportMetadata>>;


export default function Reports() {
  const [template, setTemplate] = useState<TemplateKey>('summary_report');
  const [preset, setPreset] = useState<RangePreset>('90d');
  const [customStart, setCustomStart] = useState<string>('');
  const [customEnd, setCustomEnd] = useState<string>('');
  const [useCustom, setUseCustom] = useState(false);
  const [latestDate, setLatestDate] = useState<string>('');
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastFilename, setLastFilename] = useState<string | null>(null);

  // Pull the latest imported night once on mount so the range presets
  // anchor to "today" in the user's data, not today's wall-clock.
  useEffect(() => {
    api.listNights()
      .then((nights) => {
        if (nights.length > 0) {
          const latest = [...nights].sort((a, b) => a.date.localeCompare(b.date)).at(-1);
          if (latest) setLatestDate(latest.date);
        }
      })
      .catch(() => {/* leave latestDate empty; user can enter dates manually */});
  }, []);

  const resolved = useMemo(() => {
    if (useCustom) {
      return { start: customStart, end: customEnd };
    }
    return resolveRange(preset, latestDate);
  }, [useCustom, customStart, customEnd, preset, latestDate]);

  const canSubmit = Boolean(resolved.start && resolved.end);

  const runPreview = useCallback(async () => {
    if (!canSubmit) return;
    setPreviewing(true);
    setError(null);
    try {
      const meta = await api.previewReportMetadata({
        template,
        start_date: resolved.start,
        end_date: resolved.end,
      });
      setPreview(meta);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }, [canSubmit, template, resolved.start, resolved.end]);

  const runGenerate = useCallback(async () => {
    if (!canSubmit) return;
    setGenerating(true);
    setError(null);
    try {
      const { blob, filename } = await api.generateReportBlob({
        template,
        start_date: resolved.start,
        end_date: resolved.end,
      });
      // Trigger download via hidden anchor.
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setLastFilename(filename);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }, [canSubmit, template, resolved.start, resolved.end]);

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Reports</h1>
        <p className="page-subtitle">
          Generate a PDF report combining your CPAP data, analytical findings,
          and methodology disclosures. Three templates — pick the one that
          fits the appointment.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
        {/* ===== Left column: Configure ===== */}
        <div className="chart-card">
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginTop: 0, marginBottom: '0.625rem' }}>
            Configure
          </h2>

          <div className="field" style={{ marginBottom: '0.875rem' }}>
            <label>Template</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {TEMPLATE_OPTIONS.map((opt) => (
                <label
                  key={opt.key}
                  style={{
                    padding: '0.5rem 0.625rem',
                    border: `1px solid ${template === opt.key ? 'var(--accent-primary, #2563eb)' : 'var(--border-color, #e5e7eb)'}`,
                    borderRadius: '6px',
                    cursor: 'pointer',
                    background: template === opt.key ? 'rgba(37, 99, 235, 0.05)' : undefined,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <input
                      type="radio"
                      name="template"
                      value={opt.key}
                      checked={template === opt.key}
                      onChange={() => { setTemplate(opt.key); setPreview(null); setLastFilename(null); }}
                    />
                    <strong style={{ fontSize: '0.9375rem' }}>{opt.label}</strong>
                  </div>
                  <div style={{
                    fontSize: '0.75rem',
                    color: 'var(--text-muted)',
                    marginTop: '0.25rem',
                    marginLeft: '1.5rem',
                    lineHeight: 1.4,
                  }}>
                    {opt.description}
                  </div>
                </label>
              ))}
            </div>
          </div>

          <div className="field" style={{ marginBottom: '0.625rem' }}>
            <label>Date range</label>
            {!useCustom ? (
              <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap', alignItems: 'center' }}>
                {(['30d', '90d', '180d', 'all'] as RangePreset[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={preset === p ? 'btn-primary' : 'btn-secondary'}
                    onClick={() => { setPreset(p); setPreview(null); setLastFilename(null); }}
                    style={{ fontSize: '0.8125rem', padding: '0.3rem 0.625rem' }}
                  >
                    {p === '30d' ? 'Last 30 days' :
                     p === '90d' ? 'Last 90 days' :
                     p === '180d' ? 'Last 6 months' : 'All data'}
                  </button>
                ))}
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setUseCustom(true)}
                  style={{ fontSize: '0.8125rem', padding: '0.3rem 0.625rem' }}
                >
                  Custom…
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  type="date"
                  value={customStart}
                  onChange={(e) => { setCustomStart(e.target.value); setPreview(null); }}
                  style={{ fontSize: '0.875rem' }}
                />
                <span>to</span>
                <input
                  type="date"
                  value={customEnd}
                  onChange={(e) => { setCustomEnd(e.target.value); setPreview(null); }}
                  style={{ fontSize: '0.875rem' }}
                />
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setUseCustom(false)}
                  style={{ fontSize: '0.8125rem', padding: '0.3rem 0.625rem' }}
                >
                  Back to presets
                </button>
              </div>
            )}
            {resolved.start && resolved.end && (
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.375rem' }}>
                Resolved range: <code>{resolved.start}</code> to <code>{resolved.end}</code>
              </div>
            )}
          </div>

          <button
            type="button"
            className="btn-secondary"
            onClick={runPreview}
            disabled={!canSubmit || previewing}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <RefreshCw size={14} className={previewing ? 'spin' : undefined} />
            {previewing ? 'Loading preview…' : 'Preview'}
          </button>
        </div>

        {/* ===== Right column: Preview + Generate ===== */}
        <div className="chart-card">
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginTop: 0, marginBottom: '0.625rem' }}>
            Preview & generate
          </h2>

          {!preview && !previewing && (
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              Configure the report on the left and click <strong>Preview</strong>
              {' '}to see what will be included.
            </div>
          )}

          {previewing && (
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              Collecting analytical previews…
            </div>
          )}

          {preview && (
            <div>
              <table className="data-table" style={{ fontSize: '0.875rem', width: '100%', marginBottom: '0.625rem' }}>
                <tbody>
                  <tr><td style={{ color: 'var(--text-secondary)' }}>Template</td><td>{preview.template_label}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary)' }}>Estimated pages</td><td>{preview.estimated_page_count}</td></tr>
                  <tr><td style={{ color: 'var(--text-secondary)' }}>Nights in range</td><td>{preview.n_nights_in_range}</td></tr>
                  <tr>
                    <td style={{ color: 'var(--text-secondary)' }}>Prediction confidence</td>
                    <td>{preview.confidence_level_for_predictions ?? <span className="muted">—</span>}</td>
                  </tr>
                  <tr>
                    <td style={{ color: 'var(--text-secondary)' }}>Methods included</td>
                    <td>{preview.methodology_section_includes.length}</td>
                  </tr>
                </tbody>
              </table>

              <div style={{ fontSize: '0.8125rem', marginBottom: '0.5rem' }}>
                <strong>Sections:</strong>
                <ul style={{ margin: '0.25rem 0 0 1.25rem', padding: 0, lineHeight: 1.5 }}>
                  {preview.sections_included.map((s) => {
                    const refused = preview.sections_with_insufficient_data.includes(s);
                    return (
                      <li key={s} style={{ color: refused ? 'var(--ahi-warn, #d97706)' : undefined }}>
                        {refused
                          ? <><AlertTriangle size={12} style={{ verticalAlign: 'middle' }} /> {s} — insufficient data</>
                          : <><CheckCircle2 size={12} style={{ verticalAlign: 'middle' }} /> {s}</>}
                      </li>
                    );
                  })}
                </ul>
              </div>

              {preview.sections_with_insufficient_data.length > 0 && (
                <div style={{
                  padding: '0.5rem 0.625rem',
                  background: 'rgba(217, 119, 6, 0.1)',
                  borderLeft: '3px solid var(--ahi-warn, #d97706)',
                  borderRadius: '4px',
                  fontSize: '0.75rem',
                  marginBottom: '0.625rem',
                }}>
                  Some sections have insufficient data for the chosen range.
                  They will appear in the PDF as explicit "insufficient data"
                  callouts, not omitted. You can still generate the report —
                  the PDF is honest about what's missing.
                </div>
              )}

              <button
                type="button"
                className="btn-primary"
                onClick={runGenerate}
                disabled={generating}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
              >
                <Download size={14} className={generating ? 'spin' : undefined} />
                {generating ? 'Generating…' : 'Generate PDF'}
              </button>

              {lastFilename && !generating && (
                <div style={{
                  marginTop: '0.625rem',
                  padding: '0.5rem 0.625rem',
                  background: 'rgba(37, 99, 235, 0.08)',
                  borderRadius: '4px',
                  fontSize: '0.8125rem',
                }}>
                  <CheckCircle2 size={14} style={{ verticalAlign: 'middle' }} />
                  {' '}Downloaded <code>{lastFilename}</code>. Generate again for a
                  fresh copy or change the template / range and click
                  Preview.
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="error-banner" style={{ marginTop: '0.625rem', fontSize: '0.8125rem' }}>
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
