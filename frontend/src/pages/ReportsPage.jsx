import { useNavigate, useParams } from 'react-router-dom';
import { Tabs } from '../components/UI';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import ReportsOverview from './reports/Overview';
import ReportsGrowth from './reports/Growth';
import ReportsRegistry from './reports/Registry';
import ReportsDataPreview from './reports/DataPreview';
import ReportsLogs from './reports/Logs';

export default function ReportsPage() {
  const { canView, perms } = useSession();
  const nav = useNavigate();
  const { tab: subTab } = useParams();

  if (!canView('reports')) return <NoAccessPage module="Reports" />;

  // Growth/Registry/Data/Logs hit real /api/reports/* endpoints, all gated
  // server-side by IsAdminRole (is_admin OR custom_role.is_all_access) — match
  // that here instead of the legacy `role` field, which a custom "full access"
  // role wouldn't satisfy even though the backend would authorize the calls.
  const isAdmin = !!perms?.is_all_access;
  const TABS = [{ id: 'overview', label: 'Overview' }].concat(isAdmin ? [{ id: 'growth', label: 'Event Growth' }, { id: 'registry', label: 'Sheet Registry' }, { id: 'data', label: 'Report Data' }, { id: 'logs', label: 'Sync Logs' }] : []);
  const tab = TABS.some((t) => t.id === subTab) ? subTab : 'overview';

  return (
    <>
      {/* No PageHead: the title repeated the breadcrumb and the description was
          the only other thing in it, so the tabs lead the page. */}
      <Tabs list={TABS} active={tab} onPick={(id) => nav('/reports/' + id)} />
      {tab === 'overview' && <ReportsOverview />}
      {tab === 'growth' && <ReportsGrowth />}
      {tab === 'registry' && <ReportsRegistry />}
      {tab === 'data' && <ReportsDataPreview />}
      {tab === 'logs' && <ReportsLogs />}
    </>
  );
}
