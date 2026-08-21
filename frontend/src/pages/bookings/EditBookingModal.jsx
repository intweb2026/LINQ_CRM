import { useRef, useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import { Av, StatusBadge } from '../../components/Badge';
import { ownerOf } from '../../lib/owners';
import * as eventsApi from '../../api/events';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import DelegateTable, { blankDelegate, delegateProblem } from './DelegateTable';
import * as bookingsApi from '../../api/bookings';
import { apiErrorMessage } from '../../api/client';

/**
 * `owner` may be a plain name string or an ownerOf() result. The SpEx and Market
 * Research chips take the latter: those two event columns are blank on every
 * event in the live data, so both chips were unconditionally suppressed and the
 * header showed only Sales Exec. An inherited name is italicised and says which
 * team it came from in its tooltip, so it is not mistaken for a value someone
 * set on this event.
 */
function ownerChip(roleLabel, owner) {
  const map = { 'Sales Exec': ['--green-bg', '--green-tx'], SCA: ['--blue-bg', '--blue-tx'], SpEx: ['--violet-bg', '--violet-tx'], 'Market Research': ['--amber-bg', '--amber-tx'] };
  const c = map[roleLabel] || ['--n-75', '--text-3'];
  const personName = typeof owner === 'string' ? owner : (owner && owner.name) || '';
  const inherited = typeof owner === 'object' && owner && owner.inherited;
  const team = (typeof owner === 'object' && owner && owner.team) || '';
  if (!personName || personName === '—') return null;
  return (
    <span key={roleLabel} title={inherited ? `${roleLabel}: inherited from ${team || 'the owning team'} — no value set on this event` : undefined} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '3px 11px 3px 3px', borderRadius: 999, background: `var(${c[0]})` }}>
      <Av name={personName} size="xs" />
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
        <span style={{ fontSize: 8, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.06em', color: `var(${c[1]})` }}>{roleLabel}</span>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text)', fontStyle: inherited ? 'italic' : undefined }}>{personName}</span>
      </span>
    </span>
  );
}

/**
 * Everything the modal holds, as one comparable string.
 *
 * Feeds the `dirty` flag handed to the transfer modal. `key` is dropped because it
 * is a render identity this component adds, not booking data, and the two sides
 * would otherwise never match for a row that has not been touched.
 */
const snapshot = (invoiceNumber, eventCode, rows) =>
  JSON.stringify([invoiceNumber, eventCode, rows.map(({ key, ...d }) => d)]);

// `delegateRows` = every BOOKINGS row sharing the invoice_number of the row that was opened.
export default function EditBookingModal({ delegateRows, onClose, onSaved, onTransfer }) {
  const toast = useToast();
  const confirm = useConfirm();
  const first = delegateRows[0];
  const today = new Date().toISOString().slice(0, 10);
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });

  const [invoiceNumber, setInvoiceNumber] = useState(first.invoice_number);
  // Editable: a delegate who transfers to another event has to be re-homed, and
  // that is a change to the booking's event code, not a note in a free-text field.
  const [eventCode, setEventCode] = useState(first.event_code);
  const [delegates, setDelegates] = useState(delegateRows.map((d) => ({ key: 'row-' + d.id, ...d })));

  const EVENTS = events || [];
  const ev = EVENTS.find((e) => e.event_code === eventCode) || {};
  // Every event, not just the open ones: this booking's own code must be
  // selectable even when its event has been completed, or opening an old booking
  // would show an empty dropdown and the first save would move it elsewhere.
  // `first.event_code` is appended for a code with no master event behind it.
  const eventCodes = (() => {
    const codes = EVENTS.map((e) => e.event_code).filter(Boolean).sort((a, b) => a.localeCompare(b));
    return codes.includes(first.event_code) || !first.event_code ? codes : [first.event_code, ...codes];
  })();
  // Owned by the Events tab. It follows the event code — including a transfer made
  // in this modal — and the server derives the stored value the same way, from the
  // event, so this is a display of that rule rather than an editable field.
  const salesExec = ev.sales_exec || '';
  // The booking's stored name while the event is unchanged, the master catalogue's
  // when it has been transferred. The stored one carries the edition year
  // ("… 2026") and the catalogue's does not, so preferring the catalogue
  // unconditionally would drop the year from the field the moment the events list
  // arrived, with nobody having touched anything. On a transfer the backend
  // re-derives the name with the edition appended anyway (BookEvent.save()).
  const eventName = eventCode === first.event_code
    ? (first.event_name || ev.name || '')
    : (ev.name || '');

  // The transfer runs server-side against SAVED data, so the modal has to say
  // whether what it is showing has diverged from that.
  const initialSnapshot = useRef(snapshot(first.invoice_number, first.event_code, delegateRows));
  const dirty = snapshot(invoiceNumber, eventCode, delegates) !== initialSnapshot.current;

  function addDelegate() {
    setDelegates((d) => [...d, blankDelegate(today, salesExec)]);
  }
  function removeDelegate(i) {
    setDelegates((d) => d.filter((_, idx) => idx !== i));
  }

  async function save() {
    if (!invoiceNumber.trim()) { toast('Invoice number is required', 'er'); return; }
    if (!eventCode) { toast('Event code is required', 'er'); return; }
    const problem = delegateProblem(delegates);
    if (problem) { toast(problem, 'er'); return; }
    try {
      await bookingsApi.saveInvoiceDelegates(
        first.invoice_number,
        { invoice_number: invoiceNumber.trim(), event_code: eventCode, event_name: eventName },
        delegates.map(({ key, ...d }) => d),
        first.book_event_id
      );
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not save booking — check the form and try again'), 'er');
      return;
    }
    onClose();
    toast(invoiceNumber + ' updated', 'ok');
    onSaved && onSaved();
  }

  async function del() {
    onClose();
    const ok = await confirm({
      title: 'Delete booking?', sub: invoiceNumber + ' · ' + delegates.length + ' delegate' + (delegates.length === 1 ? '' : 's'), danger: true, ok: 'Delete booking',
      body: <p style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.55 }}>This removes the invoice and every delegate on it. This cannot be undone.</p>,
    });
    if (ok) { await bookingsApi.removeInvoice(first.book_event_id); toast(invoiceNumber + ' deleted', 'ok'); onSaved && onSaved(); }
  }

  return (
    <Modal size="full" bodyFill onClose={onClose}
      header={
        <div className="md-h">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, flexWrap: 'wrap' }}>
            <Av name={first.name} size="lg" />
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                <h2 style={{ fontSize: 17 }}>Edit Booking</h2><StatusBadge value={first.payment_status} /><span className="tg bg-neutral">{first.source === 'Direct' ? 'MANUAL' : (first.source || 'MANUAL').toUpperCase()}</span>
              </div>
              <p>{first.name} · {first.company_name}</p>
            </div>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {/* The event's sales executive — the same value the Sales Executive
                  column shows. It used to read ev.sales_lead, which is the event's
                  team LEADER, so the chip labelled "Sales Exec" and the column
                  named Sales Executive could disagree about the same booking. */}
              {/* No SCA chip beside this one: `sales_exec` already falls back to the
                  event's sales_team, so the two would print the same name twice on
                  every booking whose event has no sales_executive FK. */}
              {ownerChip('Sales Exec', ev.sales_exec)}{ownerChip('SpEx', ownerOf(ev, 'spex_lead'))}{ownerChip('Market Research', ownerOf(ev, 'mr_senior'))}
            </div>
          </div>
          {/* `.md-h` top-aligns its children (right, for a header whose title+sub
              stack tall), but this header's content row centers ITSELF instead —
              so without alignSelf the button anchored to the row's top edge while
              the avatar/badges sat centered a few px below it. */}
          <button className="dr-x" aria-label="Close" style={{ marginLeft: 8, alignSelf: 'center' }} onClick={onClose}><Icon name="x" size={15} /></button>
        </div>
      }
      footJustify="space-between"
      footer={<>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <button className="btn btn-g" style={{ color: 'var(--red)' }} onClick={del}><Icon name="trash" size={14} />Delete booking</button>
          <span className="tb-m"><b>{delegates.length}</b> delegate{delegates.length === 1 ? '' : 's'}</span>
        </div>
        <div style={{ display: 'flex', gap: 7 }}>
          <button className="btn btn-s" onClick={onClose}>Cancel</button>
          <button className="btn btn-p" onClick={save}><Icon name="check" size={15} />Save changes</button>
        </div>
      </>}
    >
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Invoice</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label>
            <Select className="in mono" value={eventCode} options={eventCodes} onChange={setEventCode} />
            {eventCode !== first.event_code ? (
              <span className="bu-hint" style={{ marginBottom: 0 }}>Transferring from <b>{first.event_code}</b> — every delegate on this invoice moves with it.</span>
            ) : null}
          </div>
          <div className="fd"><label className="fd-l">Event name</label><input className="in" value={eventName} readOnly /></div>
          <div className="fd"><label className="fd-l">Invoice number<span className="req">*</span></label><input className="in mono" value={invoiceNumber} onChange={(e) => setInvoiceNumber(e.target.value)} /></div>
          {/* SCA, read-only: it belongs to the EVENT (events.sales_team), not to
              the invoice, so it follows the event code above and is edited in the
              Events tab. Shown here because it is the owner a booking is
              attributed to, and reading it used to mean leaving the modal. */}
          <div className="fd"><label className="fd-l">SCA</label><input className="in" value={ev.sales_team || ev.sales_exec || ''} readOnly placeholder="—" /></div>
        </div>
      </div>
      {/* fs-fill: this section, not the modal body, owns the vertical scroll.
          See the .md-b.fill block in styles/overlays.css. */}
      <div className="fs fs-fill">
        <div className="fs-t" style={{ justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}><Icon name="users" size={13} />Delegate details</span>
          <button className="btn btn-s btn-sm" onClick={addDelegate}><Icon name="plus" size={13} />Add delegate</button>
        </div>
        <DelegateTable rows={delegates} onChange={setDelegates} onRemove={removeDelegate} eventCode={eventCode} eventName={eventName} invoiceNumber={invoiceNumber} salesExec={salesExec}
          onTransfer={onTransfer ? (row) => onTransfer(row, dirty) : undefined} />
      </div>
    </Modal>
  );
}
