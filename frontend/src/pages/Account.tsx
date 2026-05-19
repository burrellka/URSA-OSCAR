// Settings -> Account page — Phase 6.4.
//
// Two operator workflows:
//
//   1. Change password
//      Form: current_password + new_password (>=12) + confirm. POST to
//      /auth/change-password. On 401, surface "current password
//      incorrect" inline. On success, refresh() the auth state so the
//      session-expiry display in the top-bar shows the new exp.
//
//   2. Generate an API token (90 days)
//      Button -> POST /auth/generate-api-token. The 90d JWT comes back
//      in the response body and is shown ONCE in a modal with a copy
//      button. The token is NOT persisted server-side; if the operator
//      doesn't copy it, they regenerate. We don't show old tokens —
//      there's no list endpoint by design (the token's signature IS
//      its validity).
//
// Session-expiry card is informational only: shows when the current
// session expires + a "Sign out" button alongside the top-bar one for
// discoverability.

import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, Key, Copy, Check, ArrowLeft, LogOut } from 'lucide-react';
import { api, ApiError, type AuthSessionResponse, type AuthTokenResponse } from '../api/client';

const MIN_PASSWORD_LENGTH = 12;

interface AccountProps {
  onAuthChanged: () => Promise<void> | void;
}

export default function Account({ onAuthChanged }: AccountProps) {
  const navigate = useNavigate();
  const [session, setSession] = useState<AuthSessionResponse | null>(null);
  const [sessionErr, setSessionErr] = useState<string | null>(null);

  useEffect(() => {
    api.sessionInfo()
      .then(setSession)
      .catch((e: ApiError) => setSessionErr(e.message));
  }, []);

  async function onSignOut() {
    try { await api.logout(); } catch { /* fine */ }
    await onAuthChanged();
    navigate('/login', { replace: true });
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Account</h1>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => navigate('/settings')}
          style={{ display: 'inline-flex', gap: '0.375rem', alignItems: 'center' }}
        >
          <ArrowLeft size={14} /> Back to Settings
        </button>
      </div>

      {/* --- Session info ------------------------------------------------- */}
      <div className="chart-card" style={{ marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
          Current session
        </h2>
        {sessionErr && <div className="error-banner">{sessionErr}</div>}
        {!session && !sessionErr && <div className="loading">Loading…</div>}
        {session && (
          <table className="data-table">
            <tbody>
              <tr><td style={labelCellStyle}>User</td><td><code>{session.user}</code></td></tr>
              <tr><td style={labelCellStyle}>Token kind</td><td>{session.token_kind}</td></tr>
              <tr>
                <td style={labelCellStyle}>Issued at</td>
                <td>{new Date(session.issued_at_iso).toLocaleString()}</td>
              </tr>
              <tr>
                <td style={labelCellStyle}>Expires</td>
                <td>
                  {new Date(session.expires_at_iso).toLocaleString()}{' '}
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
                    (in {formatDuration(session.expires_in_seconds)})
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={onSignOut}
          style={{ marginTop: '0.75rem', display: 'inline-flex', gap: '0.375rem', alignItems: 'center' }}
        >
          <LogOut size={14} /> Sign out
        </button>
      </div>

      {/* --- Change password --------------------------------------------- */}
      <ChangePasswordCard onChanged={onAuthChanged} />

      {/* --- API token generation ---------------------------------------- */}
      <GenerateTokenCard />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Change password
// ---------------------------------------------------------------------------

interface ChangePasswordCardProps {
  onChanged: () => Promise<void> | void;
}

function ChangePasswordCard({ onChanged }: ChangePasswordCardProps) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && confirm !== next;
  const canSubmit =
    current.length > 0 &&
    next.length >= MIN_PASSWORD_LENGTH &&
    confirm === next &&
    !busy;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    setSuccess(false);
    try {
      await api.changePassword(current, next);
      setCurrent('');
      setNext('');
      setConfirm('');
      setSuccess(true);
      await onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError('Current password is incorrect.');
      } else if (e instanceof ApiError) {
        setError(`Failed (${e.status}). ${describeBody(e.body)}`);
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chart-card" style={{ marginBottom: '1rem' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
        <Lock size={14} style={{ verticalAlign: -2, marginRight: '0.375rem' }} />
        Change password
      </h2>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
        Updates <code>/data/auth.json</code> with a fresh Argon2id hash
        and refreshes the session cookie. Active API tokens are
        unaffected (they validate against <code>URSA_OSCAR_JWT_SECRET</code>,
        not the password).
      </p>

      <form onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="current">Current password</label>
          <input
            id="current"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="next">New password</label>
          <input
            id="next"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            disabled={busy}
            placeholder={`Minimum ${MIN_PASSWORD_LENGTH} characters`}
          />
          {tooShort && (
            <div style={hintErrorStyle}>
              At least {MIN_PASSWORD_LENGTH} characters.
            </div>
          )}
        </div>
        <div className="field">
          <label htmlFor="confirm">Confirm new password</label>
          <input
            id="confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={busy}
          />
          {mismatch && (
            <div style={hintErrorStyle}>Doesn't match.</div>
          )}
        </div>

        {error && <div className="error-banner" style={{ marginBottom: '0.5rem' }}>{error}</div>}
        {success && (
          <div className="success-text" style={{ marginBottom: '0.5rem' }}>
            Password changed.
          </div>
        )}

        <button type="submit" className="btn-primary" disabled={!canSubmit}>
          {busy ? 'Changing…' : 'Change password'}
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// API token generation
// ---------------------------------------------------------------------------

function GenerateTokenCard() {
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<AuthTokenResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    setToken(null);
    setCopied(false);
    try {
      const r = await api.generateApiToken();
      setToken(r);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${describeBody(e.body)}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCopy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Clipboard copy failed. Select the text manually.');
    }
  }

  return (
    <div className="chart-card" style={{ marginBottom: '1rem' }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem' }}>
        <Key size={14} style={{ verticalAlign: -2, marginRight: '0.375rem' }} />
        API tokens
      </h2>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
        Long-lived (90 day) JWT for service-to-service auth — the
        watcher's <code>URSA_OSCAR_WATCHER_TOKEN</code> env var, the
        MCP server's bearer config, or any script that calls the API
        outside the browser. The token is shown <strong>once</strong>;
        if you lose it, generate a new one.
      </p>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
        Server does not store the token. Revoke by rotating{' '}
        <code>URSA_OSCAR_JWT_SECRET</code> (invalidates ALL tokens
        including your current browser session).
      </p>

      <button
        type="button"
        className="btn-primary"
        onClick={onGenerate}
        disabled={busy}
      >
        {busy ? 'Generating…' : 'Generate new API token (90 days)'}
      </button>

      {error && <div className="error-banner" style={{ marginTop: '0.75rem' }}>{error}</div>}

      {token && (
        <div className="modal-overlay" onClick={() => setToken(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '640px' }}>
            <h2>API token generated</h2>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
              Copy this token now and store it in your password manager
              or the watcher's env file. It cannot be retrieved again.
            </p>
            <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
              Expires: {new Date(token.expires_at_iso).toLocaleString()}
            </p>
            <textarea
              readOnly
              value={token.token}
              onFocus={(e) => e.currentTarget.select()}
              style={{
                width: '100%',
                minHeight: '7rem',
                fontFamily: 'var(--font-mono, ui-monospace)',
                fontSize: '0.75rem',
                wordBreak: 'break-all',
                resize: 'vertical',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.75rem' }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={onCopy}
                style={{ display: 'inline-flex', gap: '0.375rem', alignItems: 'center' }}
              >
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? 'Copied' : 'Copy to clipboard'}
              </button>
              <button type="button" className="btn-primary" onClick={() => setToken(null)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const labelCellStyle: React.CSSProperties = {
  color: 'var(--text-secondary)',
  whiteSpace: 'nowrap',
  width: '12rem',
};

const hintErrorStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  color: 'var(--status-bad)',
  marginTop: '0.25rem',
};

function formatDuration(seconds: number): string {
  if (seconds <= 0) return 'expired';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function describeBody(body: unknown): string {
  if (!body) return '';
  if (typeof body === 'string') return body;
  if (typeof body === 'object' && body && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    return typeof d === 'string' ? d : JSON.stringify(d);
  }
  return JSON.stringify(body);
}
