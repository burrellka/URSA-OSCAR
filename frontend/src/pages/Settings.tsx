import { useEffect, useState } from 'react';
import { api, ApiError, type SystemConfig, type VerifyMcpResult } from '../api/client';
import { CheckCircle2, XCircle, AlertTriangle, RefreshCw } from 'lucide-react';

/**
 * Settings page — read-only operational visibility.
 *
 * Architect decision (Phase 2 polish work order Item 5): NO write-side
 * secrets management in the web UI. Mutation happens in Dockge's env
 * editor + container recreate. Rationale: full UI-driven secrets
 * management requires (a) auth on the web UI itself, since
 * ursa-oscar-web:5063 is currently unauthenticated and anyone on the
 * LAN could rotate secrets, (b) a writable secrets persistence layer
 * with its own backup story, and (c) container restart orchestration
 * from the web UI. That's Phase 4+ scope.
 *
 * What ships here:
 *  - Configuration card: masked values from GET /api/v1/system/config.
 *    All masking is server-side; the browser never receives full
 *    secret values.
 *  - MCP Health Check card: button drives POST /api/v1/system/verify-mcp
 *    which runs the four checks from infra/verify-mcp-live.sh server-side.
 *  - Secrets Management notes: prose pointing at Docs/17-oauth-setup.md.
 *  - Future Consideration marker: surfaces the deferred UI-driven rotation.
 */
export default function Settings() {
  const [cfg, setCfg] = useState<SystemConfig | null>(null);
  const [cfgErr, setCfgErr] = useState<string | null>(null);

  const [verifyResult, setVerifyResult] = useState<VerifyMcpResult | null>(null);
  const [verifyRunning, setVerifyRunning] = useState(false);
  const [verifyErr, setVerifyErr] = useState<string | null>(null);

  useEffect(() => {
    api.getSystemConfig()
      .then(setCfg)
      .catch((e: ApiError) => setCfgErr(e.message));
  }, []);

  async function runVerify() {
    setVerifyRunning(true);
    setVerifyErr(null);
    setVerifyResult(null);
    try {
      const r = await api.verifyMcp();
      setVerifyResult(r);
    } catch (e) {
      setVerifyErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setVerifyRunning(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
      </div>

      {/* --- Configuration card -------------------------------------------- */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>Configuration</h2>
        {cfgErr && <div className="error-banner">{cfgErr}</div>}
        {!cfg && !cfgErr && <div className="loading">Loading…</div>}
        {cfg && (
          <table className="data-table">
            <tbody>
              <ConfigRow label="MCP Base URL" value={cfg.mcp.base_url ?? '—'} />
              <ConfigRow
                label="MCP Bearer Token"
                value={cfg.mcp.bearer_token_masked ?? '—'}
                code
              />
              <ConfigRow
                label="MCP OAuth Client ID"
                value={cfg.mcp.oauth_client_id_masked ?? '—'}
                code
              />
              <ConfigRow
                label="MCP OAuth Client Secret"
                value={cfg.mcp.oauth_client_secret.set ? '••• (set)' : 'not set'}
                muted={!cfg.mcp.oauth_client_secret.set}
              />
              <ConfigRow label="API URL (internal)" value={cfg.api.internal_url} code />
              <ConfigRow label="Database path" value={cfg.api.db_path} code />
              <ConfigRow
                label="Database size"
                value={cfg.api.db_size_bytes === null ? '—' : formatBytes(cfg.api.db_size_bytes)}
              />
              <ConfigRow label="API image version" value={`brain40/ursa-oscar-api:${cfg.images.api}`} code />
              <ConfigRow label="MCP image version" value={cfg.images.mcp ? `brain40/ursa-oscar-mcp:${cfg.images.mcp}` : 'unknown'} code />
              <ConfigRow label="Web image version" value={cfg.images.web ? `brain40/ursa-oscar-web:${cfg.images.web}` : 'unknown'} code />
              <ConfigRow label="Watcher image version" value={cfg.images.watcher ? `brain40/ursa-oscar-watcher:${cfg.images.watcher}` : 'unknown'} code />
            </tbody>
          </table>
        )}
      </div>

      {/* --- MCP Health Check ---------------------------------------------- */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>MCP Health Check</h2>
          <button
            type="button"
            className="btn-primary"
            onClick={runVerify}
            disabled={verifyRunning}
            style={{ display: 'inline-flex', gap: '0.375rem', alignItems: 'center' }}
          >
            <RefreshCw size={14} className={verifyRunning ? 'spin' : undefined} />
            {verifyRunning ? 'Running…' : 'Verify MCP Connectivity'}
          </button>
        </div>

        {verifyErr && <div className="error-banner">{verifyErr}</div>}

        {!verifyResult && !verifyErr && !verifyRunning && (
          <div className="stat-sub" style={{ color: 'var(--text-muted)' }}>
            Click the button to run the four standard checks from{' '}
            <code>infra/verify-mcp-live.sh</code> against the MCP container.
          </div>
        )}

        {verifyResult && (
          <>
            <div style={{ marginBottom: '0.5rem' }}>
              <span
                className={`status-pill ${verifyResult.all_passed ? 'good' : 'bad'}`}
                style={{ marginRight: '0.5rem' }}
              >
                {verifyResult.all_passed ? 'All checks passed' : 'One or more checks failed'}
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
                ran at {new Date(verifyResult.ran_at).toLocaleString()}
              </span>
            </div>
            <table className="data-table">
              <tbody>
                {verifyResult.checks.map((c, i) => (
                  <tr key={i}>
                    <td style={{ width: '1.5rem' }}>
                      {c.status === 'pass' ? (
                        <CheckCircle2 size={16} color="var(--ahi-good, #16a34a)" />
                      ) : c.status === 'fail' ? (
                        <XCircle size={16} color="var(--ahi-bad, #dc2626)" />
                      ) : (
                        <AlertTriangle size={16} color="var(--ahi-warn, #d97706)" />
                      )}
                    </td>
                    <td>{c.name}</td>
                    <td style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono, ui-monospace)', fontSize: '0.8125rem' }}>
                      {c.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      {/* --- Secrets Management notes -------------------------------------- */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>Secrets Management</h2>
        <p style={{ marginBottom: '0.5rem' }}>
          Secrets are managed via Docker environment variables. To rotate the bearer token,
          OAuth client ID, or OAuth client secret:
        </p>
        <ol style={{ marginLeft: '1.25rem', marginBottom: '0.75rem' }}>
          <li>Open Dockge → <code>ursa-oscar</code> stack → <code>ursa-oscar-mcp</code> service → Edit env</li>
          <li>
            Generate new values:
            <pre style={{ margin: '0.375rem 0', padding: '0.5rem', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '0.8125rem' }}>
{`python -c "import secrets; print(secrets.token_urlsafe(32))"`}
            </pre>
          </li>
          <li>Save and recreate the container</li>
          <li>
            If you rotated OAuth credentials, re-register the connector in claude.ai with the
            new <code>client_id</code> + <code>client_secret</code>
          </li>
        </ol>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          Full rotation procedure: see <code>Docs/17-oauth-setup.md</code>.
        </p>
      </div>

      {/* --- Future Consideration marker ----------------------------------- */}
      <div
        style={{
          fontSize: '0.8125rem',
          color: 'var(--text-muted)',
          fontStyle: 'italic',
          textAlign: 'center',
          padding: '0.5rem',
        }}
      >
        Future enhancement: full secrets management with web UI rotation. Deferred to Phase 4+
        due to security scope (requires web UI auth layer, secrets persistence, container
        orchestration). Architect decision in <code>Docs/URSA-OSCAR_Design.md</code> v1.2 +
        the Phase 2 polish work order.
      </div>
    </div>
  );
}

function ConfigRow({
  label, value, code, muted,
}: { label: string; value: string; code?: boolean; muted?: boolean }) {
  return (
    <tr>
      <td style={{ color: 'var(--text-secondary)', whiteSpace: 'nowrap', width: '14rem' }}>
        {label}
      </td>
      <td style={{ color: muted ? 'var(--text-muted)' : undefined }}>
        {code ? <code>{value}</code> : value}
      </td>
    </tr>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
