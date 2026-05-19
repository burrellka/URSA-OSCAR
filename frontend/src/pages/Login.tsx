// Login page — Phase 6.4.
//
// Shown when bootstrap-status=true but /auth/session returns 401, or
// when the global 401 interceptor force-redirects here. Submits to
// POST /auth/login.
//
// Rate-limit (Decision 9): 5 failures per IP per 15 min returns 429.
// We surface the server's Retry-After-derived message verbatim.
//
// Return-to: the 401 interceptor saves window.location.pathname to
// sessionStorage.ursa_oscar_return_to before redirecting. We consume
// it after a successful login.

import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock } from 'lucide-react';
import { api, ApiError, type ConnectionDiagnostic } from '../api/client';
import {
  consumeReturnTo,
  // shared visual primitives from Setup.tsx so the two pages match.
} from '../lib/auth';
import {
  authShellStyle,
  authCardStyle,
  AuthBrandHeader,
  ConnectionWarning,
} from './Setup';

interface LoginProps {
  onSuccess: () => Promise<void> | void;
  connection: ConnectionDiagnostic | null;
}

export default function Login({ onSuccess, connection }: LoginProps) {
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy || !password) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      await onSuccess();
      const to = consumeReturnTo('/');
      navigate(to, { replace: true });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 429) {
          setError(describeBody(e.body) || 'Too many failed attempts. Wait and try again.');
        } else if (e.status === 401) {
          setError('Incorrect password.');
        } else if (e.status === 409) {
          // Not bootstrapped yet — race with a setup wipe. Punt the
          // operator at /setup.
          navigate('/setup', { replace: true });
          return;
        } else {
          setError(`Login failed (${e.status}). ${describeBody(e.body)}`);
        }
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

        <ConnectionWarning connection={connection} />

        <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '0.25rem' }}>
          <Lock size={16} style={{ verticalAlign: -2, marginRight: '0.375rem' }} />
          Sign in
        </h2>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          Operator account. Enter the password you set at first-run setup.
        </p>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="username">User</label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              value="operator"
              readOnly
              disabled
              style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </div>

          {error && <div className="error-banner" style={{ marginBottom: '0.75rem' }}>{error}</div>}

          <button
            type="submit"
            className="btn-primary"
            disabled={busy || !password}
            style={{ width: '100%' }}
          >
            {busy ? 'Signing in…' : 'Sign in'}
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
