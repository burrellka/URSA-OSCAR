import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import Daily from './pages/Daily';
import Statistics from './pages/Statistics';
import Events from './pages/Events';
import ImportPage from './pages/Import';
import ExportPage from './pages/Export';
import Reports from './pages/Reports';
import Trends from './pages/Trends';
import ManualLogs from './pages/ManualLogs';
import Profile from './pages/Profile';
import Settings from './pages/Settings';
import SettingsAi from './pages/SettingsAi';
import DataManagement from './pages/DataManagement';
import Setup from './pages/Setup';
import Login from './pages/Login';
import Account from './pages/Account';
import { useAuthState } from './lib/auth';

/**
 * Phase 6.4 — auth-gated routing.
 *
 * useAuthState() polls /auth/bootstrap-status + /auth/session once on
 * mount. The four resulting states drive the route tree:
 *
 *   loading          — initial fetch in flight; render a blank shell
 *   unbootstrapped   — /data/auth.json doesn't exist; route everything
 *                      to /setup so the operator can pick a password
 *   unauthenticated  — bootstrapped but no valid session; route
 *                      everything to /login (the 401 interceptor in
 *                      client.ts also force-redirects here)
 *   authenticated    — full app tree under <Layout>, plus /settings/account
 *
 * The global 401 interceptor in client.ts handles mid-session
 * expirations by saving the current pathname and navigating to /login.
 * The login page consumes that on success.
 */
export default function App() {
  const auth = useAuthState();

  return (
    <BrowserRouter>
      {auth.status === 'loading' && <LoadingShell />}

      {auth.status === 'unbootstrapped' && (
        <Routes>
          <Route
            path="/setup"
            element={<Setup onSuccess={auth.refresh} connection={auth.connection} />}
          />
          <Route path="*" element={<Navigate to="/setup" replace />} />
        </Routes>
      )}

      {auth.status === 'unauthenticated' && (
        <Routes>
          <Route
            path="/login"
            element={<Login onSuccess={auth.refresh} connection={auth.connection} />}
          />
          {/* If the user lands on /setup but the system is already
              bootstrapped, push them at /login — they can't re-setup. */}
          <Route path="/setup" element={<Navigate to="/login" replace />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      )}

      {auth.status === 'authenticated' && (
        <Routes>
          <Route
            path="/"
            element={<Layout session={auth.session} onSignOut={auth.signOut} />}
          >
            <Route index element={<Overview />} />
            <Route path="daily" element={<Daily />} />
            <Route path="daily/:date" element={<Daily />} />
            <Route path="statistics" element={<Statistics />} />
            <Route path="events" element={<Events />} />
            <Route path="import" element={<ImportPage />} />
            <Route path="export" element={<ExportPage />} />
            <Route path="trends" element={<Trends />} />
            <Route path="reports" element={<Reports />} />
            <Route path="logs" element={<ManualLogs />} />
            <Route path="profile" element={<Profile />} />
            <Route path="settings" element={<Settings />} />
            <Route path="settings/ai" element={<SettingsAi />} />
            <Route path="settings/account" element={<Account onAuthChanged={auth.refresh} />} />
            <Route path="data-management" element={<DataManagement />} />
            {/* If an authenticated user hits /login or /setup, send
                them home — they're already in. */}
            <Route path="login" element={<Navigate to="/" replace />} />
            <Route path="setup" element={<Navigate to="/" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      )}
    </BrowserRouter>
  );
}

function LoadingShell() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-muted)',
        fontStyle: 'italic',
      }}
    >
      Loading URSA-OSCAR…
    </div>
  );
}
