import { useState } from 'react';
import { Tabs } from '../components/UI';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import WebhookLogs from './webhooks/Logs';
import WebhookKeys from './webhooks/Keys';
import { HP_USERNAME } from '../lib/constants';

/**
 * TWO TABS, TWO DIFFERENT AUDIENCES.
 *
 * Delivery logs are operational data and belong to the `webhooks` module like
 * any other page. API keys are the website's INGEST CREDENTIALS: the list shows
 * each key string in the clear, so reading the tab is enough to start posting
 * bookings into the CRM, and the row actions regenerate and disable keys a live
 * site is using. That surface answers to the HP account alone — matching
 * /api/webhooks/keys/ (accounts.permissions.IsHPAccount) and the Data API Keys
 * page, which is the same kind of thing.
 *
 * So the tab is built per session rather than declared as a constant. Hiding the
 * PANEL while leaving the tab in the strip would only advertise it and answer
 * 403 on the click.
 */
const LOGS_TAB = { id: '', label: 'Delivery logs' };
const KEYS_TAB = { id: 'keys', label: 'API keys' };

export default function WebhookLogsPage() {
  const { canView, user } = useSession();
  const [tab, setTab] = useState('');
  const isHP = user?.username === HP_USERNAME;

  if (!canView('webhooks')) return <NoAccessPage module="Webhooks" />;

  const tabs = isHP ? [LOGS_TAB, KEYS_TAB] : [LOGS_TAB];
  // `tab === 'keys' && isHP`, not just the tab id. The id is component state and
  // survives nothing, but reading it as the only condition would mean the panel
  // renders for anyone who ever reaches that value by another route.
  const showKeys = tab === 'keys' && isHP;

  return (
    <>
      {/* No PageHead — the title duplicated the breadcrumb and the description
          was all that remained, so the tab strip below is the whole head. Every
          other list page has since been brought to the same shape. */}
      <Tabs list={tabs} active={tab} onPick={setTab} />
      {showKeys ? <WebhookKeys /> : <WebhookLogs />}
    </>
  );
}
