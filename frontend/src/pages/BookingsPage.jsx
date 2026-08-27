import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Av, StatusBadge, Dot, Who } from '../components/Badge';
import { fdate, ftime, nf, plur, rel } from '../lib/helpers';
import { PAYMENT_STATUSES, ATTENDANCE, TICKET_TIERS, PAYMENT_TYPES, BOOKING_CODES, PAID_OR_FREE, paidOrFreeLabel } from '../lib/constants';
import { useBulkUpdate } from '../hooks/useBulkUpdate';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import NoAccessPage from './NoAccessPage';
import EditBookingModal from './bookings/EditBookingModal';
import NewBookingModal from './bookings/NewBookingModal';
import TransferBookingModal from './bookings/TransferBookingModal';
import ImportWizard from '../components/ImportWizard';
import BulkUpdateModal from '../components/BulkUpdateModal';
import { apiErrorMessage } from '../api/client';
import ClearAllButton from '../components/ClearAllButton';
import DateRangeFilter from '../components/DateRangeFilter';
import * as bookingsApi from '../api/bookings';

/**
 * Columns declare their server-side capabilities explicitly:
 *
 *   serverField    — the filter_spec field name. Present only where
 *                    BookDelegateViewSet.filter_spec_fields registers it. A
 *                    column without one is filtered in the browser, against the
 *                    fetched page only, and DataTable labels it as such.
 *   serverOrdering — the `ordering` term. Must be one of the viewset's
 *                    ordering_fields; DRF silently DROPS an unknown term, which
 *                    would leave rows in default order under a header claiming
 *                    otherwise. A column without one is not sortable in server
 *                    mode at all, rather than sorting one page and implying more.
 *
 * EVERY data column now carries one. It did not: name, owner,
 * accounts_contact_email, delegate_number, discount, added_time and
 * modified_time had no server field, and a column without one is not
 * "unfiltered" — it is filtered in the BROWSER, over the fifty rows that
 * happened to be loaded, with the footer counting those. On a table of ~14,800
 * delegates that reads as a filter that works and quietly lies. The seven are
 * registered on BookDelegateViewSet.filter_spec_fields now, three of them as
 * SQL expressions mirroring the serializer (see the helpers there):
 *
 *   name / owner            Trim(first || ' ' || last), and the same over the
 *                           invoice's sales executive with a username fallback.
 *   accounts_contact_email  resolved — the invoice's, else the delegate's own,
 *                           which is what the cell shows.
 *   delegate_number         the viewset's filter exclusion was lifted.
 *   added_time/modified_time created_at/updated_at, registered under the names
 *                           this table uses. Both are `type: 'date'` here so the
 *                           filter is a calendar and travels as a date criterion —
 *                           a text `contains` has no backend form on a date field
 *                           and would have fallen straight back to the page.
 *   discount                serverField is `discount_percent`, NOT `discount`:
 *                           the column stores a FRACTION (0.2) while this cell
 *                           shows the percent (20), so the backend annotates
 *                           discount * 100 and the criterion is written in the
 *                           units the user can see.
 *
 * Still deliberately absent:
 *   transfer                a button, not data — see the column itself.
 *
 * No column carries `editOpts`/`onEdit`: a booking is edited ONLY through
 * EditBookingModal, which opens via onRow and is gated on can('update',
 * 'bookings'). The in-cell dropdowns these columns used to declare bypassed that
 * gate entirely — DataTable rendered an editor from the presence of editOpts
 * alone — so a view-only role could set a payment status and never learn the
 * server had refused it. `opts` is unrelated and stays: it feeds the column's
 * FILTER value list, which is a read operation.
 *
 * A FACTORY, not a constant, because the Transfer column carries a callback and is
 * omitted entirely for a caller who cannot transfer.
 *
 * CALL IT ONCE PER SET OF ARGUMENTS, NOT ONCE PER RENDER. This used to say that
 * rebuilding each render cost nothing, because DataTable keyed off the column keys
 * rather than the array identity. That stopped being true when its Row became a
 * React.memo component: the memo uses a shallow prop comparison, so a fresh array
 * every render is a changed prop on every row, and the memo never hits. The call
 * site memoises on `canTransfer` for that reason.
 */
const bkCols = ({ onTransfer } = {}) => [
  { key: 'payment_status', label: 'Payment Status', group: 'id', serverField: 'payment_status',
    // _sort_effective_payment_status, NOT _sort_status. The latter annotates
    // invoice__payment_status, so it ordered by the invoice value while this cell
    // displays the resolved COALESCE(override, invoice) one — the header claimed an
    // order the rows did not have for any delegate carrying an override. Reproduced
    // and fixed in accounts/tests_resolved_ordering.py.
    serverOrdering: '_sort_effective_payment_status',
    cell: (v) => <StatusBadge value={v} />, opts: () => PAYMENT_STATUSES },
  { key: 'event_code', label: 'Event Code', group: 'id', serverField: 'event_code', serverOrdering: 'event_code',
    cell: (v) => <span className="mono" style={{ color: 'var(--t-600)' }}>{v}</span> },
  // serverField stays 'booking_code', which now resolves to the DELEGATE's own
  // column (book_delegate/views.py) — the same value this cell renders.
  { key: 'booking_code', label: 'Booking Code', group: 'id', serverField: 'booking_code',
    cell: (v) => <span className="mono">{v}</span>, opts: () => BOOKING_CODES },
  { key: 'request_date', label: 'Request Date', type: 'date', group: 'id', serverField: 'request_date', serverOrdering: '_sort_request_date', cell: (v) => fdate(v) },
  { key: 'invoice_date', label: 'Invoice Date', type: 'date', group: 'id', serverField: 'invoice_date', serverOrdering: '_sort_date', cell: (v) => fdate(v) },
  { key: 'invoice_number', label: 'Invoice Number', group: 'id', serverField: 'invoice_number', serverOrdering: '_sort_invoice', cell: (v) => <span className="mono lnk">{v}</span> },
  // Name only — the company had been repeated here as a sub-line directly
  // beside the Delegate Company column that already holds it.
  { key: 'name', label: 'Name', group: 'del', serverField: 'name', serverOrdering: '_sort_name', cls: 'st', cell: (v) => <Who name={v} avatar={false} /> },
  { key: 'company_name', label: 'Delegate Company', group: 'del', serverField: 'company_name' },
  { key: 'email', label: 'Delegate Email', group: 'del', serverField: 'email', serverOrdering: 'email', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'phone_number', label: 'Direct Line', group: 'del', serverField: 'phone_number', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v}</span> },
  { key: 'accounts_contact_email', label: 'Accounts Contact', group: 'del', serverField: 'accounts_contact_email', cell: (v) => <span className="dim" style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'delegate_number', label: 'Delegate Number', group: 'del', serverField: 'delegate_number', cell: (v) => <span className="mono">{v}</span> },
  // Displayed as "Payable"/"Free" and filtered by the STORED values — paidOrFreeLabel
  // is a rename of the wording only, so `optLabel` relabels the filter checkboxes
  // while the value posted to ?paid_or_free= stays what the server's choice field
  // accepts. See lib/constants.js.
  { key: 'paid_or_free', label: 'Payable/Free', group: 'pay', serverField: 'paid_or_free', serverOrdering: '_sort_effective_paid_or_free',
    cell: (v) => paidOrFreeLabel(v), opts: () => PAID_OR_FREE, optLabel: paidOrFreeLabel },
  { key: 'payment_date', label: 'Date Paid', type: 'date', group: 'pay', serverField: 'payment_date', serverOrdering: '_sort_effective_payment_date', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'payment_type', label: 'Payment Type', group: 'pay', serverField: 'payment_type', serverOrdering: '_sort_effective_payment_type', opts: () => PAYMENT_TYPES },
  { key: 'ticket_tier', label: 'Ticket Tier', group: 'pay', serverField: 'ticket_tier', serverOrdering: '_sort_effective_ticket_tier', cell: (v) => <span className="tg bg-neutral">{v}</span>,
    opts: () => TICKET_TIERS },
  // Percent, mapped from the stored fraction in api/bookings.js. Rendered through
  // an explicit cell so a zero discount reads as "0" — DataTable's default would
  // print the raw serialized decimal, which is what showed 0.00.
  { key: 'discount', label: 'Discount', group: 'pay', num: true, serverField: 'discount_percent',
    cell: (v) => <span>{v == null || v === '' ? 0 : v}</span> },
  { key: 'add_ons', label: 'Add-Ons', group: 'pay', serverField: 'add_ons' },
  { key: 'reference', label: 'Ref', group: 'pay', serverField: 'reference', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v}</span> },
  { key: 'event_name', label: 'Event Name', group: 'audit', serverField: 'event_name', cls: 'st' },
  // Where the dead "Transfer to Other Event" text column used to be. It held a
  // value with no backend field behind it, so anything typed into it was discarded;
  // the transfer is an action, and this is the button that starts it.
  //
  // Table view only, by construction: the Cards view renders `card(r)` below and
  // never these columns. stopPropagation is load-bearing — the row's own onClick
  // opens the edit modal, so without it every Transfer click would open two things.
  ...(onTransfer ? [{
    key: 'transfer', label: 'Transfer', group: 'audit',
    cell: (v, r) => (
      <span onClick={(e) => { e.stopPropagation(); onTransfer(r); }}>
        <button className="btn btn-s btn-sm" title={'Transfer ' + r.name + ' to another event'}>
          <Icon name="refresh" size={13} />Transfer
        </button>
      </span>
    ),
  }] : []),
  { key: 'added_time', label: 'Added Time', type: 'date', group: 'audit', serverField: 'added_time', serverOrdering: 'created_at',
    cell: (v) => (v ? <span className="dim">{fdate(v)} {ftime(v)}</span> : <span className="dim">—</span>) },
  // serverOrdering, and the table's defaultSort below, both point here now.
  // The header was dead without it: DataTable disables a header that has no
  // ordering term rather than sort the loaded page and imply it sorted the
  // table. The term must also be in BookDelegateViewSet.ordering_fields, or
  // DRF drops it silently and the rows come back in default order under a
  // header claiming otherwise. Rendered {fdate} {ftime}, both IST — see
  // lib/helpers.js; the value on the wire stays UTC.
  { key: 'modified_time', label: 'Modified Time', type: 'date', group: 'audit', serverField: 'modified_time', serverOrdering: 'updated_at', cell: (v) => (v ? <span className="dim">{fdate(v)} {ftime(v)}</span> : <span className="dim">—</span>) },
  { key: 'owner', label: 'Sales Executive', group: 'team', serverField: 'owner', cell: (v) => <Who name={v} avatar={false} /> },
  // ONE attendance column, backed by `attendance`. There were two: this one, and a
  // "Attendance - IN?" Yes/No column with no backend field behind it at all — it
  // read 'No' for all 14.8k rows because api/bookings.js hardcoded it. The
  // importers already treat Zoho's checkbox as this field, so the tick IS the
  // status: Confirmed = in. Filtering keeps the stored values rather than Yes/No,
  // because `attendance` is a choice field server-side and only its own choices
  // pass validation.
  { key: 'attendance', label: 'Attendance - IN?', group: 'team', serverField: 'attendance', serverOrdering: 'attendance',
    cell: (v) => (v === 'Confirmed' ? <Dot tone="green">In</Dot> : <span className="dim">—</span>),
    opts: () => ATTENDANCE },
];

const TAB_STATUSES = ['Pending', 'Paid'];

export default function BookingsPage() {
  const { canView, can } = useSession();
  const toast = useToast();
  const confirm = useConfirm();
  const nav = useNavigate();
  const { tab: subTab } = useParams();

  // Counts come from the database, one small query per tab — see
  // api/bookings.js countsByPaymentStatus for why they cannot be counted locally.
  const [counts, setCounts] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(() => new Date().toISOString());
  const [tableRefetch, setTableRefetch] = useState(null);

  const [editBooking, setEditBooking] = useState(null);
  const [transferRow, setTransferRow] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  // Stored inside an updater, not passed bare to setTableRefetch. React treats a
  // function argument to a state setter as an UPDATER, so `setTableRefetch(fn)`
  // invoked fn(prevState) and stored its return value — undefined. The visible
  // effects were one spurious extra fetch on mount and a Refresh button that
  // silently did nothing, because `tableRefetch` was never actually a function.
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);

  // Date range. Applied by the SERVER over COALESCE(request_date, invoice_date)
  // — the same date the Dashboard's monthly chart is keyed on, so the same button
  // gives the same number on both screens. See accounts/period_filter.py.
  const [period, setPeriod] = useState('all');

  // The tab counts take the window too. They sit directly above the rows, so
  // "Paid (1,204)" over a table showing 52 bookings would be the same defect as
  // an unfiltered aggregate beside a filtered table.
  const reloadCounts = useCallback(() => {
    bookingsApi.countsByPaymentStatus(TAB_STATUSES, period)
      .then(setCounts).catch(() => setCounts(null));
  }, [period]);

  useEffect(() => { reloadCounts(); }, [reloadCounts]);

  /**
   * The tab counts have to stay current alongside the rows, and they are a
   * different query — three small aggregates, not the page of delegates. The table
   * keeps ITSELF up to date (see DataTable's liveReload); this keeps
   * "Pending (312)" from disagreeing with the rows underneath it.
   *
   * Both `delegates` and `invoices` are watched because a booking is written
   * through either: the edit modal PATCHes invoices/{id}/, the import POSTs
   * invoices/bulk_import/, while marking paid and transferring go through
   * delegates/. Watching only the resource the page READS from would miss half
   * the writes it makes.
   */
  const { refreshNow: refreshCounts } = useLiveData(
    useCallback(() => {
      reloadCounts();
      setLastUpdated(new Date().toISOString());
    }, [reloadCounts]),
    { resources: ['delegates', 'invoices'] },
  );

  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    refreshCounts();
  }, [tableRefetch, refreshCounts]);

  // The schema fetch, the selection and the preview/commit pair were written out
  // here first; they are now the shared hook, which is what Ticket Central, Events,
  // Paper Review and Proposal Submission use as well. Same behaviour, one copy.
  const bulk = useBulkUpdate(bookingsApi.RESOURCE, refresh);

  // A transfer rewrites the booking it leaves AND creates one on the target event,
  // so it takes both rights — the same pair the endpoint enforces
  // (BookDelegateViewSet.transfer). Without them the column is not rendered at all,
  // rather than offering a button that can only answer 403.
  const canTransfer = can('update', 'bookings') && can('create', 'bookings');
  /**
   * Memoised on `canTransfer` alone, which is the only thing bkCols actually
   * varies on — setTransferRow is a setState function and React guarantees its
   * identity for the life of the component.
   *
   * Rebuilt every render, as this was, `cols` is a new prop identity each time and
   * DataTable's memoised Row never hits, so every loaded delegate re-renders on
   * every state change. This table is infinite-scroll over the largest table in
   * the CRM, so the cost grows the further the user scrolls — which is what
   * "it gets stuck once I've seen all the entries" describes.
   *
   * ABOVE the canView guard below, with every other hook. A hook after an early
   * return is called on some renders and not others, which breaks the order React
   * relies on; eslint's rules-of-hooks catches it, and it did.
   */
  const cols = useMemo(
    () => bkCols({ onTransfer: canTransfer ? (row) => setTransferRow({ row, dirty: false }) : null }),
    [canTransfer],
  );

  if (!canView('bookings')) return <NoAccessPage module="Bookings" />;

  const TABS = [
    { id: '', label: 'All', count: counts?.total },
    ...TAB_STATUSES.map((s) => ({ id: s, label: s, count: counts?.[s] })),
  ];
  const tab = TAB_STATUSES.includes(subTab) ? subTab : '';

  // The tab is a real server-side criterion, not a client narrowing of one page.
  const serverCriteria = tab ? [{ field: 'payment_status', op: 'is', value: tab }] : null;

  async function openInvoice(row) {
    try {
      // Every delegate on this invoice, from the server. Previously this filtered
      // an in-memory copy of all 35k rows; with server-side paging the siblings
      // may not be on the fetched page at all.
      const rows = await bookingsApi.listByInvoice(row.invoice_number);
      setEditBooking(rows.length ? rows : [row]);
    } catch {
      toast('Could not load the delegates on this invoice', 'er');
    }
  }

  return (
    <>
      {/* Import/New booking/Clear All ride on the SAME row as the tabs rather
          than a header row of their own above it — a row that, with the page
          title gone (see PageHead), had nothing on its left and was pure
          whitespace. Tabs and actions now share one horizontal line. */}
      <Tabs list={TABS} active={tab} onPick={(id) => nav('/bookings' + (id ? '/' + id : ''))}
        actions={<>
          <span className="tabs-upd">Updated {rel(lastUpdated)}</span>
          <button className="btn btn-g btn-ic btn-sm" title="Refresh bookings" onClick={refresh}><Icon name="refresh" size={14} /></button>
          <div className="ph-act">
            {can('create', 'bookings') ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button> : null}
            {can('create', 'bookings') ? <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New booking</button> : null}
            {/* HP only — the gate lives inside ClearAllButton, which renders nothing
                for anyone else, and mirrors IsHPAccount on the endpoint. It used to be
                an inline `user?.username === 'HP'` here, which is the copy every other
                module would have had to repeat. */}
            <ClearAllButton noun="bookings" count={counts?.total}
              onClear={bookingsApi.clearAll} onCleared={refresh}
              extra="Every invoice, delegate, webhook log and historical-registry row is destroyed with it." />
          </div>
        </>}
      />

      <DateRangeFilter value={period} onChange={setPeriod}
        count={counts?.total} noun="bookings" note="by request date" />

      <DataTable
        tableId="bookings"
        // live: the rows are READ from delegates/, but an invoice edit and the
        // import both write invoices/ — named here so those reach the table too.
        server={{ resource: bookingsApi.RESOURCE, mapRow: bookingsApi.fromApi, live: ['invoices'] }}
        serverCriteria={serverCriteria}
        serverParams={{ period }}
        onServerReady={keepRefetch}
        noun="bookings" select={can('delete', 'bookings') || can('update', 'bookings')} infinite pageSize={1000}
        // DEFAULT SORT IS MODIFIED TIME, newest first, by request.
        //
        // This prop is not decoration: DataTable sends an explicit `ordering`
        // for whatever sort is in effect, so BookDelegateViewSet.ordering is
        // the fallback for callers that send none and NOT what this table
        // opens on. Changing the viewset default alone would have changed
        // nothing here, which is why both moved together.
        //
        // It was request_date, a BUSINESS date on the invoice; a booking
        // corrected this morning against a July invoice sat in July. The
        // viewset's matching default is ["-updated_at", "-id"], served by
        // book_delegates_updated_id_idx. Request Date keeps its column and its
        // own index, so the chronological read is one header click away.
        //
        // defaultSortVersion RETIRES THE OLD STORED SORT, ONCE. Without it this
        // change reached nobody: DataTable persists each table's sort per browser
        // and a stored sort outranks defaultSort, so everyone who had ever opened
        // Bookings kept getting Request Date. Merely visiting the page writes that
        // blob, so "everyone" is not an exaggeration. Filters and hidden columns
        // are untouched; only the stale sort is dropped. BUMP THIS AGAIN if this
        // table's default ever moves again.
        defaultSort={{ key: 'modified_time', dir: 'desc' }} defaultSortVersion={1}
        searchPlaceholder="Search invoice, delegate, company…"
        groups={[
          { key: 'id', label: 'Identification' }, { key: 'del', label: 'Delegate' }, { key: 'pay', label: 'Payment & logistics' },
          { key: 'audit', label: 'Event & audit trail' }, { key: 'team', label: 'Team & check-in' },
        ]}
        hiddenDefault={[]}
        cols={cols}
        card={(r) => (
          <div className="rc">
            <div className="rc-t"><Av name={r.name} size="md" /><span className="who-t" style={{ flex: 1 }}><span className="who-n">{r.name}</span><span className="who-s mono">{r.invoice_number}</span></span><StatusBadge value={r.payment_status} /></div>
            <div className="rc-m">
              <div><div className="l">Event</div><div className="v mono" style={{ color: 'var(--t-600)' }}>{r.event_code}</div></div>
              <div><div className="l">Company</div><div className="v">{r.company_name}</div></div>
              <div><div className="l">Attendance - IN?</div><div className="v">{r.attendance === 'Confirmed' ? 'Yes' : 'No'}</div></div>
              <div><div className="l">Tier</div><div className="v">{r.ticket_tier}</div></div>
              <div><div className="l">Requested</div><div className="v">{fdate(r.request_date)}</div></div>
              <div><div className="l">Owner</div><div className="v">{r.owner}</div></div>
            </div>
          </div>
        )}
        onRow={can('update', 'bookings') ? openInvoice : undefined}
        bulkActions={(ids, { clear, total }) => (
          <div className="bulk">
            {/* `ids.length` is the truth of what the buttons below act on,
                whether that is six rows ticked by hand or every match the header
                checkbox resolved. The "of N matching" tail appears only while the
                two differ, so a partial selection can never read as the whole
                filtered set. */}
            <span className="n">{nf(ids.length)}</span> selected
            {total > ids.length ? <span className="dim" style={{ fontSize: 11 }}>&nbsp;of {nf(total)} matching</span> : null}
            <div className="sep" />
            {can('update', 'bookings') ? (
              <button className="btn btn-sm btn-p" onClick={() => bulk.open(ids, clear)}>
                <Icon name="edit" size={13} />Update field…
              </button>
            ) : null}
            {can('update', 'bookings') ? <button className="btn btn-sm btn-s" onClick={async () => { await bookingsApi.bulkMarkPaid(ids); clear(); refresh(); toast(plur(ids.length, 'booking') + ' marked paid', 'ok'); }}><Icon name="check" size={13} />Mark paid</button> : null}
            <button className="btn btn-sm btn-s" onClick={() => toast('Exporting ' + plur(ids.length, 'row') + '…', 'nf')}><Icon name="download" size={13} />Export</button>
            {can('delete', 'bookings') ? (
              <button className="btn btn-sm btn-d" onClick={async () => {
                const ok = await confirm({ title: 'Delete bookings?', sub: plur(ids.length, 'record') + ' will be permanently removed.', danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>This cannot be undone. Related delegate rows are removed with the invoice.</p> });
                // The toast reports what the SERVER deleted, not how many were
                // asked for. Those differ whenever RBAC scoping skips a row, and
                // that gap widens with select-all — "13,264 records deleted" over
                // a scoped delete of 900 is the kind of number people act on.
                if (!ok) return;
                // WRAPPED, because a rejected delete used to be INVISIBLE. The
                // response interceptor only acts on 401 (api/client.js), so a 403
                // — the shape a permission or scoping refusal arrives in — threw
                // past this handler, the confirm dialog closed, and nothing else
                // happened. "I pressed Delete and the row is still there" with no
                // message is indistinguishable from a bug in the delete itself.
                try {
                  const res = await bookingsApi.bulkRemove(ids);
                  clear(); refresh();
                  toast(plur(res.deleted, 'record') + ' deleted', 'ok');
                } catch (err) {
                  toast(apiErrorMessage(err, 'Could not delete those bookings.'), 'er');
                }
              }}><Icon name="trash" size={13} />Delete</button>
            ) : null}
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {bulk.ready ? (
        <BulkUpdateModal {...bulk.props} rowLabel="delegate" totalMatching={counts?.total} />
      ) : null}

      {/* Rendered here rather than inside the table so it survives the row list
          being refetched underneath it. */}
      {transferRow ? (
        <TransferBookingModal
          row={transferRow.row}
          dirty={transferRow.dirty}
          onClose={() => setTransferRow(null)}
          onTransferred={refresh}
        />
      ) : null}

      {editBooking ? (
        <EditBookingModal
          delegateRows={editBooking}
          onClose={() => setEditBooking(null)}
          onSaved={refresh}
          // A transfer from inside the edit modal closes it: the invoice it was
          // showing has changed on the server, and leaving a stale form open invites
          // saving the pre-transfer version back over it.
          onTransfer={canTransfer ? (row, dirty) => { setEditBooking(null); setTransferRow({ row, dirty }); } : undefined}
        />
      ) : null}
      {newOpen ? <NewBookingModal onClose={() => setNewOpen(false)} onCreated={refresh} /> : null}
      {importOpen ? <ImportWizard kind="bookings" onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
