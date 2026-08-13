import { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ExtLink, PageHead, Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { TkBadge, PriBadge, Who } from '../components/Badge';
import { fdate, fmy, nf, plur } from '../lib/helpers';
import { TK_STATUS, TK_PRIORITY, TK_TYPES, TK_TICKET_TYPES, TK_RELATIONSHIPS } from '../lib/constants';
import * as ticketsApi from '../api/tickets';
import { useFetch } from '../hooks/useFetch';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';
import TicketFormModal from './tickets/TicketFormModal';
import ImportWizard from '../components/ImportWizard';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';

const dim = (v) => (v == null || v === '' || v === '—' ? <span className="dim">—</span> : null);
const person = (v) => dim(v) || <Who name={v} />;
const num = (v) => dim(v) || nf(v);
const day = (v) => dim(v) || fdate(v);

/**
 * Columns in the order Ticket Central presents them, which is the order of the
 * Zoho report this module replaces: when it lands, Added Time first, the MR brief,
 * then the DMD result, then the import provenance and the system stamps.
 *
 * `status` is deliberately LAST. It led the table before, which spent the first
 * (and on a narrow screen, only) column on a value the tab strip above already
 * filters by, pushing the ticket number and the brief off-screen.
 *
 * Ordering is server-side, so a column is sortable only where TicketViewSet
 * .ordering_fields has a term for it — id, created_at, updated_at, status,
 * priority. `serverField` is likewise present only where filter_spec accepts the
 * field: id, the timestamps and the four provenance columns are excluded there by
 * accounts/filter_spec.DEFAULT_EXCLUDES, so filtering those narrows the rows
 * already loaded and DataTable says so rather than implying a full-table result.
 */
const tkCols = () => [
  { key: 'created_at', label: 'Added Time', group: 'rec', serverOrdering: 'created_at', cell: (v) => fdate(v) },
  { key: 'link_url', serverField: 'link_url', label: 'Link URL', group: 'mr', cell: (v) => <ExtLink value={v} /> },
  { key: 'linkedin_keywords', serverField: 'linkedin_keywords', label: 'LinkedIn Keywords', group: 'mr' },
  { key: 'duplicate_tickets', serverField: 'duplicate_tickets', label: 'Duplicate Tickets', group: 'mr', cell: (v) => dim(v) || <span className="mono" style={{ color: 'var(--amber)' }}>{v}</span> },
  { key: 'ticket_number', serverField: 'ticket_number', label: 'Ticket Number', group: 'rec', cell: (v) => dim(v) || <span className="mono lnk">{v}</span> },
  { key: 'type_of_ticket', serverField: 'type_of_ticket', label: 'Type of Ticket', group: 'mr', opts: () => TK_TYPES },
  { key: 'purpose', serverField: 'purpose', label: 'Purpose', group: 'mr' },
  { key: 'priority', serverField: 'priority', serverOrdering: 'priority', label: 'Priority', group: 'mr', cell: (v) => <PriBadge value={v} />, opts: () => Object.keys(TK_PRIORITY), editOpts: Object.keys(TK_PRIORITY), onEdit: (r, v) => ticketsApi.update(r.id, { priority: v }) },
  { key: 'estimate', serverField: 'estimate', label: 'Estimate', group: 'mr', num: true, cell: num },
  { key: 'mr_comments', serverField: 'mr_comments', label: 'MR Comments', group: 'mr' },
  { key: 'ticket_type', serverField: 'ticket_type', label: 'Ticket Type', group: 'dm', cell: (v) => dim(v) || <span className="tg bg-neutral">{v}</span>, opts: () => TK_TICKET_TYPES },
  { key: 'assign_date', serverField: 'assign_date', label: 'Assign Date', group: 'dm', cell: day },
  { key: 'assign_name', serverField: 'assign_name', label: 'Assign Name', group: 'dm', cell: person },
  { key: 'actual_number', serverField: 'actual_number', label: 'Actual Number', group: 'dm', num: true, cell: num },
  { key: 'new_contacts_created', serverField: 'new_contacts_created', label: 'New Contacts Created', group: 'dm', num: true, cell: num },
  { key: 'mined_count', serverField: 'mined_count', label: 'Mined Count', group: 'dm', num: true, cell: (v) => dim(v) || <b style={{ color: 'var(--text)' }}>{nf(v)}</b> },
  { key: 'hubspot_entry_date', serverField: 'hubspot_entry_date', label: 'HubSpot Entry Date', group: 'dm', cell: day },
  { key: 'assign_name_lx2', serverField: 'assign_name_lx2', label: 'Assign Name (LX-2)', group: 'lx', cell: person },
  { key: 'complete_date', serverField: 'complete_date', label: 'Complete Date', group: 'dm', cell: day },
  { key: 'complete_date_lx2', serverField: 'complete_date_lx2', label: 'Complete Date (LX2)', group: 'lx', cell: day },
  { key: 'dm_comments', serverField: 'dm_comments', label: 'DM Comments', group: 'dm' },
  { key: 'dm_comments_lx2', serverField: 'dm_comments_lx2', label: 'DM Comments (LX-2)', group: 'lx' },
  { key: 'source_spreadsheet_id', label: 'Source_Spreadsheet_ID', group: 'dm', cell: (v) => dim(v) || <span className="mono">{v}</span> },
  { key: 'source_tab', label: 'Source_Tab', group: 'dm' },
  { key: 'source_row_number', label: 'Source_Row_Number', group: 'dm', num: true, cell: num },
  { key: 'idempotency_key', label: 'Idempotency_Key', group: 'dm', cell: (v) => dim(v) || <span className="mono">{v}</span> },
  { key: 'updated_at', label: 'Modified Time', group: 'rec', serverOrdering: 'updated_at', cell: (v) => fdate(v) },
  { key: 'id', label: 'ID', group: 'rec', serverOrdering: 'id', num: true, cell: (v) => <span className="mono">{v}</span> },
  { key: 'assigned_mr', serverField: 'assigned_mr', label: 'Assigned MR', group: 'mr', cell: person },
  { key: 'added_user_text', serverField: 'added_user_text', label: 'Added User', group: 'rec' },
  // Off the Zoho report, so hidden by default rather than dropped — they are real
  // MR fields and the Columns menu turns them back on.
  { key: 'competitor_event_name', serverField: 'competitor_event_name', label: 'Competitor Event', group: 'mr' },
  { key: 'organizer', serverField: 'organizer', label: 'Organizer', group: 'mr' },
  { key: 'event_month_year', serverField: 'event_month_year', label: 'Event Month/Year', group: 'mr', cell: (v) => dim(v) || fmy(v) },
  { key: 'event_location', serverField: 'event_location', label: 'Event Location', group: 'mr' },
  { key: 'relationship', serverField: 'relationship', label: 'Relationship', group: 'mr', cell: (v) => dim(v) || <span className="tg bg-neutral">{v}</span>, opts: () => TK_RELATIONSHIPS },
  { key: 'actual_count_lx2', serverField: 'actual_count_lx2', label: 'Actual Count (LX-2)', group: 'lx', num: true, cell: num },
  { key: 'status', serverField: 'status', serverOrdering: 'status', label: 'Status', group: 'rec', cell: (v) => <TkBadge value={v} />, opts: () => Object.keys(TK_STATUS) },
];

const HIDDEN_DEFAULT = ['competitor_event_name', 'organizer', 'event_month_year', 'event_location', 'relationship', 'actual_count_lx2'];

export default function TicketCentralPage() {
  const { canView, can, user } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const { tab: subTab } = useParams();
  // No ticketsApi.list() here any more: that was a fetchAllPages walk of 35,690
  // rows (~72 sequential requests) before the table could render a single row.
  // DataTable now pages against the server. Tab counts already came from
  // tickets/stats/, which is a real aggregate.
  const { data: stats, refetchQuiet: reloadStats } = useFetch(ticketsApi.stats, [], { initialData: {} });
  const [tableRefetch, setTableRefetch] = useState(null);
  // Wrapped in an updater: React treats a bare function passed to a state setter
  // as an updater and would call it instead of storing it.
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);
  // The tab counts are their own aggregate, so they need their own subscription —
  // the table looks after its rows (see DataTable's liveReload). Both move when
  // anything writes tickets/, including a colleague moving one through the
  // workflow from their own browser.
  const { refreshNow: refreshStats } = useLiveData(reloadStats, { resources: ['tickets'] });
  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    refreshStats();
  }, [tableRefetch, refreshStats]);
  const bulk = useBulkUpdate('tickets', refresh);
  // null = closed; a row = edit that ticket; NEW = the add form. Same component
  // either way, so the two layouts cannot drift apart.
  const [formTicket, setFormTicket] = useState(null);
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
      <PageHead title="Ticket Central" sub="Market Research raises tickets, Data Mining works the queue. Open a ticket to edit it and move it through the workflow."
        actions={<>
          {user.role === 'admin' ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Smart import</button> : null}
          {isMR && can('create', 'ticket_central') ? <button className="btn btn-p" onClick={() => setFormTicket('NEW')}><Icon name="plus" size={15} />New ticket</button> : null}
          {/* HP only — ClearAllButton renders nothing for anyone else, mirroring
              IsHPAccount on tickets/clear_all/. The endpoint already existed; there
              was simply no way to reach it from the UI. */}
          <ClearAllButton noun="tickets" count={S.total}
            onClear={ticketsApi.clearAll} onCleared={refresh}
            extra="The ticket-number sequences are reset with it, so the next ticket raised numbers from the start again." />
        </>} />

      <Tabs list={TABS} active={tab} onPick={(id) => nav('/tickets' + (id ? '/' + id : ''))} />

      <DataTable
        // Renamed from 'tickets' on purpose. Column visibility is persisted per
        // tableId (localStorage), and every user who had opened this page carried
        // a stored `hidden` set from the OLD default — which hid twelve of the
        // columns this order exists to show, so the new layout would have been
        // invisible to exactly the people already using it.
        tableId="tickets.v2"
        server={{ resource: 'tickets', mapRow: ticketsApi.fromApi }}
        serverCriteria={serverCriteria}
        onServerReady={keepRefetch}
        noun="tickets" select={can('update', 'ticket_central')} infinite pageSize={50}
        defaultSort={{ key: 'created_at', dir: 'desc' }} searchPlaceholder="Search ticket, organizer, keywords…"
        groups={[{ key: 'rec', label: 'Record' }, { key: 'mr', label: 'Ticket Hub (MR)' }, { key: 'dm', label: 'For DMD' }, { key: 'lx', label: 'LX-2 Second Pass' }]}
        hiddenDefault={HIDDEN_DEFAULT}
        cols={tkCols()}
        // The Priority column declares editOpts. DataTable now renders an in-cell
        // editor only where the page says the viewer may write, so this has to be
        // passed explicitly or the column goes read-only.
        canEdit={can('update', 'ticket_central')}
        onRow={(r) => setFormTicket(r)}
        bulkActions={(ids, { clear }) => (
          <div className="bulk">
            <span className="n">{ids.length}</span> selected<div className="sep" />
            {/* TicketViewSet has declared bulk_update_fields all along — priority,
                type of ticket, the DMD assignment columns — and nothing here
                reached them, so editing many tickets meant opening each one. */}
            <button className="btn btn-sm btn-p" onClick={() => bulk.open(ids, clear)}>
              <Icon name="edit" size={13} />Update field…
            </button>
            <button className="btn btn-sm btn-s" onClick={async () => { const n = await ticketsApi.bulkSubmit(ids); clear(); refresh(); toast(n ? plur(n, 'ticket') + ' submitted to Data Mining' : 'Only draft tickets can be submitted', n ? 'ok' : 'wn'); }}><Icon name="send" size={13} />Submit to DMD</button>
            <button className="btn btn-sm btn-s" onClick={() => toast('Exporting ' + plur(ids.length, 'ticket') + '…', 'nf')}><Icon name="download" size={13} />Export</button>
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="ticket" totalMatching={S.total} />
      ) : null}

      {formTicket ? (
        <TicketFormModal
          ticket={formTicket === 'NEW' ? null : formTicket}
          onClose={() => setFormTicket(null)}
          onSaved={refresh}
        />
      ) : null}
      {importOpen ? <ImportWizard kind="tickets" onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
