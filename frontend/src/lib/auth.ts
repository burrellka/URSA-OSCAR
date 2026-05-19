// Auth state hook + helpers — Phase 6.4.
//
// Approach (per ADR-001: no global state library — fetch-then-useState):
//
//   useAuthState() is called once at the top of App.tsx and exposes:
//     - status: 'loading' | 'unbootstrapped' | 'unauthenticated' | 'authenticated'
//     - operator: the username (always "operator" for now)
//     - signOut(): logout + flip status back to 'unauthenticated'
//     - refresh(): re-poll bootstrap-status + session
//
// On mount we hit GET /auth/bootstrap-status (open). Then:
//   - bootstrapped=false                → 'unbootstrapped' (App routes to /setup)
//   - bootstrapped=true + /session 401  → 'unauthenticated' (App routes to /login)
//   - bootstrapped=true + /session 200  → 'authenticated' (render Layout)
//
// We do NOT poll /session continuously. The httpOnly cookie's 24h
// expiry is enforced server-side; if it lapses mid-session the next
// API call returns 401 and the global request() helper redirects to
// /login. That's the only signal we need.
//
// Why no React Context: a single hook called once in App handles
// everything; pages that need to trigger a refresh after login/setup
// call refresh() via the props that App passes down (or just rely on
// react-router's navigate() — the next API call will revalidate).

import { useCallback, useEffect, useState } from 'react';
import { api, ApiError, type AuthSessionResponse } from '../api/client';

export type AuthStatus =
  | 'loading'
  | 'unbootstrapped'
  | 'unauthenticated'
  | 'authenticated';

export interface AuthState {
  status: AuthStatus;
  session: AuthSessionResponse | null;
  /** Re-poll bootstrap-status + session. Call after a successful
   *  login/setup or after a logout. */
  refresh: () => Promise<void>;
  /** Hits POST /auth/logout. On success, flips to 'unauthenticated'. */
  signOut: () => Promise<void>;
}

/** Read sessionStorage.ursa_oscar_return_to and clear it. Used by the
 *  /login and /setup pages after a successful auth to send the
 *  operator back where they came from (the 401 interceptor saved the
 *  pre-login pathname there). */
export function consumeReturnTo(fallback = '/'): string {
  if (typeof window === 'undefined') return fallback;
  try {
    const v = sessionStorage.getItem('ursa_oscar_return_to');
    sessionStorage.removeItem('ursa_oscar_return_to');
    if (v && !v.startsWith('/login') && !v.startsWith('/setup')) return v;
  } catch { /* sessionStorage disabled — fall back */ }
  return fallback;
}

export function useAuthState(): AuthState {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [session, setSession] = useState<AuthSessionResponse | null>(null);

  const refresh = useCallback(async () => {
    try {
      const boot = await api.bootstrapStatus();
      if (!boot.bootstrapped) {
        setStatus('unbootstrapped');
        setSession(null);
        return;
      }
    } catch {
      // bootstrap-status is open; failing means the API is offline.
      // Show "loading" indefinitely; the operator's nav will surface
      // it via normal browser network errors.
      setStatus('loading');
      return;
    }

    try {
      const info = await api.sessionInfo();
      setSession(info);
      setStatus('authenticated');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        // NB: the global request() helper has already set
        // ursa_oscar_return_to; we just flip state so App routes to
        // /login.
        setSession(null);
        setStatus('unauthenticated');
        return;
      }
      // Any other error — treat as unauthenticated so the operator
      // can re-login rather than seeing a blank page.
      setSession(null);
      setStatus('unauthenticated');
    }
  }, []);

  const signOut = useCallback(async () => {
    try { await api.logout(); } catch { /* token may already be invalid */ }
    setSession(null);
    setStatus('unauthenticated');
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { status, session, refresh, signOut };
}
