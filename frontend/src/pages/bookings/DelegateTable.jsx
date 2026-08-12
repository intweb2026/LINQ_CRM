import { Icon } from '../../lib/icons';
import Select from '../../components/Select';
import { fdate, ftime } from '../../lib/helpers';
import { PAYMENT_STATUSES, ATTENDANCE, TICKET_TIERS, PAYMENT_TYPES, DISCOUNTS, YES_NO } from '../../lib/constants';

export const SOURCES = ['Website', 'Telemarketing', 'Partner', 'Referral', 'Direct'];
const DELEGATE_COUNTS = [1, 2, 3, 4];

export function blankDelegate(today, defaultOwner = '') {
  const now = new Date().toISOString();
  return {
    key: 'new-' + Math.random().toString(36).slice(2),
    id: null, payment_status: 'Pending', booking_code: '', request_date: today, invoice_date: today,
    name: '', company_name: '', email: '', phone_number: '', accounts_contact_email: '',
    delegate_number: '', paid_or_free: 'Paid', payment_date: '', payment_type: 'Stripe', ticket_tier: 'Regular',
    discount: '0%', add_ons: '—', reference: '', transfer_to_event: '—', added_time: now, modified_time: now,
    owner: defaultOwner, checked_in: 'No', attendance: 'Pending', delegate_count: 1, source: 'Website',
  };
}

// Columns follow the reference booking-list order: Payment Status → Event Code
// → … → Sales Executive → Attendance - IN?, with the pre-existing extra fields
// (detailed attendance status, delegate count, source) kept at the end so
// nothing already relied on elsewhere gets dropped. Job Title is intentionally
// omitted for now (removed per product request).
const baseCols = (ownerNames) => [
  { key: 'payment_status', label: 'Payment Status', type: 'select', options: PAYMENT_STATUSES, width: 150 },
  { key: 'event_code', label: 'Event Code', type: 'display', width: 130, mono: true, from: 'eventCode' },
  { key: 'booking_code', label: 'Booking Code', type: 'text', width: 130 },
  { key: 'request_date', label: 'Request Date', type: 'date', width: 140 },
  { key: 'invoice_date', label: 'Invoice Date', type: 'date', width: 140 },
  { key: 'invoice_number', label: 'Invoice Number', type: 'display', width: 150, mono: true, from: 'invoiceNumber' },
  { key: 'name', label: 'Name', type: 'text', width: 160, required: true },
  { key: 'company_name', label: 'Delegate Company', type: 'text', width: 170, required: true },
  { key: 'email', label: 'Delegate Email', type: 'email', width: 190, required: true },
  { key: 'phone_number', label: 'Direct Line', type: 'text', width: 150 },
  { key: 'accounts_contact_email', label: 'Accounts Contact', type: 'email', width: 190 },
  { key: 'delegate_number', label: 'Delegate Number', type: 'text', width: 140 },
  { key: 'paid_or_free', label: 'Paid/Free', type: 'select', options: ['Paid', 'Free'], width: 110 },
  { key: 'payment_date', label: 'Date Paid', type: 'date', width: 140 },
  { key: 'payment_type', label: 'Payment Type', type: 'select', options: PAYMENT_TYPES, width: 120 },
  { key: 'ticket_tier', label: 'Ticket Tier', type: 'select', options: TICKET_TIERS, width: 110 },
  { key: 'discount', label: 'Discount', type: 'select', options: DISCOUNTS, width: 100 },
  { key: 'add_ons', label: 'Add-Ons', type: 'text', width: 140 },
  { key: 'reference', label: 'Ref', type: 'text', width: 130 },
  { key: 'event_name', label: 'Event Name', type: 'display', width: 190, from: 'eventName' },
  { key: 'transfer_to_event', label: 'Transfer to Other Event', type: 'text', width: 170 },
  { key: 'added_time', label: 'Added Time', type: 'display', width: 150, format: 'datetime' },
  { key: 'modified_time', label: 'Modified Time', type: 'display', width: 150, format: 'datetime' },
  { key: 'owner', label: 'Sales Executive', type: 'select', options: ownerNames, width: 150 },
  { key: 'checked_in', label: 'Attendance - IN?', type: 'select', options: YES_NO, width: 130 },
  { key: 'attendance', label: 'Attendance Status', type: 'select', options: ATTENDANCE, width: 140 },
  { key: 'delegate_count', label: 'Count', type: 'select', options: DELEGATE_COUNTS, width: 90 },
  { key: 'source', label: 'Source', type: 'select', options: SOURCES, width: 130 },
];

export default function DelegateTable({ rows, onChange, onRemove, eventCode, eventName, invoiceNumber, ownerNames = [] }) {
  const ctx = { eventCode, eventName, invoiceNumber };
  const COLS = baseCols(ownerNames);

  function update(i, key, value) {
    const next = rows.slice();
    next[i] = { ...next[i], [key]: value, modified_time: new Date().toISOString() };
    onChange(next);
  }

  function displayValue(c, row) {
    if (c.from) return ctx[c.from] || '—';
    const v = row[c.key];
    if (!v) return '—';
    if (c.format === 'datetime') return fdate(v) + ' ' + ftime(v);
    return v;
  }

  return (
    <div className="tw">
      <div className="tsc" style={{ maxHeight: 420 }}>
        <table className="dt">
          <thead>
            <tr>
              <th style={{ width: 32 }}>#</th>
              {COLS.map((c) => <th key={c.key} style={{ minWidth: c.width }}>{c.label}{c.required ? <span className="req">*</span> : null}</th>)}
              {onRemove ? <th style={{ width: 36 }} /> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.key || row.id}>
                <td className="dim">{i + 1}</td>
                {COLS.map((c) => (
                  <td key={c.key}>
                    {c.type === 'select' ? (
                      <Select className="in in-xs" value={row[c.key] || ''} options={c.options} width={Math.max(c.width, 160)}
                        onChange={(v) => update(i, c.key, v)} />
                    ) : c.type === 'display' ? (
                      <span className={'dim' + (c.mono ? ' mono' : '')} style={{ fontSize: c.mono ? 11 : 12 }}>{displayValue(c, row)}</span>
                    ) : (
                      <input className="in in-xs" type={c.type} value={row[c.key] || ''} onChange={(e) => update(i, c.key, e.target.value)} />
                    )}
                  </td>
                ))}
                {onRemove ? (
                  <td>
                    {rows.length > 1 ? (
                      <button className="btn btn-g btn-sm btn-ic" aria-label="Remove delegate" onClick={() => onRemove(i)}><Icon name="trash" size={13} /></button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
