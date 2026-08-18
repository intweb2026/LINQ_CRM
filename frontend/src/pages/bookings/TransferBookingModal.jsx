import { useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { StatusBadge } from '../../components/Badge';
import * as eventsApi from '../../api/events';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import * as bookingsApi from '../../api/bookings';

/**
 * Transfer one delegate's credit to another event.
 *
 * `row` is a BOOKINGS row (a delegate joined onto its invoice), from either the
 * table or the edit modal — both hand over the same shape, so this modal does not
 * care which opened it.
 *
 * The two resulting statuses are shown before anything is written, because the
 * outcome is two rows rather than a moved one: the booking left behind reads Credit
 * Transferred, and a new booking appears on the target event reading Paid
 * (Transferred). Nobody should have to discover that afterwards.
 *
 * `dirty` says the edit modal has unsaved changes. The transfer copies what is
 * SAVED — it runs server-side against stored data — so the warning is shown rather
 * than silently transferring a stale version of the row.
 */
export default function TransferBookingModal({ row, dirty = false, onClose, onTransferred }) {
  const toast = useToast();
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const [targetCode, setTargetCode] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState(
    bookingsApi.suggestTransferInvoiceNumber(row.invoice_number),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const EVENTS = events || [];
  // Completed events are not offered: a transfer moves a delegate onto an event
  // they are going to attend. The event they are leaving is excluded too — the
  // server rejects it, and offering it invites the round trip.
  const targets = EVENTS
    .filter((e) => e.status !== 'Completed' && e.event_code && e.event_code !== row.event_code)
    .map((e) => e.event_code)
    .sort((a, b) => a.localeCompare(b));
  const target = EVENTS.find((e) => e.event_code === targetCode);

  async function submit() {
    if (!targetCode) { setError('Choose the event this booking is moving to.'); return; }
    if (!invoiceNumber.trim()) { setError('The new booking needs an invoice number.'); return; }
    setBusy(true);
    setError('');
    try {
      const res = await bookingsApi.transferDelegate(row.id, {
        targetEventCode: targetCode,
        invoiceNumber: invoiceNumber.trim(),
      });
      onClose();
      toast(`${row.name} transferred to ${targetCode} as ${res.created.invoice_number}`, 'ok');
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
      sub={`${row.name} · ${row.invoice_number} · ${row.event_code}`}
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-p" onClick={submit} disabled={busy}>
          {busy ? <span className="spin" /> : <Icon name="check" size={15} />}
          {busy ? 'Transferring…' : 'Transfer booking'}
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
          credit behind this row has already been given to another event once, so
          moving it again hands the same credit out twice — which is worth seeing
          before confirming, not after. */}
      {row.payment_status === 'Credit Transferred' ? (
        <div className="vr wn">
          <Icon name="warn" size={15} />
          <span>This booking already reads <b>Credit Transferred</b> — its credit has been moved to another event once. Transferring again issues that credit a second time.</span>
        </div>
      ) : null}

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
              add this delegate to a transfer already made.
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
              <tr><th>Booking</th><th>Event</th><th>Payment Status</th></tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="mono">{row.invoice_number}</span> <span className="dim">(this one)</span></td>
                <td className="mono">{row.event_code}</td>
                <td>
                  <StatusBadge value={row.payment_status} />
                  <Icon name="chevR" size={12} />
                  <StatusBadge value="Credit Transferred" />
                </td>
              </tr>
              <tr>
                <td><span className="mono">{invoiceNumber || '—'}</span> <span className="dim">(new)</span></td>
                <td className="mono">{targetCode || '—'}</td>
                <td><StatusBadge value="Paid (Transferred)" /></td>
              </tr>
            </tbody>
          </table>
        </div>
        <span className="bu-hint" style={{ marginTop: 8, marginBottom: 0 }}>
          Both rows are kept — the pair is the record of the transfer. The delegate's
          details, booking code, tier and discount are copied to the new booking;
          attendance starts as not-in.
        </span>
      </div>

      {error ? <div className="vr er"><Icon name="warn" size={15} /><span>{error}</span></div> : null}
    </Modal>
  );
}
