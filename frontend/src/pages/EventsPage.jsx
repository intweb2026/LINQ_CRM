import { useState } from 'react';
import { PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { EvBadge, Who } from '../components/Badge';
import { fdate, nf, uniq } from '../lib/helpers';
import { EVENT_STATUSES } from '../lib/constants';
import * as eventsApi from '../api/events';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import EventDrawer from './events/EventDrawer';
import EditEventModal from './events/EditEventModal';
import NewEventModal from './events/NewEventModal';
import ImportWizard from '../components/ImportWizard';

export default function EventsPage() {
  const { canView, can } = useSession();
  const { data: events, refetch } = useFetch(eventsApi.list, [], { initialData: [] });
  const EVENTS = events || [];
  const refresh = () => refetch();
  const [drawerEvent, setDrawerEvent] = useState(null);
  const [editEvent, setEditEvent] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  if (!canView('events')) return <NoAccessPage module="Events" />;

  return (
    <>
      <PageHead title="Events" sub="The catalogue with edition history and every team ownership column. Open an event for its edition breakdown and growth."
        actions={can('create', 'events') ? <>
          <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button>
          <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New event</button>
        </> : null} />

      <DataTable
        rows={EVENTS} noun="events" pageSize={50} defaultSort={{ key: 'event_date', dir: 'asc' }} searchPlaceholder="Search event or code…"
        groups={[
          { key: 'ev', label: 'Event' }, { key: 'web', label: 'Web presence' }, { key: 'own', label: 'Team ownership' },
          { key: 'meta', label: 'Naming & metadata' }, { key: 'rel', label: 'Related & upcoming events' },
        ]}
        hiddenDefault={[]}
        cols={[
          { key: 'event_code', label: 'Code', group: 'ev', cell: (v) => <span className="mono lnk">{v}</span> },
          { key: 'name', label: 'Official Event Name', group: 'ev', cls: 'st' },
          { key: 'status', label: 'Status', group: 'ev', cell: (v) => <EvBadge value={v} />, opts: () => EVENT_STATUSES },
          { key: 'event_date', label: 'Start Date', group: 'ev', cell: (v) => fdate(v) },
          { key: 'end_date', label: 'End Date', group: 'ev', cell: (v) => fdate(v) },
          { key: 'location', label: 'Location', group: 'ev', opts: () => uniq(EVENTS.map((e) => e.location)) },
          { key: 'edition', label: 'Edition', group: 'ev' },
          { key: 'event_type', label: 'Event Type', group: 'ev', cell: (v) => <span className="tg bg-neutral">{v}</span>, opts: () => uniq(EVENTS.map((e) => e.event_type)) },
          { key: 'capacity', label: 'Capacity', group: 'ev', num: true, cell: (v) => nf(v) },
          { key: 'web_bookings', label: 'Web Bookings', group: 'ev', num: true, cell: (v) => nf(v) },
          { key: 'nearest_related', label: 'Nearest Related', group: 'ev', cell: (v) => <span className="mono">{v}</span> },
          { key: 'website_live_date', label: 'Website Live', group: 'ev', cell: (v) => fdate(v) },
          { key: 'sales_check', label: 'Sales Check', group: 'ev', cell: (v) => <span className={'bg bg-' + (v === 'Done' ? 'green' : v === 'Pending' ? 'amber' : 'blue')}><i />{v}</span>, opts: () => uniq(EVENTS.map((e) => e.sales_check)) },
          { key: 'website', label: 'Website', group: 'web', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v || '—'}</span> },
          { key: 'web_bookings_enabled', label: 'Web Bookings Enabled', group: 'web', opts: () => ['Yes', 'No'] },
          { key: 'vr1_status', label: 'VR1 Sent Status', group: 'web', opts: () => uniq(EVENTS.map((e) => e.vr1_status)) },
          { key: 'sales_team', label: 'Sales Team', group: 'own', opts: () => uniq(EVENTS.map((e) => e.sales_team)) },
          { key: 'sales_lead', label: 'Sales Team Leader', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'speaker_team', label: 'Speaker Sales', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'tele_team', label: 'Telemarketing', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'mr_senior', label: 'Market Research Sr.', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'mr_junior', label: 'Market Research Jr.', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'spex_lead', label: 'SpEx Lead', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'event_mgmt', label: 'Event Management', group: 'own', cell: (v) => <Who name={v} /> },
          { key: 'email_marketing', label: 'Email Marketing Campaign', group: 'meta', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v}</span> },
          { key: 'email_marketing_name', label: 'Name for Email Marketing', group: 'meta' },
          { key: 'branding_name', label: 'Name for Branding', group: 'meta' },
          { key: 'annualisation', label: 'Annualisation', group: 'meta' },
          { key: 'date_format', label: 'Date Format', group: 'meta', cell: (v) => <span className="mono">{v}</span> },
          { key: 'related_event_1', label: 'Related Event 1', group: 'rel' },
          { key: 'related_event_2', label: 'Related Event 2', group: 'rel' },
          { key: 'related_event_3', label: 'Related Event 3', group: 'rel' },
          { key: 'upcoming_event_1', label: 'Upcoming Event 1', group: 'rel' },
          { key: 'upcoming_event_2', label: 'Upcoming Event 2', group: 'rel' },
          { key: 'upcoming_event_3', label: 'Upcoming Event 3', group: 'rel' },
        ]}
        card={(r) => (
          <div className="rc">
            <div className="rc-t">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mono" style={{ color: 'var(--t-600)', marginBottom: 2 }}>{r.event_code}</div>
                <div className="who-n" style={{ whiteSpace: 'normal' }}>{r.name}</div>
              </div>
              <EvBadge value={r.status} />
            </div>
            <div className="rc-m">
              <div><div className="l">Starts</div><div className="v">{fdate(r.event_date)}</div></div>
              <div><div className="l">Location</div><div className="v">{r.location}</div></div>
              <div><div className="l">Capacity</div><div className="v">{nf(r.capacity)}</div></div>
              <div><div className="l">Web bookings</div><div className="v">{nf(r.web_bookings)}</div></div>
            </div>
          </div>
        )}
        onRow={(r) => setDrawerEvent(r)}
      />

      {drawerEvent ? <EventDrawer event={drawerEvent} onClose={() => setDrawerEvent(null)} onEdit={setEditEvent} /> : null}
      {editEvent ? <EditEventModal event={editEvent} onClose={() => setEditEvent(null)} onSaved={refresh} /> : null}
      {newOpen ? <NewEventModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <ImportWizard kind="events" onClose={() => setImportOpen(false)} /> : null}
    </>
  );
}
