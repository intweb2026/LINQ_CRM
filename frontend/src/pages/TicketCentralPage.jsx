import { useCallback, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ExtLink, Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { TkBadge, PriBadge, Who } from '../components/Badge';
import { fdate, ftime, fmy, nf, plur } from '../lib/helpers';
import { TK_STATUS, TK_PRIORITY, TK_TYPES, TK_TICKET_TYPES, TK_RELATIONSHIPS } from '../lib/constants';
import * as ticketsApi from '../api/tickets';
import { useFetch } from '../hooks/useFetch';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useConfirm } from '../context/ConfirmContext';
import { apiErrorMessage } from '../api/client';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';
import TicketFormModal from './tickets/TicketFormModal';
import TicketEntryRows from './tickets/TicketEntryRows';
import ImportWizard from '../components/ImportWizard';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';

const dim = (v) => (v == null || v === '' || v === '—' ? <span className="dim">—</span> : null);
const person = (v) => dim(v) || <Who name={v} avatar={false} />;
const num = (v) => dim(v) || nf(v);
const day = (v) => dim(v) || fdate(v);
/** Added Time / Modified Time — a timestamp, so it shows the time too. */
const stamp = (v) => dim(v) || <span className="dim">{fdate(v)} {ftime(v)}</span>;

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
  // Date AND time, the way Bookings renders the same two columns. Date alone made
  // an edit invisible for the rest of the day it was made on, which reads as
  // "Modified Time is not updating".
  //
  // This is also the table's default sort, ASCENDING — see defaultSort below.
  // The Duplicate Tickets column that used to sit next in this list is gone with
  // its database column; a repeated link is flagged while it is being typed.
  { key: 'created_at', serverField: 'created_at', label: 'Added Time', type: 'date', group: 'rec', serverOrdering: 'created_at', cell: (v) => stamp(v) },
  { key: 'link_url', serverField: 'link_url', label: 'Link URL', group: 'mr', cell: (v) => <ExtLink value={v} /> },
  { key: 'linkedin_keywords', serverField: 'linkedin_keywords', label: 'LinkedIn Keywords', group: 'mr' },
  { key: 'ticket_number', serverField: 'ticket_number', label: 'Ticket Number', group: 'rec', cell: (v) => dim(v) || <span className="mono lnk">{v}</span> },
  { key: 'type_of_ticket', serverField: 'type_of_ticket', label: 'Type of Ticket', group: 'mr', opts: () => TK_TYPES },
  { key: 'purpose', serverField: 'purpose', label: 'Purpose', group: 'mr' },
  { key: 'priority', serverField: 'priority', serverOrdering: 'priority', label: 'Priority', group: 'mr', cell: (v) => <PriBadge value={v} />, opts: () => Object.keys(TK_PRIORITY), editOpts: Object.keys(TK_PRIORITY), onEdit: (r, v) => ticketsApi.update(r.id, { priority: v }) },
  { key: 'estimate', serverField: 'estimate', label: 'Estimate', group: 'mr', num: true, cell: num },
  { key: 'mr_comments', serverField: 'mr_comments', label: 'MR Comments', group: 'mr' },
  { key: 'ticket_type', serverField: 'ticket_type', label: 'Ticket Type', group: 'dm', cell: (v) => dim(v) || <span className="tg bg-neutral">{v}</span>, opts: () => TK_TICKET_TYPES },
  { key: 'assign_date', serverField: 'assign_date', label: 'Assign Date', type: 'date', group: 'dm', cell: day },
  { key: 'assign_name', serverField: 'assign_name', label: 'Assign Name', group: 'dm', cell: person },
  { key: 'actual_number', serverField: 'actual_number', label: 'Actual Number', group: 'dm', num: true, cell: num },
  { key: 'new_contacts_created', serverField: 'new_contacts_created', label: 'New Contacts Created', group: 'dm', num: true, cell: num },
  { key: 'mined_count', serverField: 'mined_count', label: 'Mined Count', group: 'dm', num: true, cell: (v) => dim(v) || <b style={{ color: 'var(--text)' }}>{nf(v)}</b> },
  { key: 'hubspot_entry_date', serverField: 'hubspot_entry_date', label: 'HubSpot Entry Date', type: 'date', group: 'dm', cell: day },
  { key: 'assign_name_lx2', serverField: 'assign_name_lx2', label: 'Assign Name (LX-2)', group: 'lx', cell: person },
  { key: 'complete_date', serverField: 'complete_date', label: 'Complete Date', type: 'date', group: 'dm', cell: day },
  { key: 'complete_date_lx2', serverField: 'complete_date_lx2', label: 'Complete Date (LX2)', type: 'date', group: 'lx', cell: day },
  { key: 'dm_comments', serverField: 'dm_comments', label: 'DM Comments', group: 'dm' },
  { key: 'dm_comments_lx2', serverField: 'dm_comments_lx2', label: 'DM Comments (LX-2)', group: 'lx' },
  { key: 'source_spreadsheet_id', serverField: 'source_spreadsheet_id', label: 'Source_Spreadsheet_ID', group: 'dm', cell: (v) => dim(v) || <span className="mono">{v}</span> },
  { key: 'source_tab', serverField: 'source_tab', label: 'Source_Tab', group: 'dm' },
  { key: 'source_row_number', serverField: 'source_row_number', label: 'Source_Row_Number', group: 'dm', num: true, cell: num },
  { key: 'idempotency_key', serverField: 'idempotency_key', label: 'Idempotency_Key', group: 'dm', cell: (v) => dim(v) || <span className="mono">{v}</span> },
  { key: 'updated_at', serverField: 'updated_at', label: 'Modified Time', type: 'date', group: 'rec', serverOrdering: 'updated_at', cell: (v) => stamp(v) },
  { key: 'id', serverField: 'id', label: 'ID', group: 'rec', serverOrdering: 'id', num: true, cell: (v) => <span className="mono">{v}</span> },
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

// Evaluated ONCE. See the note on the `cols` prop below for why the identity of
// this array, not merely its contents, is what matters.
const TK_COLS = tkCols();

const HIDDEN_DEFAULT = ['competitor_event_name', 'organizer', 'event_month_year', 'event_location', 'relationship', 'actual_count_lx2'];

/**
 * Criteria arriving in the URL, from a Mining Matrix row.
 *
 * WHY NOT PUT THEM IN THE TABLE'S OWN FILTER BAR. DataTable persists `conds` per
 * tableId in localStorage and seeds its state from that ONCE, on first render
 * (components/DataTable.jsx, `storedRef`). Writing an incoming link's filter in
 * there would therefore either be ignored, on any browser that had opened this
 * page before, or would overwrite a filter the user had built by hand and left
 * set. Neither is acceptable for a link.
 *
 * So the link's filter rides on `serverCriteria` instead, the same channel the
 * status tabs use: ANDed into every request by the SERVER, not layered onto one
 * loaded page, so the row count under it is the real one. It is not editable in
 * place, which is why the banner below says what is applied and offers one click
 * to drop it.
 *
 * `unmined` is `actual_number is_empty` — the Data Mining result column, so NULL
 * means raised and not yet worked. It is the very predicate mining_matrix
 * aggregates on (backend/mining_matrix/services.py), which is what makes the
 * figure on the matrix row and the row count here the same number.
 */
function linkedCriteria(params) {
  const out = [];
  const purpose = (params.get('purpose') || '').trim();
  if (purpose) out.push({ field: 'purpose', op: 'is', value: purpose });
  if (params.get('unmined') === '1') out.push({ field: 'actual_number', op: 'is_empty' });
  return out;
}

export default function TicketCentralPage() {
  const { canView, can, user } = useSession();
  const toast = useToast();
  const nav = useNavigate();
  const { tab: subTab } = useParams();
  const [params, setParams] = useSearchParams();
  // No ticketsApi.list() here any more: that was a fetchAllPages walk of 35,690
  // rows (~72 sequential requests) before the table could render a single row.
  // DataTable now pages against the server. Tab counts already came from
  // tickets/stats/, which is a real aggregate.
  // Date range. The window is applied by the SERVER — over created_at ("Added
  // Time"), which is what this table sorts by; see accounts/period_filter.py for
  // why it cannot be a filter_spec criterion. The tab counts take the same
  // window, so a tab and the rows under it never disagree.
  //
  // Fixed at 'all' now — the Date Range control was removed from this page
  // (kept on Bookings only), so there is no UI left to change it.
  const period = 'all';
  const fetchStats = useCallback(() => ticketsApi.stats(period), [period]);
  const { data: stats, refetchQuiet: reloadStats } = useFetch(fetchStats, [period], { initialData: {} });
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
  const confirm = useConfirm();
  // null = closed; a row = edit that ticket. Adding is no longer this modal's
  // job — new tickets are typed into the inline grid below, so the modal is only
  // ever an EDIT form now and never has to render an empty one.
  const [formTicket, setFormTicket] = useState(null);
  // Handle the entry band registers on mount, so "New tickets" can ask it for a
  // row. A ref, not state: the band owns the drafts (and persists them), and
  // copying that count up here would be a second source of truth for it.
  const entryRef = useRef(null);
  const addEntryRow = useCallback(() => {
    if (entryRef.current) entryRef.current.addRows(1);
  }, []);
  /**
   * STABLE IDENTITY MATTERS HERE.
   *
   * DataTable re-renders once per animation frame while the rows are scrolled
   * (hooks/useVirtualRows sets state on scroll) and calls this on each of those
   * renders. An inline arrow would build a new element every frame, defeating
   * the memo on TicketEntryRows and re-rendering every draft cell about sixty
   * times a second while scrolling 42,912 rows. Which is exactly what it did.
   *
   * `refresh` is itself a useCallback, so the props reaching the band are
   * referentially stable and its shallow compare bails out.
   */
  const renderEntryBand = useCallback(
    (band) => <TicketEntryRows {...band} openRef={entryRef} onCreated={refresh} />,
    [refresh],
  );
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
  // Who may type a new ticket into the table. Read twice: by the New ticket row
  // button and by the band itself, so it is named once.
  const mayEnter = isMR && can('create', 'ticket_central');
  // `status` is a registered filter_spec field on TicketViewSet, so the tab is
  // evaluated by the database over all 35,690 rows rather than over one page.
  // The tab and any linked-in criteria are ANDed together, which is what lets a
  // Mining Matrix link be narrowed further by clicking a status tab.
  const linked = linkedCriteria(params);
  const criteria = [...(tab ? [{ field: 'status', op: 'is', value: tab }] : []), ...linked];
  const serverCriteria = criteria.length ? criteria : null;
  const linkedPurpose = (params.get('purpose') || '').trim();

  return (
    <>
      {/* Actions ride on the tab row — see BookingsPage for the reasoning. The
          page title duplicated the breadcrumb and the description sat in a row
          of its own above the tabs; both are gone, so the tabs start directly
          under the breadcrumb. */}
      <Tabs list={TABS} active={tab} onPick={(id) => nav('/tickets' + (id ? '/' + id : ''))}
        actions={<div className="ph-act">
          {user.role === 'admin' ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Smart import</button> : null}
          {mayEnter ? <button className="btn btn-p" onClick={addEntryRow}><Icon name="plus" size={15} />New ticket row</button> : null}
          {/* HP only — ClearAllButton renders nothing for anyone else, mirroring
              IsHPAccount on tickets/clear_all/. The endpoint already existed; there
              was simply no way to reach it from the UI. */}
          <ClearAllButton noun="tickets" count={S.total}
            onClear={ticketsApi.clearAll} onCleared={refresh}
            extra="The ticket-number sequences are reset with it, so the next ticket raised numbers from the start again." />
        </div>}
      />

      <>
          {linked.length ? (
            /* Says what arrived in the URL, and undoes it in one click. Without
               this the rows would simply be missing with nothing on screen
               accounting for it — the table's own filter bar cannot show these,
               because they are not in its `conds` (see linkedCriteria above). */
            <div className="lnk-filter">
              <Icon name="filter" size={14} />
              <span>
                Showing{' '}
                {params.get('unmined') === '1' ? <b>unmined</b> : 'all'} tickets
                {linkedPurpose ? <> for <b className="mono">{linkedPurpose}</b></> : null}
              </span>
              <button className="btn btn-sm btn-s" onClick={() => setParams({}, { replace: true })}>
                <Icon name="x" size={12} />Clear
              </button>
            </div>
          ) : null}

          <DataTable
        // Renamed from 'tickets' on purpose. Column visibility is persisted per
        // tableId (localStorage), and every user who had opened this page carried
        // a stored `hidden` set from the OLD default — which hid twelve of the
        // columns this order exists to show, so the new layout would have been
        // invisible to exactly the people already using it.
        tableId="tickets.v2"
        server={{ resource: 'tickets', mapRow: ticketsApi.fromApi }}
        serverCriteria={serverCriteria}
        serverParams={{ period }}
        onServerReady={keepRefetch}
        // Selection is what the Delete button reads, so a viewer who may delete
        // but not update must still be able to select — otherwise the button is
        // rendered on a table that hands it nothing.
        noun="tickets" select={can('update', 'ticket_central') || can('delete', 'ticket_central')} infinite pageSize={1000}
        // ASCENDING. Added Time is the row's own insert stamp, so oldest first
        // puts the newest ticket at the END of the table, which is where the
        // entry grid leaves a batch and how people read one back. Matches
        // Ticket.Meta.ordering, so the server and the header arrow agree.
        defaultSort={{ key: 'created_at', dir: 'asc' }} searchPlaceholder="Search ticket, organizer, keywords…"
        groups={[{ key: 'rec', label: 'Record' }, { key: 'mr', label: 'Ticket Hub (MR)' }, { key: 'dm', label: 'For DMD' }, { key: 'lx', label: 'LX-2 Second Pass' }]}
        hiddenDefault={HIDDEN_DEFAULT}
        // TK_COLS, not tkCols(). The call returned a fresh array on every render,
        // so `cols` was a new prop identity each time and DataTable's memoised Row
        // never hit — every loaded ticket re-rendered on every state change, and
        // this table accumulates pages as you scroll 42,912 of them. tkCols takes
        // no arguments and closes over nothing in the component, so the result is
        // a constant; it stays a factory only because the module already exported
        // it that way.
        cols={TK_COLS}
        // The Priority column declares editOpts. DataTable now renders an in-cell
        // editor only where the page says the viewer may write, so this has to be
        // passed explicitly or the column goes read-only.
        canEdit={can('update', 'ticket_central')}
        // New tickets are typed into the table itself: DataTable renders this
        // inside its scroll box, pinned under the last row, sharing the columns
        // and the horizontal scroll. It renders nothing until there is a draft,
        // so the table looks untouched until somebody starts one.
        entryBand={mayEnter ? renderEntryBand : null}
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
            {/* Select one row and this is a per-ticket delete; the endpoints have
                existed on TicketViewSet all along (destroy + bulk_delete) with
                nothing in the UI reaching either, so a wrongly-pushed ticket
                could only be edited, never removed. Wrapped in try/catch for the
                reason spelled out on the Bookings delete: the response
                interceptor only acts on 401, so a 403 would otherwise close the
                dialog and do nothing visible. */}
            {can('delete', 'ticket_central') ? (
              <button className="btn btn-sm btn-d" onClick={async () => {
                const ok = await confirm({ title: 'Delete tickets?', sub: plur(ids.length, 'ticket') + ' will be permanently removed.', danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>This cannot be undone. The ticket numbers are not reissued.</p> });
                if (!ok) return;
                try {
                  // The toast reports what the SERVER deleted, not how many were
                  // asked for — those differ whenever a row is out of scope.
                  const res = await ticketsApi.bulkRemove(ids);
                  clear(); refresh();
                  toast(plur(res.deleted, 'ticket') + ' deleted', 'ok');
                } catch (err) {
                  toast(apiErrorMessage(err, 'Could not delete those tickets.'), 'er');
                }
              }}><Icon name="trash" size={13} />Delete</button>
            ) : null}
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
          />
      </>

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="ticket" totalMatching={S.total} />
      ) : null}

      {formTicket ? (
        <TicketFormModal
          ticket={formTicket}
          onClose={() => setFormTicket(null)}
          onSaved={refresh}
        />
      ) : null}
      {importOpen ? <ImportWizard kind="tickets" onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
