import { useState } from 'react';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { OwnerName } from '../components/Badge';
import { ownerOf } from '../lib/owners';
import { fdate, nf, plur, uniq } from '../lib/helpers';
import * as eventsApi from '../api/events';
import { apiErrorMessage } from '../api/client';
import { useFetch } from '../hooks/useFetch';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import NoAccessPage from './NoAccessPage';
import EventDrawer from './events/EventDrawer';
import EditEventModal from './events/EditEventModal';
import NewEventModal from './events/NewEventModal';
import ImportWizard from '../components/ImportWizard';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';

export default function EventsPage() {
  const { canView, can, isAdmin } = useSession();
  const toast = useToast();
  const confirm = useConfirm();
  const { data: events, refetchQuiet: reloadEvents } = useFetch(eventsApi.list, [], { initialData: [] });
  const EVENTS = events || [];
  // The catalogue is also written by the webhook ingestion path and by the
  // importer, neither of which this browser initiates — so the page polls as well
  // as reacting to its own saves.
  const { refreshNow: refresh } = useLiveData(reloadEvents, { resources: ['events'] });
  // EventViewSet has declared bulk_update_fields (status, web bookings, location,
  // official name) since it was written; nothing in this page reached them.
  const bulk = useBulkUpdate('events', refresh);
  const [drawerEvent, setDrawerEvent] = useState(null);
  const [editEvent, setEditEvent] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  if (!canView('events')) return <NoAccessPage module="Events" />;

  return (
    <div className="gs-page">
      {/* `infinite` without `server`. eventsApi.list already walks every page up
          front via fetchAllPages, so the whole catalogue is in memory and
          scrolling reveals more of what is already there rather than fetching;
          there is no request per scroll, and the `opts` closures below keep
          seeing the full set, which is what makes the filter dropdowns list
          every real location, type and team rather than only the values that
          happen to be on screen. Bookings and Tickets pair `infinite` with
          `server` instead, because those tables are too large to hold at once;
          this one is the catalogue. */}
      <DataTable
        rows={EVENTS} noun="events" infinite pageSize={1000} defaultSort={{ key: 'event_date', dir: 'asc' }} searchPlaceholder="Search event or code…"
        select={can('update', 'events')}
        // No tab strip on this page to fold these into (see BookingsPage /
        // TicketCentralPage), so they ride on the table's own toolbar row
        // instead of a PageHead row of their own — one fewer row of height.
        extraToolbar={<>
          {can('create', 'events') ? <>
            <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button>
            <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New event</button>
          </> : null}
          {/* Outside the create gate on purpose: this button answers to the HP
              account, not to a module permission, and nesting it inside another
              check would make its audience the INTERSECTION of the two. */}
          {/* The second sentence was learned the hard way: the catalogue is what
              every importer resolves an Event Code against, so clearing it stops
              Paper Review, Proposal Submission and Booking imports from accepting
              ANY row — each one reports "no matching event" per row and nothing
              says why. Wiping events is not a self-contained action and the
              confirmation has to say so. */}
          <ClearAllButton noun="events" count={EVENTS.length}
            onClear={eventsApi.clearAll} onCleared={refresh}
            extra="Bookings are not deleted with the catalogue — they store their event as a text code, so they will survive with codes that no longer resolve to an event. Imports in Paper Review, Proposal Submission and Bookings will also reject every row until the catalogue is restored, because they match each row's Event Code against it." />
        </>}
        groups={[
          { key: 'ev', label: 'Event' }, { key: 'web', label: 'Web presence' }, { key: 'own', label: 'Team ownership' },
          { key: 'meta', label: 'Naming & metadata' }, { key: 'rel', label: 'Related & upcoming events' },
        ]}
        hiddenDefault={[]}
        cols={[
          { key: 'event_code', label: 'Internal Code', group: 'ev', cell: (v) => <span className="mono lnk">{v}</span> },
          // The family code every edition shares; (base code, year) is what the
          // Performance Matrix keys on. See backend/events/codes.py.
          { key: 'base_code', label: 'Base Code', group: 'ev', cell: (v) => <span className="mono">{v}</span>, opts: () => uniq(EVENTS.map((e) => e.base_code)) },
          { key: 'name', label: 'Official Event Name', group: 'ev', cls: 'st' },
          { key: 'event_date', label: 'Start Date', type: 'date', group: 'ev', cell: (v) => fdate(v) },
          { key: 'end_date', label: 'End Date', type: 'date', group: 'ev', cell: (v) => fdate(v) },
          { key: 'location', label: 'Location', group: 'ev', opts: () => uniq(EVENTS.map((e) => e.location)) },
          { key: 'year', label: 'Year', group: 'ev', num: true, opts: () => uniq(EVENTS.map((e) => e.year)) },
          { key: 'event_type', label: 'Event Type', group: 'ev', cell: (v) => <span className="tg bg-neutral">{v}</span>, opts: () => uniq(EVENTS.map((e) => e.event_type)) },
          { key: 'nearest_related', label: 'Nearest Related', group: 'ev', cell: (v) => <span className="mono">{v}</span> },
          { key: 'website_live_date', label: 'Website Live', type: 'date', group: 'ev', cell: (v) => fdate(v) },
          { key: 'sales_check', label: 'Sales Check', group: 'ev', cell: (v) => <span className={'bg bg-' + (v === 'Done' ? 'green' : v === 'Pending' ? 'amber' : 'blue')}><i />{v}</span>, opts: () => uniq(EVENTS.map((e) => e.sales_check)) },
          { key: 'website', label: 'Website', group: 'web', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v || '—'}</span> },
          { key: 'web_bookings_enabled', label: 'Web Bookings Enabled', group: 'web', opts: () => ['Yes', 'No'] },
          { key: 'vr1_status', label: 'VR1 Sent Status', group: 'web', opts: () => uniq(EVENTS.map((e) => e.vr1_status)) },
          // The owner columns read through ownerOf (lib/owners.js), so a column with
          // no value of its own shows the lead of the team that owns the role,
          // muted and attributed in its tooltip. Six of these seven are blank on
          // every event in the live data, which is why they were seven empty
          // columns before. SCA keeps a plain filter list: it is the one owner that
          // is genuinely per-event, so there is nothing for it to inherit and its
          // values are worth filtering on.
          { key: 'sales_team', label: 'SCA', group: 'own', opts: () => uniq(EVENTS.map((e) => e.sales_team)) },
          { key: 'sales_lead', label: 'Sales Team Leader', group: 'own', cell: (v, row) => <OwnerName owner={ownerOf(row, 'sales_lead')} /> },
          { key: 'tele_team', label: 'Telemarketing', group: 'own', cell: (v, row) => <OwnerName owner={ownerOf(row, 'tele_team')} /> },
          { key: 'mr_senior', label: 'Market Research Sr.', group: 'own', cell: (v, row) => <OwnerName owner={ownerOf(row, 'mr_senior')} /> },
          { key: 'mr_junior', label: 'Market Research Jr.', group: 'own', cell: (v, row) => <OwnerName owner={ownerOf(row, 'mr_junior')} /> },
          { key: 'spex_lead', label: 'SpEx Lead', group: 'own', cell: (v, row) => <OwnerName owner={ownerOf(row, 'spex_lead')} /> },
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
            </div>
            <div className="rc-m">
              <div><div className="l">Starts</div><div className="v">{fdate(r.event_date)}</div></div>
              <div><div className="l">Location</div><div className="v">{r.location}</div></div>
              <div><div className="l">Ends</div><div className="v">{fdate(r.end_date)}</div></div>
              <div><div className="l">Year</div><div className="v">{r.year || '—'}</div></div>
            </div>
          </div>
        )}
        onRow={(r) => setDrawerEvent(r)}
        bulkActions={(ids, { clear, total }) => (
          <div className="bulk">
            {/* The header checkbox selects every matching row. This table is
                in-memory, so "every match" is resolved locally out of the rows
                already held rather than fetched — but the count below still
                states what the buttons act on, and the "of N matching" tail
                appears only while a selection is a subset. */}
            <span className="n">{nf(ids.length)}</span> selected
            {total > ids.length ? <span className="dim" style={{ fontSize: 11 }}>&nbsp;of {nf(total)} matching</span> : null}
            <div className="sep" />
            <button className="btn btn-sm btn-p" onClick={() => bulk.open(ids, clear)}>
              <Icon name="edit" size={13} />Update field…
            </button>
            {/* isAdmin, not can('delete', 'events'): events/views.py bulk_delete is
                IsAdminRole, so showing this to a grid-granted deleter would offer
                an affordance the API then refuses. Select one row and this is a
                per-event delete; the modal keeps its own, which stays on the grid
                cell because DELETE /events/{id}/ does. Wrapped in try/catch — the
                response interceptor only acts on 401 (api/client.js), so a 403
                would otherwise close the dialog and do nothing visible. */}
            {isAdmin ? (
              <button className="btn btn-sm btn-d" onClick={async () => {
                const ok = await confirm({ title: 'Delete events?', sub: plur(ids.length, 'event') + ' will be permanently removed from the catalogue.', danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>This cannot be undone. Bookings are not deleted with them — they store their event as a text code, so they survive with codes that no longer resolve, and imports in Paper Review, Proposal Submission and Bookings will reject rows matching those codes.</p> });
                if (!ok) return;
                try {
                  // The toast reports what the SERVER deleted, not how many were
                  // asked for — those differ whenever a row is out of scope.
                  const res = await eventsApi.bulkRemove(ids);
                  clear(); refresh();
                  toast(plur(res.deleted, 'event') + ' deleted', 'ok');
                } catch (err) {
                  toast(apiErrorMessage(err, 'Could not delete those events.'), 'er');
                }
              }}><Icon name="trash" size={13} />Delete</button>
            ) : null}
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="event" totalMatching={EVENTS.length} />
      ) : null}

      {drawerEvent ? <EventDrawer event={drawerEvent} onClose={() => setDrawerEvent(null)} onEdit={setEditEvent} /> : null}
      {editEvent ? <EditEventModal event={editEvent} onClose={() => setEditEvent(null)} onSaved={refresh} /> : null}
      {newOpen ? <NewEventModal onClose={() => setNewOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <ImportWizard kind="events" onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </div>
  );
}
