// First-run bootstrap page — Phase 6.4.
//
// Shown when GET /auth/bootstrap-status returns { bootstrapped: false }.
// Submits to POST /auth/bootstrap, which:
//   - creates /data/auth.json with Argon2id-hashed password (mode 0600)
//   - sets the session cookie
//   - returns the session token in the body (unused here; cookie is enough)
//
// UI rules (per work order Item 4 + Decision 4):
//   - Two password fields ("Set password" + "Confirm password"), both
//     min 12 chars. Confirm must match.
//   - Show a clear warning: no recovery, no email, choose carefully.
//   - On success, refresh() the auth state and navigate to / (or the
//     return-to in sessionStorage).
//   - 409 surfaces as "already bootstrapped; refresh the page" since
//     someone else completed setup in another tab.

import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, AlertTriangle } from 'lucide-react';
import { api, ApiError } from '../api/client';
import { consumeReturnTo } from '../lib/auth';

const MIN_PASSWORD_LENGTH = 12;

interface SetupProps {
  onSuccess: () => Promise<void> | void;
}

export default function Setup({ onSuccess }: SetupProps) {
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const passwordTooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && confirm !== password;
  const canSubmit =
    password.length >= MIN_PASSWORD_LENGTH &&
    confirm === password &&
    !busy;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await api.bootstrap(password);
      await onSuccess();
      const to = consumeReturnTo('/');
      navigate(to, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError(
          'URSA-OSCAR is already bootstrapped. Refresh the page and sign in instead.',
        );
      } else if (e instanceof ApiError) {
        setError(`Setup failed (${e.status}). ${describeBody(e.body)}`);
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={authShellStyle}>
      <div className="chart-card" style={authCardStyle}>
        <AuthBrandHeader />

        <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '0.25rem' }}>
          <Lock size={16} style={{ verticalAlign: -2, marginRight: '0.375rem' }} />
          Set the operator password
        </h2>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          First-run setup. This password protects all CPAP data on this
          server. There is one user (<code>operator</code>) and no
          password recovery.
        </p>

        <div
          style={{
            display: 'flex',
            gap: '0.5rem',
            alignItems: 'flex-start',
            padding: '0.625rem 0.75rem',
            background: 'var(--status-warn-soft)',
            border: '1px solid rgba(217,119,6,0.25)',
            borderRadius: '6px',
            marginBottom: '1rem',
            fontSize: '0.8125rem',
            color: '#92400e',
          }}
        >
          <AlertTriangle size={16} style={{ marginTop: '0.125rem', flexShrink: 0 }} />
          <span>
            <strong>No recovery.</strong> If you forget this password,
            the only way back in is to SSH to the host and delete{' '}
            <code>/data/auth.json</code>. Choose something durable and
            store it in your password manager.
          </span>
        </div>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="new-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
              placeholder={`Minimum ${MIN_PASSWORD_LENGTH} characters`}
            />
            {passwordTooShort && (
              <div style={hintErrorStyle}>
                Password must be at least {MIN_PASSWORD_LENGTH} characters.
              </div>
            )}
          </div>

          <div className="field">
            <label htmlFor="confirm">Confirm password</label>
            <input
              id="confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={busy}
            />
            {mismatch && (
              <div style={hintErrorStyle}>Passwords don't match.</div>
            )}
          </div>

          {error && <div className="error-banner" style={{ marginBottom: '0.75rem' }}>{error}</div>}

          <button
            type="submit"
            className="btn-primary"
            disabled={!canSubmit}
            style={{ width: '100%' }}
          >
            {busy ? 'Setting password…' : 'Create operator account'}
          </button>
        </form>
      </div>
    </div>
  );
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

// ---------------------------------------------------------------------------
// Shared header — Setup + Login both render this so branding stays
// consistent. The "Built on OSCAR analytics engine" tagline credits
// the upstream OSCAR project whose ResMed SDXX file format work this
// codebase builds on; it's a permanent piece of the auth-screen brand.
// ---------------------------------------------------------------------------

export function AuthBrandHeader() {
  return (
    <div style={{ marginBottom: '1.25rem', textAlign: 'center' }}>
      <div style={authLogoStyle}>URSA-OSCAR</div>
      <div style={authSubtitleStyle}>
        Unified Rest &amp; Somatic Analytics
      </div>
      <div style={authAttributionStyle}>
        Built on OSCAR analytics engine
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared styles — also used by Login.tsx so the two pages match.
// ---------------------------------------------------------------------------

export const authShellStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'var(--bg-primary)',
  padding: '1rem',
};

export const authCardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: '420px',
  padding: '1.5rem 1.75rem',
};

export const authLogoStyle: React.CSSProperties = {
  fontSize: '1.5rem',
  fontWeight: 700,
  color: 'var(--text-primary)',
};

export const authSubtitleStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  fontStyle: 'italic',
  color: 'var(--text-muted)',
  marginTop: '0.125rem',
};

export const authAttributionStyle: React.CSSProperties = {
  fontSize: '0.6875rem',
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: 'var(--text-muted)',
  marginTop: '0.625rem',
  fontWeight: 500,
};

export const hintErrorStyle: React.CSSProperties = {
  fontSize: '0.8125rem',
  color: 'var(--status-bad)',
  marginTop: '0.25rem',
};
