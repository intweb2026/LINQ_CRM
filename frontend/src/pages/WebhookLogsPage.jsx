import { useState } from 'react';
import { Tabs } from '../components/UI';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import WebhookLogs from './webhooks/Logs';
import WebhookKeys from './webhooks/Keys';

const TABS = [{ id: '', label: 'Delivery logs' }, { id: 'keys', label: 'API keys' }];

export default function WebhookLogsPage() {
  const { canView } = useSession();
  const [tab, setTab] = useState('');

  if (!canView('webhooks')) return <NoAccessPage module="Webhooks" />;

  return (
    <>
      {/* No PageHead — see ReportsPage: title duplicated the breadcrumb and the
          description was all that remained. */}
      <Tabs list={TABS} active={tab} onPick={setTab} />
      {tab === '' ? <WebhookLogs /> : <WebhookKeys />}
    </>
  );
}
