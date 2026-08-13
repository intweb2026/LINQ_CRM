import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageHead, Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import { Icon } from '../lib/icons';
import { Av, StatusBadge, Dot, Who } from '../components/Badge';
import { fdate, ftime, nf, plur, rel } from '../lib/helpers';
import { PAYMENT_STATUSES, ATTENDANCE, TICKET_TIERS, PAYMENT_TYPES, BOOKING_CODES } from '../lib/constants';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import NoAccessPage from './NoAccessPage';
import EditBookingModal from './bookings/EditBookingModal';
import NewBookingModal from './bookings/NewBookingModal';
import TransferBookingModal from './bookings/TransferBookingModal';
import ImportWizard from '../components/ImportWizard';
import BulkUpdateModal from '../components/BulkUpdateModal';
import ClearAllButton from '../components/ClearAllButton';
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
 * Deliberately absent server fields, and why:
 *   name / owner            full_name and sales_executive_name are computed on
 *                           the serializer (a property and a SerializerMethod);
 *                           neither is a column the delegate registry exposes.
 *   accounts_contact_email  invoice-sourced but not declared in the registry.
 *   delegate_number         explicitly excluded by the viewset.
 *   added_time/modified_time created_at/updated_at are in DEFAULT_EXCLUDES.
 *   transfer                a button, not data — see the column itself.
 *   discount                the column DOES exist server-side, but it stores a
 *                           FRACTION (0.2) while this cell shows the percent (20).
 *                           A server filter would therefore match a number the
 *                           user never saw, so discount is filtered in the browser
 *                           against the displayed value and DataTable says so.
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
 * omitted entirely for a caller who cannot transfer. DataTable expects callers to
 * rebuild cols each render and keys off the column keys rather than array identity
 * (DataTable.jsx:383), so this costs nothing.
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
  { key: 'request_date', label: 'Request Date', group: 'id', serverField: 'request_date', serverOrdering: '_sort_request_date', cell: (v) => fdate(v) },
  { key: 'invoice_date', label: 'Invoice Date', group: 'id', serverField: 'invoice_date', serverOrdering: '_sort_date', cell: (v) => fdate(v) },
  { key: 'invoice_number', label: 'Invoice Number', group: 'id', serverField: 'invoice_number', serverOrdering: '_sort_invoice', cell: (v) => <span className="mono lnk">{v}</span> },
  { key: 'name', label: 'Name', group: 'del', serverOrdering: '_sort_name', cls: 'st', cell: (v, r) => <Who name={v} sub={r.company_name} /> },
  { key: 'company_name', label: 'Delegate Company', group: 'del', serverField: 'company_name' },
  { key: 'email', label: 'Delegate Email', group: 'del', serverField: 'email', serverOrdering: 'email', cell: (v) => <span style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'phone_number', label: 'Direct Line', group: 'del', serverField: 'phone_number', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v}</span> },
  { key: 'accounts_contact_email', label: 'Accounts Contact', group: 'del', cell: (v) => <span className="dim" style={{ fontSize: 11.5 }}>{v}</span> },
  { key: 'delegate_number', label: 'Delegate Number', group: 'del', cell: (v) => <span className="mono">{v}</span> },
  { key: 'paid_or_free', label: 'Paid/Free', group: 'pay', serverField: 'paid_or_free', serverOrdering: '_sort_effective_paid_or_free', opts: () => ['Paid', 'Free'] },
  { key: 'payment_date', label: 'Date Paid', group: 'pay', serverField: 'payment_date', serverOrdering: '_sort_effective_payment_date', cell: (v) => (v ? fdate(v) : <span className="dim">—</span>) },
  { key: 'payment_type', label: 'Payment Type', group: 'pay', serverField: 'payment_type', serverOrdering: '_sort_effective_payment_type', opts: () => PAYMENT_TYPES },
  { key: 'ticket_tier', label: 'Ticket Tier', group: 'pay', serverField: 'ticket_tier', serverOrdering: '_sort_effective_ticket_tier', cell: (v) => <span className="tg bg-neutral">{v}</span>,
    opts: () => TICKET_TIERS },
  // Percent, mapped from the stored fraction in api/bookings.js. Rendered through
  // an explicit cell so a zero discount reads as "0" — DataTable's default would
  // print the raw serialized decimal, which is what showed 0.00.
  { key: 'discount', label: 'Discount', group: 'pay', num: true,
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
  { key: 'added_time', label: 'Added Time', group: 'audit', serverOrdering: 'created_at',
    cell: (v) => (v ? <span className="dim">{fdate(v)} {ftime(v)}</span> : <span className="dim">—</span>) },
  { key: 'modified_time', label: 'Modified Time', group: 'audit', cell: (v) => (v ? <span className="dim">{fdate(v)} {ftime(v)}</span> : <span className="dim">—</span>) },
  { key: 'owner', label: 'Sales Executive', group: 'team', cell: (v) => <Who name={v} /> },
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
  const [bulkState, setBulkState] = useState(null);   // {ids, clear}
  const [bulkSchema, setBulkSchema] = useState(null);

  // Stored inside an updater, not passed bare to setTableRefetch. React treats a
  // function argument to a state setter as an UPDATER, so `setTableRefetch(fn)`
  // invoked fn(prevState) and stored its return value — undefined. The visible
  // effects were one spurious extra fetch on mount and a Refresh button that
  // silently did nothing, because `tableRefetch` was never actually a function.
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);

  const reloadCounts = useCallback(() => {
    bookingsApi.countsByPaymentStatus(TAB_STATUSES).then(setCounts).catch(() => setCounts(null));
  }, []);

  useEffect(() => { reloadCounts(); }, [reloadCounts]);

  const refresh = useCallback(() => {
    if (tableRefetch) tableRefetch();
    reloadCounts();
    setLastUpdated(new Date().toISOString());
  }, [tableRefetch, reloadCounts]);

  // Fetched once, lazily: the modal renders entirely from this, so the field
  // list is the server's and nothing about it is hardcoded in the UI.
  useEffect(() => {
    if (!bulkState || bulkSchema) return;
    bookingsApi.bulkUpdateSchema().then(setBulkSchema).catch(() => {
      toast('Could not load the list of editable fields', 'er');
      setBulkState(null);
    });
  }, [bulkState, bulkSchema, toast]);

  const onPreview = useCallback(
    (field, value) => bookingsApi.bulkUpdateDryRun(bulkState.ids, field, value),
    [bulkState],
  );
  const onCommit = useCallback(
    (field, value, planHash) => bookingsApi.bulkUpdateApply(bulkState.ids, field, value, planHash)
      .then((res) => { bulkState.clear(); refresh(); return res; }),
    [bulkState, refresh],
  );

  if (!canView('bookings')) return <NoAccessPage module="Bookings" />;

  const TABS = [
    { id: '', label: 'All', count: counts?.total },
    ...TAB_STATUSES.map((s) => ({ id: s, label: s, count: counts?.[s] })),
  ];
  const tab = TAB_STATUSES.includes(subTab) ? subTab : '';

  // The tab is a real server-side criterion, not a client narrowing of one page.
  const serverCriteria = tab ? [{ field: 'payment_status', op: 'is', value: tab }] : null;

  // A transfer rewrites the booking it leaves AND creates one on the target event,
  // so it takes both rights — the same pair the endpoint enforces
  // (BookDelegateViewSet.transfer). Without them the column is not rendered at all,
  // rather than offering a button that can only answer 403.
  const canTransfer = can('update', 'bookings') && can('create', 'bookings');
  // {row, dirty} either way: a row straight from the table cannot have unsaved
  // edits, so the flag is only ever true when the edit modal raised it.
  const cols = bkCols({ onTransfer: canTransfer ? (row) => setTransferRow({ row, dirty: false }) : null });

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
      <PageHead title="Bookings"
        actions={<>
          {can('create', 'bookings') ? <button className="btn btn-s" onClick={() => setImportOpen(true)}><Icon name="download" size={15} />Import</button> : null}
          {can('create', 'bookings') ? <button className="btn btn-p" onClick={() => setNewOpen(true)}><Icon name="plus" size={15} />New booking</button> : null}
          {/* HP only — the gate lives inside ClearAllButton, which renders nothing
              for anyone else, and mirrors IsHPAccount on the endpoint. It used to be
              an inline `user?.username === 'HP'` here, which is the copy every other
              module would have had to repeat. */}
          <ClearAllButton noun="bookings" count={counts?.total}
            onClear={bookingsApi.clearAll} onCleared={refresh}
            extra="Every invoice, delegate, webhook log and historical-registry row is destroyed with it." />
        </>} />

      <Tabs list={TABS} active={tab} onPick={(id) => nav('/bookings' + (id ? '/' + id : ''))}
        actions={<>
          <span className="tabs-upd">Updated {rel(lastUpdated)}</span>
          <button className="btn btn-g btn-ic btn-sm" title="Refresh bookings" onClick={refresh}><Icon name="refresh" size={14} /></button>
        </>}
      />

      <DataTable
        tableId="bookings"
        server={{ resource: bookingsApi.RESOURCE, mapRow: bookingsApi.fromApi }}
        serverCriteria={serverCriteria}
        onServerReady={keepRefetch}
        noun="bookings" select={can('delete', 'bookings') || can('update', 'bookings')} infinite pageSize={50}
        defaultSort={{ key: 'request_date', dir: 'desc' }} searchPlaceholder="Search invoice, delegate, company…"
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
            {/* Selection spans loaded rows only — there is no "select all N
                matching". Saying so here is what stops `ids.length` being read as
                the whole filtered set. */}
            <span className="n">{nf(ids.length)}</span> selected
            {total > ids.length ? <span className="dim" style={{ fontSize: 11 }}>&nbsp;of {nf(total)} matching</span> : null}
            <div className="sep" />
            {can('update', 'bookings') ? (
              <button className="btn btn-sm btn-p" onClick={() => setBulkState({ ids, clear })}>
                <Icon name="edit" size={13} />Update field…
              </button>
            ) : null}
            {can('update', 'bookings') ? <button className="btn btn-sm btn-s" onClick={async () => { await bookingsApi.bulkMarkPaid(ids); clear(); refresh(); toast(plur(ids.length, 'booking') + ' marked paid', 'ok'); }}><Icon name="check" size={13} />Mark paid</button> : null}
            <button className="btn btn-sm btn-s" onClick={() => toast('Exporting ' + plur(ids.length, 'row') + '…', 'nf')}><Icon name="download" size={13} />Export</button>
            {can('delete', 'bookings') ? (
              <button className="btn btn-sm btn-d" onClick={async () => {
                const ok = await confirm({ title: 'Delete bookings?', sub: plur(ids.length, 'record') + ' will be permanently removed.', danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>This cannot be undone. Related delegate rows are removed with the invoice.</p> });
                if (ok) { await bookingsApi.bulkRemove(ids); clear(); refresh(); toast(plur(ids.length, 'record') + ' deleted', 'ok'); }
              }}><Icon name="trash" size={13} />Delete</button>
            ) : null}
            <button className="x" aria-label="Clear" onClick={clear}><Icon name="x" size={13} /></button>
          </div>
        )}
      />

      {bulkState && bulkSchema ? (
        <BulkUpdateModal
          onClose={() => setBulkState(null)}
          selectedIds={bulkState.ids}
          schema={bulkSchema}
          rowLabel="delegate"
          totalMatching={counts?.total}
          onPreview={onPreview}
          onCommit={onCommit}
        />
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
      {importOpen ? <ImportWizard kind="bookings" onClose={() => setImportOpen(false)} /> : null}
    </>
  );
}
