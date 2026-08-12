import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { Av, StatusBadge } from '../../components/Badge';
import * as eventsApi from '../../api/events';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import DelegateTable, { blankDelegate } from './DelegateTable';
import * as bookingsApi from '../../api/bookings';

function ownerChip(roleLabel, personName) {
  const map = { 'Sales Exec': ['--green-bg', '--green-tx'], 'Speaker Sales': ['--blue-bg', '--blue-tx'], SpEx: ['--violet-bg', '--violet-tx'], 'Market Research': ['--amber-bg', '--amber-tx'] };
  const c = map[roleLabel] || ['--n-75', '--text-3'];
  if (!personName || personName === '—') return null;
  return (
    <span key={roleLabel} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '3px 11px 3px 3px', borderRadius: 999, background: `var(${c[0]})` }}>
      <Av name={personName} size="xs" />
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
        <span style={{ fontSize: 8, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.06em', color: `var(${c[1]})` }}>{roleLabel}</span>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text)' }}>{personName}</span>
      </span>
    </span>
  );
}

// `delegateRows` = every BOOKINGS row sharing the invoice_number of the row that was opened.
export default function EditBookingModal({ delegateRows, onClose, onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const first = delegateRows[0];
  const today = new Date().toISOString().slice(0, 10);
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const owners = (users || []).filter((u) => u.role === 'sales' && u.status === 'active');
  const ev = (events || []).find((e) => e.event_code === first.event_code) || {};

  const [invoiceNumber, setInvoiceNumber] = useState(first.invoice_number);
  const [delegates, setDelegates] = useState(delegateRows.map((d) => ({ key: 'row-' + d.id, ...d })));

  function addDelegate() {
    setDelegates((d) => [...d, blankDelegate(today, owners[0]?.name || '')]);
  }
  function removeDelegate(i) {
    setDelegates((d) => d.filter((_, idx) => idx !== i));
  }

  async function save() {
    if (!invoiceNumber.trim()) { toast('Invoice number is required', 'er'); return; }
    const missing = delegates.find((d) => !d.name.trim() || !d.company_name.trim() || !d.email.trim());
    if (missing) { toast('Each delegate needs a name, company and email', 'er'); return; }
    try {
      await bookingsApi.saveInvoiceDelegates(
        first.invoice_number,
        { invoice_number: invoiceNumber.trim(), event_code: first.event_code, event_name: first.event_name },
        delegates.map(({ key, ...d }) => d),
        first.book_event_id
      );
    } catch (err) {
      toast('Could not save booking — check the form and try again', 'er');
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
    <Modal size="full" onClose={onClose}
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
              {ownerChip('Sales Exec', ev.sales_lead)}{ownerChip('Speaker Sales', ev.speaker_team)}{ownerChip('SpEx', ev.spex_lead)}{ownerChip('Market Research', ev.mr_senior)}
            </div>
          </div>
          <button className="dr-x" aria-label="Close" style={{ marginLeft: 8 }} onClick={onClose}><Icon name="x" size={15} /></button>
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
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Event code</label><input className="in mono" value={first.event_code} readOnly /></div>
          <div className="fd"><label className="fd-l">Event name</label><input className="in" value={first.event_name} readOnly /></div>
          <div className="fd"><label className="fd-l">Invoice number<span className="req">*</span></label><input className="in mono" value={invoiceNumber} onChange={(e) => setInvoiceNumber(e.target.value)} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t" style={{ justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}><Icon name="users" size={13} />Delegate details</span>
          <button className="btn btn-s btn-sm" onClick={addDelegate}><Icon name="plus" size={13} />Add delegate</button>
        </div>
        <DelegateTable rows={delegates} onChange={setDelegates} onRemove={removeDelegate} eventCode={first.event_code} eventName={first.event_name} invoiceNumber={invoiceNumber} ownerNames={owners.map((u) => u.name)} />
      </div>
    </Modal>
  );
}
