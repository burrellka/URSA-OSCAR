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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
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
          <Route path="data-management" element={<DataManagement />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
