import { useMemo, useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { StatusBadge } from '../../components/Badge';
import { plur } from '../../lib/helpers';
import * as eventsApi from '../../api/events';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import * as bookingsApi from '../../api/bookings';

/**
 * Transfer delegates' credit to another event — all of an invoice's, or some.
 *
 * `row` is a BOOKINGS row (a delegate joined onto its invoice), from either the
 * table or the edit modal — both hand over the same shape, so this modal does not
 * care which opened it. That row is the one preselected; the rest of its invoice is
 * listed alongside it, because an invoice with five delegates where only two are
 * moving is the ordinary case and there was no way to say so here.
 *
 * WHY THE PICKER IS NOT AN AFTERTHOUGHT
 * The two outcomes differ in the data, not just in the count. Moving EVERY delegate
 * puts "Credit Transferred" on the invoice itself; moving some puts it only on the
 * rows that left, because the ones staying are still booked and paid and would
 * otherwise be relabelled with them. The summary below therefore states which of the
 * two is about to happen, and the server decides it the same way over the whole
 * selection (BookDelegateViewSet.transfer_batch) rather than per delegate.
 *
 * `dirty` says the edit modal has unsaved changes. The transfer copies what is
 * SAVED — it runs server-side against stored data — so the warning is shown rather
 * than silently transferring a stale version of the row.
 */
export default function TransferBookingModal({ row, dirty = false, onClose, onTransferred }) {
  const toast = useToast();
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  // The whole invoice, from the server. The table pages server-side, so the
  // siblings of this row are frequently not in any list the browser is holding —
  // and the edit modal loads them the same way (BookingsPage.openInvoice).
  const fetchSiblings = useMemo(
    () => () => bookingsApi.listByInvoice(row.invoice_number),
    [row.invoice_number],
  );
  const { data: invoiceRows, loading: loadingRows, error: rowsError } =
    useFetch(fetchSiblings, [row.invoice_number], { initialData: null });

  const [targetCode, setTargetCode] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState(
    bookingsApi.suggestTransferInvoiceNumber(row.invoice_number),
  );
  // The row the modal was opened from starts selected, and stays selected unless
  // the user clears it: it is what they clicked Transfer on.
  const [picked, setPicked] = useState(() => new Set([row.id]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Until the invoice loads, the only delegate known to be on it is the one that
  // opened this modal — so the list shows exactly that and the transfer still works
  // if the fetch fails. A failed lookup must not block a transfer that was already
  // possible before this picker existed.
  const ALL = invoiceRows && invoiceRows.length ? invoiceRows : [row];
  const EVENTS = events || [];
  // Completed events are not offered: a transfer moves a delegate onto an event
  // they are going to attend. The event they are leaving is excluded too — the
  // server rejects it, and offering it invites the round trip.
  const targets = EVENTS
    .filter((e) => e.status !== 'Completed' && e.event_code && e.event_code !== row.event_code)
    .map((e) => e.event_code)
    .sort((a, b) => a.localeCompare(b));
  const target = EVENTS.find((e) => e.event_code === targetCode);

  const moving = ALL.filter((d) => picked.has(d.id));
  const staying = ALL.filter((d) => !picked.has(d.id));
  // The distinction the summary reports. Matches the server's rule: the invoice
  // takes the status only when the transfer leaves nothing on it.
  const wholeInvoice = staying.length === 0;
  const alreadyTransferred = moving.filter((d) => d.payment_status === 'Credit Transferred');

  function toggle(id) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setError('');
  }

  async function submit() {
    if (!moving.length) { setError('Choose at least one delegate to transfer.'); return; }
    if (!targetCode) { setError('Choose the event this booking is moving to.'); return; }
    if (!invoiceNumber.trim()) { setError('The new booking needs an invoice number.'); return; }
    setBusy(true);
    setError('');
    try {
      // One request for the whole selection, even when it is a single delegate:
      // the server decides the source invoice's status over the set, and doing that
      // per delegate from here made the outcome depend on the order they were sent.
      const res = await bookingsApi.transferDelegates(moving.map((d) => d.id), {
        targetEventCode: targetCode,
        invoiceNumber: invoiceNumber.trim(),
      });
      onClose();
      const who = moving.length === 1 ? moving[0].name : plur(res.count, 'delegate');
      toast(`${who} transferred to ${targetCode} as ${res.created.invoice_number}`, 'ok');
      onTransferred && onTransferred(res);
    } catch (err) {
      // The server's own words: "invoice X already exists on EVENT", "email is
      // already on invoice X". A generic message would hide the one thing the user
      // needs to change.
      setError(err.response?.data?.detail || 'Could not transfer this booking — try again.');
      setBusy(false);
    }
  }

  return (
    <Modal size="mdw" title="Transfer to other event"
      sub={`${row.invoice_number} · ${row.event_code} · ${plur(ALL.length, 'delegate')}`}
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-p" onClick={submit} disabled={busy || !moving.length}>
          {busy ? <span className="spin" /> : <Icon name="check" size={15} />}
          {busy ? 'Transferring…'
            : moving.length > 1 ? `Transfer ${moving.length} bookings` : 'Transfer booking'}
        </button>
      </>}
    >
      {dirty ? (
        <div className="vr wn">
          <Icon name="warn" size={15} />
          <span>This booking has unsaved changes. The transfer copies the <b>saved</b> version — save first if those edits should move with it.</span>
        </div>
      ) : null}

      {/* Not blocked, because transferring an already-transferred booking can be a
          legitimate correction, and a chain of hops is normal in the existing data
          (a booking moved on again reads Credit Transferred at every stage). But the
          credit behind those rows has already been given to another event once, so
          moving them again hands the same credit out twice — which is worth seeing
          before confirming, not after. */}
      {alreadyTransferred.length ? (
        <div className="vr wn">
          <Icon name="warn" size={15} />
          <span>
            {alreadyTransferred.length === 1
              ? <>This booking already reads <b>Credit Transferred</b> — its credit has been moved to another event once. Transferring again issues that credit a second time.</>
              : <>{alreadyTransferred.length} of the selected bookings already read <b>Credit Transferred</b> — their credit has been moved once already. Transferring again issues it a second time.</>}
          </span>
        </div>
      ) : null}

      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Who is moving</div>
        {rowsError ? (
          <div className="vr wn">
            <Icon name="warn" size={15} />
            <span>Could not load the other delegates on this invoice — only <b>{row.name}</b> can be transferred here. Reopen the invoice to try again.</span>
          </div>
        ) : null}
        <div className="tw">
          <table className="dt dt-form">
            <thead>
              <tr>
                <th style={{ width: 34 }}>
                  {/* Select-all is scoped to THIS invoice, which is the only set the
                      server accepts — a transfer splits one invoice. */}
                  <input type="checkbox" className="ck"
                    aria-label="Select every delegate on this invoice"
                    checked={ALL.length > 0 && moving.length === ALL.length}
                    onChange={() => {
                      setPicked(moving.length === ALL.length ? new Set() : new Set(ALL.map((d) => d.id)));
                      setError('');
                    }} />
                </th>
                <th>Delegate</th><th>Email</th><th>Tier</th><th>Payment Status</th>
              </tr>
            </thead>
            <tbody>
              {loadingRows && !invoiceRows ? (
                <tr><td colSpan={5} className="dim">Loading the delegates on this invoice…</td></tr>
              ) : ALL.map((d) => (
                <tr key={d.id} className={picked.has(d.id) ? 'sel' : ''}
                  onClick={() => toggle(d.id)} style={{ cursor: 'pointer' }}>
                  <td>
                    <input type="checkbox" className="ck" checked={picked.has(d.id)}
                      aria-label={`Transfer ${d.name}`}
                      onChange={() => toggle(d.id)} onClick={(e) => e.stopPropagation()} />
                  </td>
                  <td>{d.name}{d.id === row.id ? <span className="dim"> (this one)</span> : null}</td>
                  <td className="dim">{d.email}</td>
                  <td>{d.ticket_tier || '—'}</td>
                  <td><StatusBadge value={d.payment_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <span className="bu-hint" style={{ marginTop: 8, marginBottom: 0 }}>
          {moving.length === ALL.length
            ? 'Every delegate on this invoice is moving, so the invoice itself becomes Credit Transferred.'
            : `${plur(moving.length, 'delegate')} moving · ${plur(staying.length, 'delegate')} staying on ${row.invoice_number}, unchanged.`}
        </span>
      </div>

      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Moving to</div>
        <div className="fg">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label>
            <Select className="in mono" value={targetCode} options={targets}
              placeholder="Select an event…" onChange={(v) => { setTargetCode(v); setError(''); }} />
          </div>
          <div className="fd"><label className="fd-l">Event name</label>
            <input className="in" value={target?.name || ''} readOnly />
          </div>
          <div className="fd"><label className="fd-l">New invoice number<span className="req">*</span></label>
            <input className="in mono" value={invoiceNumber}
              onChange={(e) => { setInvoiceNumber(e.target.value); setError(''); }} />
            <span className="bu-hint" style={{ marginBottom: 0 }}>
              Suggested from the current number. Replace it with the target event's own
              numbering if you have it — or reuse an existing invoice on that event to
              add these delegates to a transfer already made.
            </span>
          </div>
          <div className="fd"><label className="fd-l">Sales executive</label>
            <input className="in" value={target?.sales_exec || '—'} readOnly />
          </div>
        </div>
      </div>

      <div className="fs">
        <div className="fs-t"><Icon name="refresh" size={13} />What this does</div>
        <div className="tw">
          <table className="dt dt-form">
            <thead>
              <tr><th>Booking</th><th>Event</th><th>Delegates</th><th>Payment Status</th></tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="mono">{row.invoice_number}</span> <span className="dim">(this one)</span></td>
                <td className="mono">{row.event_code}</td>
                <td>{wholeInvoice ? 'all ' + moving.length + ' move' : `${staying.length} stay`}</td>
                <td>
                  {/* WHERE the status lands is the whole difference between the two
                      cases, so the summary says which rows change rather than
                      showing one arrow for both. */}
                  {wholeInvoice ? (
                    <>
                      <StatusBadge value={row.payment_status} />
                      <Icon name="chevR" size={12} />
                      <StatusBadge value="Credit Transferred" />
                    </>
                  ) : (
                    <span className="dim">unchanged · the {plur(moving.length, 'moved row')} read Credit Transferred</span>
                  )}
                </td>
              </tr>
              <tr>
                <td><span className="mono">{invoiceNumber || '—'}</span> <span className="dim">(new)</span></td>
                <td className="mono">{targetCode || '—'}</td>
                <td>{moving.length}</td>
                <td><StatusBadge value="Paid (Transferred)" /></td>
              </tr>
            </tbody>
          </table>
        </div>
        <span className="bu-hint" style={{ marginTop: 8, marginBottom: 0 }}>
          Both sides are kept — the pair is the record of the transfer. Each delegate's
          details, booking code, tier and discount are copied to the new booking;
          attendance starts as not-in.
        </span>
      </div>

      {error ? <div className="vr er"><Icon name="warn" size={15} /><span>{error}</span></div> : null}
    </Modal>
  );
}
