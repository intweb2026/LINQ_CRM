import { useState } from 'react';
import { PageHead, Tabs } from '../components/UI';
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
      <PageHead title="Webhooks" sub="Inbound delivery logs from the booking websites, and the API keys that authenticate them." />
      <Tabs list={TABS} active={tab} onPick={setTab} />
      {tab === '' ? <WebhookLogs /> : <WebhookKeys />}
    </>
  );
}
