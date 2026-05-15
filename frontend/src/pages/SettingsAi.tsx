/**
 * Phase 5 Ticket 2D — AI Assistant configuration page.
 *
 * Route: /settings/ai. Five sections per the work order:
 *   1. Status                — enabled/disabled + current provider/model
 *   2. Provider Selection    — dropdown loaded from /api/v1/ai/providers
 *   3. Configuration         — key, endpoint, model (conditional fields)
 *   4. System Prompt         — read-only default or operator-customized
 *   5. Notes                 — privacy + tool-calling caveats
 *
 * Keys never round-trip — the server returns api_key_set boolean only.
 * Save is a single PATCH; the field-edit state is local until save.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { AiMaskedConfig, AiProviderPreset, AiTestResult } from '../api/types';


export default function SettingsAi() {
  const [config, setConfig] = useState<AiMaskedConfig | null>(null);
  const [providers, setProviders] = useState<AiProviderPreset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Edit-form state (separate from server state so save is explicit).
  const [edit, setEdit] = useState<{
    enabled: boolean;
    provider_id: string;
    model: string;
    endpoint_url: string;
    routing_mode: string;
    proxy_endpoint_url: string;
    custom_system_prompt: string;
    api_key: string;      // empty if not editing
  }>({
    enabled: false,
    provider_id: '',
    model: '',
    endpoint_url: '',
    routing_mode: 'direct',
    proxy_endpoint_url: '',
    custom_system_prompt: '',
    api_key: '',
  });

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<AiTestResult | null>(null);

  useEffect(() => {
    Promise.all([api.getAiConfig(), api.listAiProviders()])
      .then(([cfg, p]) => {
        setConfig(cfg);
        setProviders(p.providers);
        setEdit({
          enabled: cfg.enabled,
          provider_id: cfg.provider_id || '',
          model: cfg.model || '',
          endpoint_url: cfg.endpoint_url || '',
          routing_mode: cfg.routing_mode || 'direct',
          proxy_endpoint_url: cfg.proxy_endpoint_url || '',
          custom_system_prompt: cfg.custom_system_prompt || '',
          api_key: '',
        });
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const selectedPreset = providers.find((p) => p.id === edit.provider_id);

  function onProviderChange(provider_id: string) {
    const preset = providers.find((p) => p.id === provider_id);
    setEdit({
      ...edit,
      provider_id,
      // Auto-populate endpoint + first model from the new preset.
      endpoint_url: preset?.default_endpoint || '',
      model: preset?.default_models?.[0] || '',
      api_key: '',  // clear any in-flight key edit on provider change
    });
    setTestResult(null);
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    setTestResult(null);
    try {
      const patch: Record<string, unknown> = {
        enabled: edit.enabled,
        provider_id: edit.provider_id || null,
        model: edit.model,
        endpoint_url: edit.endpoint_url,
        routing_mode: edit.routing_mode,
        proxy_endpoint_url: edit.proxy_endpoint_url || null,
        custom_system_prompt: edit.custom_system_prompt || null,
      };
      if (edit.api_key) patch.api_key = edit.api_key;
      const updated = await api.patchAiConfig(patch);
      setConfig(updated);
      setEdit((e) => ({ ...e, api_key: '' }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function onTest() {
    if (!edit.provider_id) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testAiProvider(edit.provider_id);
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <div className="loading">Loading AI Assistant settings…</div>;

  const keyAlreadyStored = !!(
    edit.provider_id && config?.api_keys_set?.[edit.provider_id]
  );

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">
          <Bot size={20} style={{ display: 'inline', marginRight: '0.5rem', verticalAlign: '-3px' }} />
          AI Assistant
        </h1>
        <Link to="/settings" className="btn-secondary">← Settings</Link>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Section 1: Status */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>Status</h2>
        <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', fontSize: '0.875rem' }}>
          <div>
            <div className="stat-label">AI Assistant</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {config?.enabled ? 'Enabled' : 'Disabled'}
            </div>
          </div>
          <div>
            <div className="stat-label">Provider</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {config?.provider_id || '—'}
            </div>
          </div>
          <div>
            <div className="stat-label">Model</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {config?.model || '—'}
            </div>
          </div>
        </div>
      </div>

      {/* Section 2: Provider selection */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
          Provider
        </h2>
        <div className="field" style={{ marginBottom: '0.75rem' }}>
          <label>Provider</label>
          <select
            value={edit.provider_id}
            onChange={(e) => onProviderChange(e.target.value)}
          >
            <option value="">— Disabled —</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          {selectedPreset && (
            <div style={{
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              marginTop: '0.375rem',
              lineHeight: 1.4,
            }}>
              {selectedPreset.notes}
            </div>
          )}
        </div>

        <div className="field" style={{ marginBottom: '0.75rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <input
              type="checkbox"
              checked={edit.enabled}
              onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })}
            />
            Enable AI Assistant
          </label>
        </div>
      </div>

      {/* Section 3: Configuration (conditional on provider) */}
      {edit.provider_id && selectedPreset && (
        <div className="chart-card" style={{ marginBottom: '1rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
            Configuration
          </h2>

          <div className="field" style={{ marginBottom: '0.75rem' }}>
            <label>
              API key
              {keyAlreadyStored && (
                <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                  {' '}— stored (enter new value to replace)
                </span>
              )}
            </label>
            <input
              type="password"
              value={edit.api_key}
              onChange={(e) => setEdit({ ...edit, api_key: e.target.value })}
              placeholder={keyAlreadyStored ? '••••••••••' : 'Paste your API key here'}
              autoComplete="off"
              spellCheck={false}
            />
            <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
              Stored encrypted at rest. Never sent back to the browser.
            </span>
          </div>

          <div className="field" style={{ marginBottom: '0.75rem' }}>
            <label>Endpoint URL</label>
            <input
              type="text"
              value={edit.endpoint_url}
              onChange={(e) => setEdit({ ...edit, endpoint_url: e.target.value })}
              placeholder={selectedPreset.default_endpoint || 'https://your-endpoint/v1'}
            />
          </div>

          <div className="field" style={{ marginBottom: '0.75rem' }}>
            <label>Model</label>
            {selectedPreset.default_models.length > 0 ? (
              <input
                type="text"
                list={`models-${selectedPreset.id}`}
                value={edit.model}
                onChange={(e) => setEdit({ ...edit, model: e.target.value })}
              />
            ) : (
              <input
                type="text"
                value={edit.model}
                onChange={(e) => setEdit({ ...edit, model: e.target.value })}
                placeholder="model-id"
              />
            )}
            <datalist id={`models-${selectedPreset.id}`}>
              {selectedPreset.default_models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>

          {selectedPreset.supports_local_routing && (
            <>
              <div className="field" style={{ marginBottom: '0.75rem' }}>
                <label>Local LLM routing</label>
                <div style={{ display: 'flex', gap: '1rem', fontSize: '0.875rem' }}>
                  <label style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center' }}>
                    <input
                      type="radio"
                      name="routing_mode"
                      value="direct"
                      checked={edit.routing_mode === 'direct'}
                      onChange={() => setEdit({ ...edit, routing_mode: 'direct' })}
                    />
                    Direct
                  </label>
                  <label style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center' }}>
                    <input
                      type="radio"
                      name="routing_mode"
                      value="proxy"
                      checked={edit.routing_mode === 'proxy'}
                      onChange={() => setEdit({ ...edit, routing_mode: 'proxy' })}
                    />
                    Through proxy / RAG layer
                  </label>
                </div>
                <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
                  "Through proxy" lets requests flow through a RAG wrapper
                  (LocalRecall, etc.) on the way to the inference engine.
                </span>
              </div>
              {edit.routing_mode === 'proxy' && (
                <div className="field" style={{ marginBottom: '0.75rem' }}>
                  <label>Proxy URL</label>
                  <input
                    type="text"
                    value={edit.proxy_endpoint_url}
                    onChange={(e) => setEdit({ ...edit, proxy_endpoint_url: e.target.value })}
                    placeholder="http://localrecall:8080/v1"
                  />
                </div>
              )}
            </>
          )}

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.75rem' }}>
            <button
              type="button"
              className="btn-primary"
              onClick={onSave}
              disabled={saving}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={onTest}
              disabled={testing || (!keyAlreadyStored && !edit.api_key)}
              title={(!keyAlreadyStored && !edit.api_key) ? 'Save an API key first' : 'Probe the provider with a 1-token request'}
            >
              {testing ? <Loader2 size={14} className="spin" /> : 'Test connection'}
            </button>
            {testResult && (
              <span style={{
                fontSize: '0.8125rem',
                color: testResult.ok ? 'var(--accent-success, #16a34a)' : 'var(--ahi-bad, #dc2626)',
                display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
              }}>
                {testResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                {testResult.ok
                  ? `OK — ${(testResult.model_info as Record<string, unknown> | undefined)?.model || 'connected'}`
                  : (testResult.error || 'failed')}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Section 4: System prompt */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
          System prompt
        </h2>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
          The AI proxy fills in your profile + device-clock context into the
          template at session start. Custom override below — leave blank to
          use the default.
        </div>
        <textarea
          rows={6}
          value={edit.custom_system_prompt}
          onChange={(e) => setEdit({ ...edit, custom_system_prompt: e.target.value })}
          placeholder="(empty = use default template — works for most users)"
          style={{ width: '100%', fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: '0.8125rem' }}
        />
      </div>

      {/* Section 5: Notes */}
      <div className="chart-card" style={{ marginBottom: '1rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-primary)' }}>
          Privacy & limitations
        </h2>
        <ul style={{ paddingLeft: '1.2rem', display: 'flex', flexDirection: 'column', gap: '0.4rem', lineHeight: 1.45 }}>
          <li>Conversations are stored <strong>only in your browser</strong> (localStorage, per Daily View date). They are not saved server-side.</li>
          <li>Tool calls execute against your URSA-OSCAR data. The AI sees only what tools return; it cannot access your DuckDB directly.</li>
          <li>If you use a cloud provider, your conversations are processed per that provider's terms. If you use a Local LLM, conversations stay on your local network.</li>
          <li>Small models (under 7B parameters) may struggle with multi-step queries. For best results, use Claude Sonnet 4.5+, GPT-4o, Gemini 1.5 Pro+, or a local model 14B+.</li>
          <li>Tool-calling reliability varies by provider and model. If responses don't use tool data when you expect them to, try a different model or provider.</li>
        </ul>
      </div>
    </div>
  );
}
