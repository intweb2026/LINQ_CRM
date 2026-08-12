import { useState, useEffect } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import * as eventsApi from '../../api/events';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import DelegateTable, { blankDelegate } from './DelegateTable';
import * as bookingsApi from '../../api/bookings';

export default function NewBookingModal({ onClose, onCreated }) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const EVENTS = events || [];
  const owners = (users || []).filter((u) => u.role === 'sales' && u.status === 'active');
  const openEvents = EVENTS.filter((e) => e.status !== 'Completed');
  const [eventCode, setEventCode] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState('INV-' + (2026000 + Math.floor(Math.random() * 900)));
  const [delegates, setDelegates] = useState([blankDelegate(today, owners[0]?.name || '')]);
  const ev = EVENTS.find((e) => e.event_code === eventCode) || {};
  useEffect(() => {
    if (!eventCode && openEvents.length) setEventCode(openEvents[0].event_code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openEvents.length]);

  function addDelegate() {
    setDelegates((d) => [...d, blankDelegate(today, owners[0]?.name || '')]);
  }
  function removeDelegate(i) {
    setDelegates((d) => d.filter((_, idx) => idx !== i));
  }

  async function create() {
    if (!eventCode) { toast('Select an event', 'er'); return; }
    if (!invoiceNumber.trim()) { toast('Invoice number is required', 'er'); return; }
    const missing = delegates.find((d) => !d.name.trim() || !d.company_name.trim() || !d.email.trim());
    if (missing) { toast('Each delegate needs a name, company and email', 'er'); return; }
    try {
      await bookingsApi.createInvoice({
        invoice_number: invoiceNumber.trim(), event_code: ev.event_code, event_name: ev.name, request_date: today, invoice_date: today,
      }, delegates.map(({ key, ...d }) => d));
    } catch (err) {
      toast(err.response?.data?.invoice_number ? 'That invoice number already exists' : 'Could not create booking — check the form and try again', 'er');
      return;
    }
    onClose();
    toast(invoiceNumber + ' created with ' + delegates.length + ' delegate' + (delegates.length === 1 ? '' : 's'), 'ok');
    onCreated && onCreated();
  }

  return (
    <Modal size="full" title="New booking" sub="Create an invoice and add every delegate booked onto it." onClose={onClose}
      footer={<><span className="tb-m" style={{ marginRight: 'auto' }}><b>{delegates.length}</b> delegate{delegates.length === 1 ? '' : 's'}</span><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" onClick={create}><Icon name="check" size={15} />Save booking</button></>}>
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Invoice</div>
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label>
            <select className="in" value={eventCode} onChange={(e) => setEventCode(e.target.value)}>
              {openEvents.map((e) => <option key={e.id} value={e.event_code}>{e.event_code}</option>)}
            </select>
          </div>
          <div className="fd"><label className="fd-l">Event name</label><input className="in" value={ev?.name || ''} readOnly /></div>
          <div className="fd"><label className="fd-l">Invoice number<span className="req">*</span></label><input className="in mono" value={invoiceNumber} onChange={(e) => setInvoiceNumber(e.target.value)} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t" style={{ justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}><Icon name="users" size={13} />Delegate details</span>
          <button className="btn btn-s btn-sm" onClick={addDelegate}><Icon name="plus" size={13} />Add delegate</button>
        </div>
        <DelegateTable rows={delegates} onChange={setDelegates} onRemove={removeDelegate} eventCode={ev?.event_code} eventName={ev?.name} invoiceNumber={invoiceNumber} ownerNames={owners.map((u) => u.name)} />
      </div>
    </Modal>
  );
}
