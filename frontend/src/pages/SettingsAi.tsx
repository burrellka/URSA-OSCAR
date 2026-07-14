/**
 * Phase 5 Ticket 2D — AI Assistant configuration page.
 *
 * Route: /settings/ai. Five sections per the work order:
 *   1. Status                — enabled/disabled + current provider/model
 *   2. Provider Selection    — dropdown loaded from /api/v1/ai/providers
 *   3. Configuration         — key, endpoint, model (conditional fields)
 *   4. System Prompt         — editable template + per-provider override
 *   5. Notes                 — privacy + tool-calling caveats
 *
 * Keys never round-trip — the server returns api_key_set boolean only.
 * Save is a single PATCH; the field-edit state is local until save.
 *
 * 0.9.10 — System Prompt section gains "Restore from template" and
 * "Save to template" buttons. The template itself is a file-backed,
 * operator-editable artifact at /data/system_prompt_template.txt.
 * The instruction field below pre-populates from the template when
 * the per-provider override (cfg.custom_system_prompt) is empty —
 * so operators always see what's actually active, never a blank box
 * masking an invisible default.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, CheckCircle2, RefreshCw, RotateCcw, Save, XCircle, Loader2 } from 'lucide-react';
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
    // 1.1.11 — operator timeout override as a string so we can show the
    // input empty when None (server-side default). Blank = None → use
    // family default (300s for local, 120s for cloud).
    timeout_seconds: string;
    // 1.1.14 — operator output-token override as a string (same pattern
    // as timeout_seconds). Blank = None → use family default (4000 for
    // local; the provider's own large default for cloud).
    max_output_tokens: string;
    api_key: string;      // empty if not editing
  }>({
    enabled: false,
    provider_id: '',
    model: '',
    endpoint_url: '',
    routing_mode: 'direct',
    proxy_endpoint_url: '',
    custom_system_prompt: '',
    timeout_seconds: '',
    max_output_tokens: '',
    api_key: '',
  });

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<AiTestResult | null>(null);

  // 0.9.10 — template store state. ``template`` is the current saved
  // content of /data/system_prompt_template.txt (or DEFAULT_TEMPLATE if
  // the file doesn't exist yet — distinguished by ``templateSource``).
  // The instruction field below uses this to pre-populate on load and
  // to power the "Restore from template" / "Save to template" buttons.
  const [template, setTemplate] = useState<string>('');
  const [templateSource, setTemplateSource] = useState<'default' | 'file'>('default');
  const [templateSaving, setTemplateSaving] = useState(false);
  const [templateActionMsg, setTemplateActionMsg] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.getAiConfig(),
      api.listAiProviders(),
      api.getSystemPromptTemplate(),
    ])
      .then(([cfg, p, tmpl]) => {
        setConfig(cfg);
        setProviders(p.providers);
        setTemplate(tmpl.template);
        setTemplateSource(tmpl.source);
        setEdit({
          enabled: cfg.enabled,
          provider_id: cfg.provider_id || '',
          model: cfg.model || '',
          endpoint_url: cfg.endpoint_url || '',
          routing_mode: cfg.routing_mode || 'direct',
          proxy_endpoint_url: cfg.proxy_endpoint_url || '',
          // 0.9.10 — pre-populate the instruction field with the saved
          // template when no per-provider override exists. Operators
          // see exactly what the AI is using; no invisible defaults.
          custom_system_prompt: cfg.custom_system_prompt || tmpl.template,
          // 1.1.11 — empty string when the operator hasn't explicitly
          // set a timeout; effective_timeout_seconds still gets shown
          // as the placeholder so they know what will actually apply.
          timeout_seconds:
            cfg.timeout_seconds != null ? String(cfg.timeout_seconds) : '',
          // 1.1.14 — empty when the operator hasn't set a cap; the
          // effective value is shown as the placeholder.
          max_output_tokens:
            cfg.max_output_tokens != null ? String(cfg.max_output_tokens) : '',
          api_key: '',
        });
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  // 0.9.10 — Restore from template: discard the field's local edits and
  // reload the saved template content. Confirmed before destroying work.
  function onRestoreFromTemplate() {
    if (!confirm(
      "Restore the instruction field from the saved template?\n\n"
      + "This will replace whatever's currently in the instruction field "
      + "with the saved template content. Any unsaved local edits in the "
      + "field will be lost. The saved template itself is NOT modified.\n\n"
      + "Continue?",
    )) return;
    setEdit((prev) => ({ ...prev, custom_system_prompt: template }));
    setTemplateActionMsg('Instruction field reloaded from saved template.');
  }

  // 0.9.10 — Save to template: persist the instruction field's current
  // content as the new template. Affects every chat session that doesn't
  // have a per-provider override.
  async function onSaveToTemplate() {
    if (!confirm(
      "Save the current instruction field as the template?\n\n"
      + "This will OVERWRITE the saved template on the server. Every "
      + "future chat session that doesn't have a per-provider override "
      + "will use the new template you're about to save.\n\n"
      + "Continue?",
    )) return;
    setTemplateSaving(true);
    setError(null);
    setTemplateActionMsg(null);
    try {
      const result = await api.setSystemPromptTemplate(edit.custom_system_prompt);
      setTemplate(result.template);
      setTemplateSource(result.source);
      setTemplateActionMsg(`Template saved (${result.template.length} chars).`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTemplateSaving(false);
    }
  }

  // 0.11.1 — Reset to factory default: drop the saved template file
  // and reload the in-code DEFAULT_TEMPLATE that shipped with the
  // running API image. Useful after a new image release that ships
  // richer template content (added sections, refined guidance) when
  // the operator wants to adopt the upstream default rather than
  // stay forked on their old saved file.
  async function onResetToFactoryDefault() {
    if (!confirm(
      "Reset to the factory-default template?\n\n"
      + "This will DELETE your saved template on the server and reload "
      + "the built-in default that ships with the running API image. "
      + "Use this when a new release adds template sections you want "
      + "to adopt (e.g., new statistical-confidence or prediction-"
      + "surfacing guidance from a Phase 6 update).\n\n"
      + "Your per-provider override field is NOT changed by this — "
      + "after the reset, click Restore from template to also reload "
      + "the field, or Save to copy the factory default to your "
      + "active provider's prompt.\n\n"
      + "Continue?",
    )) return;
    setTemplateSaving(true);
    setError(null);
    setTemplateActionMsg(null);
    try {
      const result = await api.resetSystemPromptTemplateToDefault();
      setTemplate(result.template);
      setTemplateSource(result.source);
      setTemplateActionMsg(
        `Reset to factory default (${result.template.length} chars). `
        + `Click Restore from template to reload the instruction field too.`,
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTemplateSaving(false);
    }
  }

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
      // 1.1.11 — timeout: send a number when the field has content,
      // omit the field entirely when blank so the backend keeps its
      // current value (empty on both sides = "use default"). Range
      // guard: 5s-1800s, matching the pydantic constraint. Bad input
      // is caught client-side so the operator gets a clear message
      // instead of a 400 from the API.
      if (edit.timeout_seconds !== '') {
        const parsed = parseInt(edit.timeout_seconds, 10);
        if (!Number.isFinite(parsed) || parsed < 5 || parsed > 1800) {
          throw new Error(
            'Request timeout must be between 5 and 1800 seconds. '
            + 'Leave blank to use the provider-family default '
            + '(300s for local LLMs, 120s for cloud providers).',
          );
        }
        patch.timeout_seconds = parsed;
      }
      // 1.1.14 — max output tokens: same send-or-omit pattern. Range
      // guard 256-32000 matches the pydantic constraint.
      if (edit.max_output_tokens !== '') {
        const parsedMax = parseInt(edit.max_output_tokens, 10);
        if (!Number.isFinite(parsedMax) || parsedMax < 256 || parsedMax > 32000) {
          throw new Error(
            'Max output tokens must be between 256 and 32000. '
            + 'Leave blank to use the provider-family default '
            + '(4000 for local LLMs; the provider default for cloud).',
          );
        }
        patch.max_output_tokens = parsedMax;
      }
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

  // 1.1.2 — Test connection button now reflects the SAVED config, not
  // in-flight edits. The /test endpoint probes whatever the backend has
  // persisted; enabling the button on unsaved field changes invites a
  // confusing 400 ("no provider configured" or similar) when the operator
  // hasn't hit Save yet. Five disabled states, each with its own tooltip.
  // Local LLM presets with supports_local_routing=true skip the key
  // requirement because most local servers accept empty auth.
  const savedProviderId = config?.provider_id ?? null;
  const savedProviderMatches = savedProviderId === edit.provider_id;
  const requiresKey = !selectedPreset?.supports_local_routing;
  const testReady =
    !!savedProviderId &&
    savedProviderMatches &&
    (!requiresKey || keyAlreadyStored);
  const editsDiverged =
    !!savedProviderId && (
      edit.provider_id !== savedProviderId ||
      (edit.api_key ?? '') !== '' ||
      edit.model !== (config?.model ?? '') ||
      edit.endpoint_url !== (config?.endpoint_url ?? '')
    );
  const testTooltip =
    !savedProviderId
      ? 'Save a provider first'
      : !savedProviderMatches
      ? 'Save the selected provider first'
      : requiresKey && !keyAlreadyStored
      ? 'Save an API key first'
      : editsDiverged
      ? 'Save your changes first (test probes the saved config)'
      : 'Probe the saved provider';

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

          {/* 1.1.11 — operator-tunable request timeout. Blank = family
              default (300s for local LLMs, 120s for cloud). Range
              5-1800; validated client-side before PATCH so bad input
              gets a clear inline message instead of a 400 later. */}
          <div className="field" style={{ marginBottom: '0.75rem' }}>
            <label>Request timeout (seconds)</label>
            <input
              type="number"
              min={5}
              max={1800}
              step={5}
              value={edit.timeout_seconds}
              onChange={(e) => setEdit({ ...edit, timeout_seconds: e.target.value })}
              placeholder={
                config
                  ? `${config.effective_timeout_seconds}s (default)`
                  : ''
              }
            />
            <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
              How long URSA waits for the model to start responding before
              timing out. Leave blank to use the family default (300s for
              local LLMs, 120s for cloud providers). Raise this if
              thinking-mode local models on CPU need more time to warm up.
              Range 5-1800 seconds.
            </span>
          </div>

          {/* 1.1.14 — operator-tunable output-token cap. Blank = family
              default (4000 for local; the provider's own large default
              for cloud). Range 256-32000; validated client-side. */}
          <div className="field" style={{ marginBottom: '0.75rem' }}>
            <label>Max output tokens</label>
            <input
              type="number"
              min={256}
              max={32000}
              step={100}
              value={edit.max_output_tokens}
              onChange={(e) => setEdit({ ...edit, max_output_tokens: e.target.value })}
              placeholder={
                config
                  ? (config.effective_max_output_tokens != null
                      ? `${config.effective_max_output_tokens} (default)`
                      : 'provider default (uncapped)')
                  : ''
              }
            />
            <span className="stat-sub" style={{ color: 'var(--text-muted)' }}>
              The ceiling on how many tokens the model may generate per
              answer. Leave blank for the family default (4000 for local
              LLMs; the provider's own default for cloud). Reasoning-mode
              local models (Gemma-4, Qwen3, DeepSeek-R1) spend part of this
              budget on a hidden thinking channel before the answer starts,
              so if answers come back blank or cut off ("⚠ truncated" on the
              per-turn line), raise this. Range 256-32000.
            </span>
          </div>

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
              disabled={testing || !testReady || editsDiverged}
              title={testTooltip}
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

      {/* Section 4: System prompt + template (0.9.10) */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
          Instructions to AI
        </h2>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem', lineHeight: 1.5 }}>
          This is the system prompt the AI sees at the start of every chat.
          The proxy fills in your profile and device-clock context where
          the template references them via <code>{'{user_profile_summary}'}</code> and
          {' '}<code>{'{device_clock_description}'}</code>.
          {' '}
          {templateSource === 'default' ? (
            <>The field below was pre-populated with the built-in default
            template — you haven't saved your own template yet.</>
          ) : (
            <>The field below was pre-populated with your saved template.</>
          )}
          {' '}Edit the text directly to change what this provider sees, or use
          the buttons below to manage the saved template.
        </div>
        <textarea
          rows={14}
          value={edit.custom_system_prompt}
          onChange={(e) => {
            setEdit({ ...edit, custom_system_prompt: e.target.value });
            setTemplateActionMsg(null);
          }}
          style={{
            width: '100%',
            fontFamily: 'var(--font-mono, ui-monospace, monospace)',
            fontSize: '0.8125rem',
            lineHeight: 1.45,
          }}
        />
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          marginTop: '0.625rem',
          flexWrap: 'wrap',
        }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onRestoreFromTemplate}
            disabled={templateSaving}
            title="Replace the instruction field with the saved template content."
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <RotateCcw size={14} /> Restore from template
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onSaveToTemplate}
            disabled={templateSaving}
            title="Overwrite the saved template with whatever's currently in the instruction field."
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            {templateSaving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
            {templateSaving ? 'Saving…' : 'Save to template'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onResetToFactoryDefault}
            disabled={templateSaving}
            title={
              "Drop the saved template file on the server and revert to "
              + "the in-code DEFAULT_TEMPLATE that ships with the running "
              + "API image. Useful when a new release adds template content "
              + "you want to adopt."
            }
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}
          >
            <RefreshCw size={14} /> Reset to factory default
          </button>
          <span style={{
            fontSize: '0.75rem',
            color: 'var(--text-muted)',
            marginLeft: '0.25rem',
          }}>
            Template source: <strong>{templateSource === 'file' ? 'saved file' : 'built-in default'}</strong>
            {' · '}{template.length} chars
          </span>
          {templateActionMsg && (
            <span style={{
              fontSize: '0.75rem',
              color: 'var(--accent-primary, #2563eb)',
              marginLeft: '0.5rem',
            }}>
              {templateActionMsg}
            </span>
          )}
        </div>
        <div style={{
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          marginTop: '0.5rem',
          lineHeight: 1.5,
        }}>
          <strong>How the two persist differently:</strong> the <em>parent</em> "Save" button at the
          top saves THIS field as your per-provider instructions (only the
          currently selected provider uses it). "Save to template" saves it as
          the project-wide template — used by any future provider you set up
          who doesn't have their own per-provider override.
        </div>
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
