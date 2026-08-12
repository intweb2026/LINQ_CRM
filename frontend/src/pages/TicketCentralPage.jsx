import { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageHead, Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { TkBadge, PriBadge, Who } from '../components/Badge';
import { fdate, fmy, nf, plur } from '../lib/helpers';
import { TK_STATUS, TK_PRIORITY } from '../lib/constants';
import * as ticketsApi from '../api/tickets';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';
import TicketDrawer from './tickets/TicketDrawer';
import NewTicketModal from './tickets/NewTicketModal';
import ImportWizard from '../components/ImportWizard';

const tkCols = () => [
  { key: 'status', serverField: 'status', serverOrdering: 'status', label: 'Status', group: 'id', cell: (v) => <TkBadge value={v} />, opts: () => Object.keys(TK_STATUS) },
  { key: 'ticket_number', serverField: 'ticket_number', label: 'Ticket #', group: 'id', cell: (v) => <span className="mono lnk">{v}</span> },
  { key: 'created_at', label: 'Created', group: 'id', serverOrdering: 'created_at', cell: (v) => fdate(v) },
  { key: 'purpose', serverField: 'purpose', label: 'Purpose', group: 'mr' },
  { key: 'type_of_ticket', serverField: 'type_of_ticket', label: 'Type of Ticket', group: 'mr' },
  { key: 'competitor_event_name', serverField: 'competitor_event_name', label: 'Competitor Event', group: 'mr' },
  { key: 'organizer', serverField: 'organizer', label: 'Organizer', group: 'mr' },
  { key: 'event_month_year', serverField: 'event_month_year', label: 'Event Month/Year', group: 'mr', cell: (v) => fmy(v) },
  { key: 'event_location', serverField: 'event_location', label: 'Event Location', group: 'mr' },
  { key: 'source_event', label: 'Source Event', group: 'mr', cell: (v) => <span className="mono" style={{ color: 'var(--t-600)' }}>{v}</span> },
  { key: 'relationship', serverField: 'relationship', label: 'Relationship', group: 'mr', cell: (v) => <span className="tg bg-neutral">{v}</span> },
  { key: 'priority', serverField: 'priority', serverOrdering: 'priority', label: 'Priority', group: 'mr', cell: (v) => <PriBadge value={v} />, opts: () => Object.keys(TK_PRIORITY), editOpts: Object.keys(TK_PRIORITY), onEdit: (r, v) => ticketsApi.update(r.id, { priority: v }) },
  { key: 'estimate', serverField: 'estimate', label: 'Estimate', group: 'mr', num: true, cell: (v) => nf(v) },
  { key: 'assigned_mr', serverField: 'assigned_mr', label: 'Assigned MR', group: 'mr', cell: (v) => <Who name={v} /> },
  { key: 'link_url', serverField: 'link_url', label: 'Link URL', group: 'mr', cell: (v) => <a href="#" onClick={(e) => e.preventDefault()} style={{ fontSize: 11.5 }}>{v}</a> },
  { key: 'linkedin_keywords', serverField: 'linkedin_keywords', label: 'LinkedIn Keywords', group: 'mr' },
  { key: 'duplicate_tickets', serverField: 'duplicate_tickets', label: 'Duplicate Tickets', group: 'mr', cell: (v) => (v === '—' ? <span className="dim">—</span> : <span className="mono" style={{ color: 'var(--amber)' }}>{v}</span>) },
  { key: 'mr_comments', serverField: 'mr_comments', label: 'MR Comments', group: 'mr' },
  { key: 'assign_name', serverField: 'assign_name', label: 'Assign Name', group: 'dm', cell: (v) => (v === '—' ? <span className="dim">unassigned</span> : <Who name={v} />) },
  { key: 'assign_date', serverField: 'assign_date', label: 'Assign Date', group: 'dm', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'ticket_type', serverField: 'ticket_type', label: 'Ticket Type', group: 'dm', cell: (v) => <span className="tg bg-neutral">{v}</span> },
  { key: 'actual_number', serverField: 'actual_number', label: 'Actual Number', group: 'dm', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'new_contacts_created', serverField: 'new_contacts_created', label: 'New Contacts', group: 'dm', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'mined_count', serverField: 'mined_count', label: 'Mined Count', group: 'dm', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : <b style={{ color: 'var(--text)' }}>{nf(v)}</b>) },
  { key: 'complete_date', serverField: 'complete_date', label: 'Complete Date', group: 'dm', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'hubspot_entry_date', serverField: 'hubspot_entry_date', label: 'HubSpot Entry', group: 'dm', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'dm_comments', serverField: 'dm_comments', label: 'DM Comments', group: 'dm' },
  { key: 'assign_name_lx2', serverField: 'assign_name_lx2', label: 'Assign (LX-2)', group: 'lx', cell: (v) => (v === '—' ? <span className="dim">—</span> : <Who name={v} />) },
  { key: 'actual_count_lx2', serverField: 'actual_count_lx2', label: 'Count (LX-2)', group: 'lx', num: true, cell: (v) => (v == null ? <span className="dim">—</span> : nf(v)) },
  { key: 'complete_date_lx2', serverField: 'complete_date_lx2', label: 'Complete (LX-2)', group: 'lx', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'dm_comments_lx2', serverField: 'dm_comments_lx2', label: 'DM Comments (LX-2)', group: 'lx' },
];

export default function TicketCentralPage() {
  const { canView, can, user } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const { tab: subTab } = useParams();
  // No ticketsApi.list() here any more: that was a fetchAllPages walk of 35,690
  // rows (~72 sequential requests) before the table could render a single row.
  // DataTable now pages against the server. Tab counts already came from
  // tickets/stats/, which is a real aggregate.
  const { data: stats, refetch: refetchStats } = useFetch(ticketsApi.stats, [], { initialData: {} });
  const [tableRefetch, setTableRefetch] = useState(null);
  // Wrapped in an updater: React treats a bare function passed to a state setter
  // as an updater and would call it instead of storing it.
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);
  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    refetchStats().catch(() => {});
  }, [tableRefetch, refetchStats]);
  const [drawerTicket, setDrawerTicket] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  if (!canView('ticket_central')) return <NoAccessPage module="Ticket Central" />;

  const S = stats || {};
  const TABS = [
    { id: '', label: 'All tickets', count: S.total }, { id: 'draft', label: 'Draft', count: S.draft },
    { id: 'mr_submitted', label: 'MR Submitted', count: S.mr_submitted }, { id: 'completed', label: 'Completed', count: S.completed },
    { id: 'returned', label: 'Returned', count: S.returned },
  ];
  const tab = TABS.some((t) => t.id === subTab) ? subTab : '';
  const isMR = user.role === 'market_research' || user.role === 'admin';
  // `status` is a registered filter_spec field on TicketViewSet, so the tab is
  // evaluated by the database over all 35,690 rows rather than over one page.
  const serverCriteria = tab ? [{ field: 'status', op: 'is', value: tab }] : null;

  return (
    <>
      <PageHead title="Ticket Central" sub="Market Research raises tickets, Data Mining works the queue. Priority edits inline; use a ticket to move it through the workflow."
        actions={<>
          {user.role === 'admin' ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Smart import</button> : null}
          {isMR && can('create', 'ticket_central') ? <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New ticket</button> : null}
        </>} />

      <Tabs list={TABS} active={tab} onPick={(id) => nav('/tickets' + (id ? '/' + id : ''))} />

      <DataTable
        tableId="tickets"
        server={{ resource: 'tickets', mapRow: ticketsApi.fromApi }}
        serverCriteria={serverCriteria}
        onServerReady={keepRefetch}
        noun="tickets" select={can('update', 'ticket_central')} infinite pageSize={50}
        defaultSort={{ key: 'created_at', dir: 'desc' }} searchPlaceholder="Search ticket, organizer, keywords…"
        groups={[{ key: 'id', label: 'Identifier' }, { key: 'mr', label: 'MR Section' }, { key: 'dm', label: 'DMD Section' }, { key: 'lx', label: 'LX-2 Second Pass' }]}
        hiddenDefault={['link_url', 'linkedin_keywords', 'duplicate_tickets', 'mr_comments', 'event_location', 'hubspot_entry_date', 'dm_comments', 'assign_name_lx2', 'actual_count_lx2', 'complete_date_lx2', 'dm_comments_lx2', 'new_contacts_created']}
        cols={tkCols()}
        onRow={(r) => setDrawerTicket(r)}
        bulkActions={(ids, { clear }) => (
          <div className="bulk">
            <span className="n">{ids.length}</span> selected<div className="sep" />
            <button className="btn btn-sm btn-p" onClick={async () => { const n = await ticketsApi.bulkSubmit(ids); clear(); refresh(); toast(n ? plur(n, 'ticket') + ' submitted to Data Mining' : 'Only draft tickets can be submitted', n ? 'ok' : 'wn'); }}><Icon name="send" size={13} />Submit to DMD</button>
            <button className="btn btn-sm btn-s" onClick={() => toast('Exporting ' + plur(ids.length, 'ticket') + '…', 'nf')}><Icon name="download" size={13} />Export</button>
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {drawerTicket ? <TicketDrawer ticket={drawerTicket} onClose={() => setDrawerTicket(null)} onChanged={refresh} /> : null}
      {newOpen ? <NewTicketModal onClose={() => setNewOpen(false)} onCreated={refresh} /> : null}
      {importOpen ? <ImportWizard kind="tickets" onClose={() => setImportOpen(false)} /> : null}
    </>
  );
}
