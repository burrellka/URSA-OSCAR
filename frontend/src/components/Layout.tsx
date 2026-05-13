import { NavLink, Outlet } from 'react-router-dom';
import {
  Calendar,
  Activity,
  BarChart3,
  ListChecks,
  Upload,
  TrendingUp,
  ClipboardList,
  Settings as SettingsIcon,
  type LucideIcon,
} from 'lucide-react';

const NAV: Array<{ to: string; label: string; Icon: LucideIcon; end?: boolean }> = [
  { to: '/', label: 'Overview', Icon: Calendar, end: true },
  { to: '/daily', label: 'Daily View', Icon: Activity },
  { to: '/statistics', label: 'Statistics', Icon: BarChart3 },
  { to: '/events', label: 'Events', Icon: ListChecks },
  { to: '/import', label: 'Import', Icon: Upload },
  { to: '/trends', label: 'Trends', Icon: TrendingUp },
  { to: '/logs', label: 'Manual Logs', Icon: ClipboardList },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
];

export default function Layout() {
  return (
    <div className="app-container">
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
      </nav>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
