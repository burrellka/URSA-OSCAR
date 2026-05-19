import { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import AboutModal from './AboutModal';
import {
  Calendar,
  Activity,
  BarChart3,
  ListChecks,
  Upload,
  Download,
  TrendingUp,
  ClipboardList,
  FileText,
  User,
  Settings as SettingsIcon,
  LogOut,
  KeyRound,
  BookOpen,
  Info,
  type LucideIcon,
} from 'lucide-react';
import type { AuthSessionResponse } from '../api/client';

const NAV: Array<{ to: string; label: string; Icon: LucideIcon; end?: boolean }> = [
  { to: '/', label: 'Overview', Icon: Calendar, end: true },
  { to: '/daily', label: 'Daily View', Icon: Activity },
  { to: '/statistics', label: 'Statistics', Icon: BarChart3 },
  { to: '/events', label: 'Events', Icon: ListChecks },
  { to: '/import', label: 'Import', Icon: Upload },
  // 0.9.7 — Export sits next to Import (symmetric data flow).
  { to: '/export', label: 'Export', Icon: Download },
  { to: '/trends', label: 'Trends', Icon: TrendingUp },
  // Phase 6 Ticket 6.3 — provider PDF reports.
  { to: '/reports', label: 'Reports', Icon: FileText },
  { to: '/logs', label: 'Manual Logs', Icon: ClipboardList },
  // Phase 3 Item 4B — Profile lives between Manual Logs and Settings.
  { to: '/profile', label: 'Profile', Icon: User },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
  // Phase 7 — in-app Help system. Pinned at the bottom of the nav
  // because most operators open it less often than the dashboards.
  { to: '/help', label: 'Help', Icon: BookOpen },
];

interface LayoutProps {
  session: AuthSessionResponse | null;
  onSignOut: () => Promise<void> | void;
}

export default function Layout({ session, onSignOut }: LayoutProps) {
  const navigate = useNavigate();
  // Phase 7.3 — About modal state. Opened from the sidebar footer's
  // info icon; closed via the X, ESC, or backdrop click.
  const [aboutOpen, setAboutOpen] = useState(false);

  async function handleSignOut() {
    await onSignOut();
    navigate('/login', { replace: true });
  }

  return (
    <div className="app-container">
      <AboutModal open={aboutOpen} onClose={() => setAboutOpen(false)} />
      <nav className="sidebar">
        <div className="logo">
          URSA-OSCAR
          <div
            style={{
              fontSize: '0.75em',
              fontWeight: 400,
              fontStyle: 'italic',
              color: 'var(--text-muted)',
              marginTop: '0.125rem',
              lineHeight: 1.2,
            }}
          >
            Unified Rest &amp; Somatic Analytics
          </div>
        </div>
        <div className="nav-links">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.625rem' }}>
                <Icon size={16} />
                {label}
              </span>
            </NavLink>
          ))}
        </div>

        {/* Phase 6.4 — operator | sign-out footer. Anchored to the
            bottom of the sidebar so it doesn't conflict with the
            scrolling nav links. */}
        {session && (
          <div
            style={{
              marginTop: 'auto',
              paddingTop: '0.75rem',
              borderTop: '1px solid var(--border-color)',
              fontSize: '0.8125rem',
              color: 'var(--text-secondary)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
          >
            <button
              type="button"
              onClick={() => navigate('/settings/account')}
              title="Account settings"
              style={{
                background: 'none',
                border: 'none',
                padding: '0.25rem 0.375rem',
                borderRadius: '6px',
                cursor: 'pointer',
                color: 'var(--text-primary)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.375rem',
                flex: 1,
              }}
            >
              <KeyRound size={14} />
              <span>{session.user}</span>
            </button>
            <button
              type="button"
              onClick={() => setAboutOpen(true)}
              title="About URSA-OSCAR"
              className="icon-btn"
              style={{ padding: '0.25rem 0.375rem' }}
            >
              <Info size={14} />
            </button>
            <button
              type="button"
              onClick={handleSignOut}
              title="Sign out"
              className="icon-btn"
              style={{ padding: '0.25rem 0.375rem' }}
            >
              <LogOut size={14} />
            </button>
          </div>
        )}
      </nav>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
