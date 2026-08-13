import { useState } from 'react';
import Modal from '../../components/Modal';
import Select from '../../components/Select';
import { Icon } from '../../lib/icons';
import * as eventsApi from '../../api/events';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import DelegateTable, { blankDelegate, delegateProblem } from './DelegateTable';
import * as bookingsApi from '../../api/bookings';
import { apiErrorMessage } from '../../api/client';

export default function NewBookingModal({ onClose, onCreated }) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const EVENTS = events || [];
  const openEvents = EVENTS.filter((e) => e.status !== 'Completed');
  // Starts EMPTY, and stays empty until someone chooses. It used to auto-select the
  // first open event as soon as the list arrived, so a booking saved without
  // touching the field silently landed on whichever event happened to sort first.
  const [eventCode, setEventCode] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState('INV-' + (2026000 + Math.floor(Math.random() * 900)));
  const ev = EVENTS.find((e) => e.event_code === eventCode) || {};
  // Sales Executive comes from the event, so a new booking has no owner to show
  // until an event code is picked.
  const salesExec = ev.sales_exec || '';
  const [delegates, setDelegates] = useState([blankDelegate(today)]);

  function addDelegate() {
    setDelegates((d) => [...d, blankDelegate(today, salesExec)]);
  }
  function removeDelegate(i) {
    setDelegates((d) => d.filter((_, idx) => idx !== i));
  }

  async function create() {
    if (!eventCode) { toast('Select an event', 'er'); return; }
    if (!invoiceNumber.trim()) { toast('Invoice number is required', 'er'); return; }
    const problem = delegateProblem(delegates);
    if (problem) { toast(problem, 'er'); return; }
    try {
      await bookingsApi.createInvoice({
        invoice_number: invoiceNumber.trim(), event_code: ev.event_code, event_name: ev.name, request_date: today, invoice_date: today,
      }, delegates.map(({ key, ...d }) => d));
    } catch (err) {
      // The server's own words. It names the field, and for a delegate it names
      // the row too — guessing at the reason here is what made a rejected
      // booking look like a broken button.
      toast(apiErrorMessage(err, 'Could not create booking — check the form and try again'), 'er');
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
            <Select className="in mono" value={eventCode} placeholder="Select an event…"
              options={openEvents.map((e) => e.event_code).filter(Boolean).sort((a, b) => a.localeCompare(b))}
              onChange={setEventCode} />
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
        <DelegateTable rows={delegates} onChange={setDelegates} onRemove={removeDelegate} eventCode={ev?.event_code} eventName={ev?.name} invoiceNumber={invoiceNumber} salesExec={salesExec} />
      </div>
    </Modal>
  );
}
