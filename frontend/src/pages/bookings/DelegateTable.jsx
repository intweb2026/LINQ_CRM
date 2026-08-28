import { Icon } from '../../lib/icons';
import Select from '../../components/Select';
import { fdate, ftime } from '../../lib/helpers';
import {
  PAYMENT_STATUSES, TICKET_TIERS, PAYMENT_TYPES, DISCOUNTS,
  BOOKING_CODES, DELEGATE_NUMBERS, PAID_OR_FREE, paidOrFreeLabel,
} from '../../lib/constants';

// ── Discount, as the row holds it ───────────────────────────────────────────
// A row's `discount` is a PERCENT NUMBER (20), converted to and from the stored
// fraction (0.2) at the API boundary — see api/bookings.js. These two turn it into
// the option label the dropdown works in, and back.

/** 20 → '20%'. An empty cell reads as '0%': no discount is 0, not unknown. */
function percentLabel(v) {
  if (v == null || v === '') return '0%';
  const n = parseFloat(String(v).replace('%', ''));
  return (Number.isFinite(n) ? n : 0) + '%';
}

/** '20%' → 20. */
function percentValue(label) {
  const n = parseFloat(String(label).replace('%', ''));
  return Number.isFinite(n) ? n : 0;
}

/**
 * `options`, plus the row's own value when it is not among them.
 *
 * Booking codes are a closed list now, and the live data holds a handful of codes
 * outside the agreed set. Appending the stored value means such a row still shows
 * what it holds — a plain dropdown would render it as blank and the next save
 * would replace real data with the first thing anyone clicked.
 *
 * Columns marked `strictOptions` opt out: Delegate Number must offer 0 and 1 and
 * nothing else, so a legacy 2 is not carried into the picker.
 */
function optionsWith(options, value) {
  if (value == null || value === '' || options.includes(value)) return options;
  return [...options, value];
}

// ── Row validation, shared by the new and edit modals ───────────────────────
// name@domain.tld. The SERVER only insists on an "@" (book_event/serializers.py
// validate_delegates), and this is deliberately the stricter of the two: a CRM
// address that cannot receive mail is not worth storing, and catching it here
// names the row and the value instead of costing a round trip that came back as
// {"delegates": ["Delegate #1 has an invalid email."]} — a message the modal
// then threw away.
const EMAIL_RE = /^\S+@\S+\.\S+$/;

/**
 * The first problem with `rows`, phrased for a toast, or null when they will save.
 *
 * The email SHAPE is checked, not merely its presence. The modals used to test
 * `!d.email.trim()` only, so anything with a character in it was posted — and an
 * address with no "@" is the one thing the invoice endpoint rejects outright.
 * That 400 was the reason "Save booking" appeared to do nothing.
 */
export function delegateProblem(rows) {
  for (let i = 0; i < rows.length; i++) {
    const d = rows[i];
    const who = 'Delegate ' + (i + 1);
    if (!String(d.name ?? '').trim()) return who + ' needs a name';
    if (!String(d.company_name ?? '').trim()) return who + ' needs a company';
    const email = String(d.email ?? '').trim();
    if (!email) return who + ' needs an email address';
    if (!EMAIL_RE.test(email)) return who + ' has an invalid email address: ' + email;
  }
  return null;
}

/**
 * What a brand-new delegate row books as.
 *
 * 'Delegate' rather than blank: it is what the overwhelming majority of rows are,
 * and an empty Booking Code is not a meaningful state — it was simply the value
 * every new row carried until somebody remembered to set it. Still a dropdown, so
 * a speaker or a comp is one click away.
 */
const DEFAULT_BOOKING_CODE = 'Delegate';

export function blankDelegate(today, defaultOwner = '') {
  const now = new Date().toISOString();
  return {
    key: 'new-' + Math.random().toString(36).slice(2),
    id: null, payment_status: 'Pending', booking_code: DEFAULT_BOOKING_CODE, request_date: today, invoice_date: today,
    name: '', company_name: '', email: '', phone_number: '', accounts_contact_email_raw: '',
    delegate_number: 1, paid_or_free: 'Paid', payment_date: '', payment_type: 'Stripe', ticket_tier: 'Regular',
    discount: 0, add_ons: '', reference: '', added_time: now, modified_time: now,
    owner: defaultOwner, attendance: 'Pending',
  };
}

/**
 * Columns follow the reference booking-list order: Payment Status → Event Code →
 * … → Sales Executive → Attendance - IN?, with Added/Modified Time last.
 *
 * Deliberate omissions, and why:
 *   Source          removed on request. It records how the row arrived (website vs
 *                   hand-entered), not a per-booking decision; the edit modal's
 *                   header chip still shows it.
 *   Transfer…       the free-text "Transfer to Other Event" cell is now a BUTTON
 *                   (type 'action'), present only when the caller passes
 *                   onTransfer. Typing an event name into a column with no backend
 *                   field never moved anything; the transfer is a server action that
 *                   rewrites this booking and creates one on the target event.
 *   Count           removed on request — delegate_count and Delegate Number were
 *                   two fields for one fact, and Delegate Number is the one kept.
 *   Attendance      the separate Pending/Confirmed/No-show status dropdown is gone.
 *   Status          "Attendance - IN?" below is the same field as a checkbox, which
 *                   is how Zoho presents it and how the importers already read it.
 *   Job Title       intentionally omitted (removed per earlier product request).
 *
 * Event Code and Event Name are `display` and come from the INVOICE (the `from`
 * key reads the ctx object): they are one value for the whole booking, edited at
 * the top of the modal, so showing an editor per delegate row would imply that two
 * delegates on one invoice could sit on different events. Sales Executive is
 * display for a different reason — it is owned by the Events tab, not by this form.
 */
const baseCols = ({ onTransfer } = {}) => [
  { key: 'payment_status', label: 'Payment Status', type: 'select', options: PAYMENT_STATUSES, width: 160 },
  { key: 'event_code', label: 'Event Code', type: 'display', width: 130, mono: true, from: 'eventCode' },
  { key: 'booking_code', label: 'Booking Code', type: 'select', options: BOOKING_CODES, width: 170 },
  // Per delegate, like Date Paid below it. Both dates resolve through the
  // delegate's own override to the invoice's column (api/bookings.js
  // OVERRIDE_FIELDS), so editing one row changes that row; the invoice keeps the
  // shared value for as long as every delegate on it agrees.
  { key: 'request_date', label: 'Request Date', type: 'date', width: 140 },
  { key: 'invoice_date', label: 'Invoice Date', type: 'date', width: 140 },
  { key: 'invoice_number', label: 'Invoice Number', type: 'display', width: 150, mono: true, from: 'invoiceNumber' },
  { key: 'name', label: 'Name', type: 'text', width: 160, required: true },
  { key: 'company_name', label: 'Delegate Company', type: 'text', width: 170, required: true },
  { key: 'email', label: 'Delegate Email', type: 'email', width: 190, required: true },
  { key: 'phone_number', label: 'Direct Line', type: 'digits', width: 150 },
  // The INVOICE's accounts contact, edited here because this is the only form
  // that opens a booking. Left blank it falls back to the delegate's own email
  // everywhere the booking is read (book_delegate/serializers.py), which is what
  // `placeholderFrom` shows greyed in the empty cell — the fallback is visible
  // without being stored, and typing over it is all it takes to set a real one.
  // Editing it on any row sets it for the whole invoice; see splitPersonLevel.
  { key: 'accounts_contact_email_raw', label: 'Accounts Contact', type: 'email', width: 190, placeholderFrom: 'email' },
  { key: 'delegate_number', label: 'Delegate Number', type: 'select', options: DELEGATE_NUMBERS, strictOptions: true, width: 140 },
  { key: 'paid_or_free', label: 'Payable/Free', type: 'select', options: PAID_OR_FREE, optionLabel: paidOrFreeLabel, width: 110 },
  { key: 'payment_date', label: 'Date Paid', type: 'date', width: 140 },
  { key: 'payment_type', label: 'Payment Type', type: 'select', options: PAYMENT_TYPES, width: 120 },
  { key: 'ticket_tier', label: 'Ticket Tier', type: 'select', options: TICKET_TIERS, width: 110 },
  { key: 'discount', label: 'Discount', type: 'select', options: DISCOUNTS, width: 100, percent: true },
  { key: 'add_ons', label: 'Add-Ons', type: 'text', width: 140 },
  { key: 'reference', label: 'Ref', type: 'text', width: 130 },
  { key: 'event_name', label: 'Event Name', type: 'display', width: 190, from: 'eventName' },
  ...(onTransfer ? [{ key: 'transfer', label: 'Transfer to Other Event', type: 'action', width: 170 }] : []),
  { key: 'owner', label: 'Sales Executive', type: 'display', width: 150, from: 'salesExec' },
  {
    key: 'attendance', label: 'Attendance - IN?', type: 'checkbox', width: 120,
    checked: (v) => v === 'Confirmed',
    // Unchecking returns the row to Pending, EXCEPT where it holds one of the
    // other stored states: 'No-show' and 'Cancelled' are also "not in", and
    // flattening them to Pending would destroy the distinction on any row that
    // was merely opened and saved.
    toggle: (on, prev) => (on ? 'Confirmed' : (!prev || prev === 'Confirmed' ? 'Pending' : prev)),
  },
  { key: 'added_time', label: 'Added Time', type: 'display', width: 150, format: 'datetime' },
  { key: 'modified_time', label: 'Modified Time', type: 'display', width: 150, format: 'datetime' },
];

/**
 * The booking code that is never charged for.
 *
 * An SPP delegate is a sponsor's pass, so the row carries no money: selecting it
 * blanks Date Paid and sets Payable/Free to 'Free' in one go, which is what
 * whoever picked it was going to do by hand on the next two cells anyway. Matched
 * exactly, so the combined 'SPP / Group Pass' is left alone; that one is a group
 * pass as well and is not automatically free.
 *
 * Like the Date Paid rule below, this fires only as the code CHANGES, so both
 * cells stay ordinary editors and either can be set back by hand straight after.
 */
const FREE_BOOKING_CODE = 'SPP';

/**
 * Statuses a Date Paid entry is allowed to promote to 'Paid'.
 *
 * Deliberately NOT every status. 'Cancelled', 'Refunded', 'Credit Transferred',
 * 'Paid (Transferred)' and 'IQ Staff' are decisions somebody made about the
 * booking, and a date typed into the cell beside them is not a reason to erase
 * one; the credit-pending pair are their own state for the same reason. Only a
 * row that is still waiting for money moves.
 *
 * A FREE delegate is included: Payable/Free records what was charged, not whether
 * the paperwork settled, so a date entered against a free row marks it paid like
 * any other.
 */
const AUTO_PAID_FROM = ['', 'Pending', 'Unpaid'];

/**
 * The payment status a row should carry now that Date Paid holds `date`, or null
 * to leave the status alone.
 *
 * Both directions. Entering a date on a row that is still waiting for money marks
 * it 'Paid'; CLEARING the date takes that same row back to 'Pending', because a
 * booking with no date paid is not a paid booking. The reverse leg is limited to
 * rows sitting on 'Paid' for the same reason the forward leg is limited to
 * AUTO_PAID_FROM — it undoes this rule, and nothing else.
 *
 * This only ever fires as Date Paid CHANGES, so the status cell remains a normal
 * dropdown and anything set here can be overridden by hand straight afterwards.
 */
function autoPaidStatus(row, date) {
  const current = String(row.payment_status ?? '').trim();
  if (!String(date ?? '').trim()) return current === 'Paid' ? 'Pending' : null;
  if (!AUTO_PAID_FROM.includes(current)) return null;
  return 'Paid';
}

export default function DelegateTable({ rows, onChange, onRemove, eventCode, eventName, invoiceNumber, salesExec, onTransfer }) {
  const ctx = { eventCode, eventName, invoiceNumber, salesExec };
  const COLS = baseCols({ onTransfer });

  function update(i, key, value) {
    const next = rows.slice();
    const row = { ...next[i], [key]: value, modified_time: new Date().toISOString() };
    if (key === 'payment_date') {
      const auto = autoPaidStatus(next[i], value);
      if (auto) row.payment_status = auto;
    }
    if (key === 'booking_code' && String(value).trim() === FREE_BOOKING_CODE) {
      row.payment_date = '';
      row.paid_or_free = 'Free';
    }
    // A cancelled booking does not seat a delegate, so the ordinal drops to 0
    // along with the status; the picker still allows correcting it by hand.
    if (key === 'payment_status' && String(value).trim() === 'Cancelled') {
      row.delegate_number = 0;
    }
    next[i] = row;
    onChange(next);
  }

  function displayValue(c, row) {
    if (c.from) return ctx[c.from] || '—';
    const v = row[c.key];
    if (!v) return '—';
    if (c.format === 'datetime') return fdate(v) + ' ' + ftime(v);
    return v;
  }

  function cell(c, row, i) {
    if (c.type === 'select') {
      const value = c.percent ? percentLabel(row[c.key]) : (row[c.key] ?? '');
      return (
        <Select className="in in-xs" value={value} options={c.strictOptions ? c.options : optionsWith(c.options, value)} labelOf={c.optionLabel} width={Math.max(c.width, 160)}
          onChange={(v) => update(i, c.key, c.percent ? percentValue(v) : v)} />
      );
    }
    if (c.type === 'checkbox') {
      return (
        <input type="checkbox" className="ck" aria-label={c.label} checked={c.checked(row[c.key])}
          onChange={(e) => update(i, c.key, c.toggle(e.target.checked, row[c.key]))} />
      );
    }
    if (c.type === 'action') {
      // A delegate added in this modal has no id yet, so there is nothing on the
      // server to transfer — the button says why instead of failing on click.
      const unsaved = !row.id;
      return (
        <button type="button" className="btn btn-s btn-sm" disabled={unsaved}
          title={unsaved ? 'Save the booking before transferring this delegate' : 'Transfer to another event'}
          onClick={() => onTransfer(row)}>
          <Icon name="refresh" size={13} />Transfer
        </button>
      );
    }
    if (c.type === 'display') {
      return <span className={'dim' + (c.mono ? ' mono' : '')} style={{ fontSize: c.mono ? 11 : 12 }}>{displayValue(c, row)}</span>;
    }
    // 'digits' is a text input that only ever holds digits. type="number" is not
    // the same thing: it still accepts 'e', '+', '-' and '.' as you type, and it
    // reports those as an empty value, so a typo would silently blank the cell.
    // Filtering on change keeps the caret usable and drops anything else outright.
    if (c.type === 'digits') {
      return (
        <input className="in in-xs" type="text" inputMode="numeric" value={row[c.key] || ''}
          onChange={(e) => update(i, c.key, e.target.value.replace(/\D+/g, ''))} />
      );
    }
    return (
      <input className="in in-xs" type={c.type} value={row[c.key] || ''}
        placeholder={c.placeholderFrom ? (row[c.placeholderFrom] || '') : undefined}
        title={c.placeholderFrom && !row[c.key] && row[c.placeholderFrom] ? "Blank — the delegate's own email is used" : undefined}
        onChange={(e) => update(i, c.key, e.target.value)} />
    );
  }

  return (
    <div className="tw">
      {/* No height of its own. A hardcoded maxHeight:420 here knew nothing about
          how much room the modal body actually had, so on a window under roughly
          780px tall the body scrolled as well and there were two scrollbars. The
          height now comes from the .md-b.fill chain in styles/overlays.css, which
          gives this box whatever is left after the invoice fields. It also
          restores the <=880px rule `.tsc{max-height:none}`, which the inline
          style silently outranked. */}
      <div className="tsc">
        <table className="dt dt-form dt-grid">
          {/* Remove FIRST, then the row number, then the fields.
              The row is ~25 columns wide and this container scrolls horizontally, so
              a trash button in the last column sat off-screen: deleting one of
              several delegates meant scrolling to the far right, per row. Leading the
              row keeps it beside the number it belongs to.
              `pin1`/`pin2` (styles/components.css) then PIN both to the left edge, so
              they stay reachable at any scroll position — first column alone would
              only be in view until the first sideways scroll. The row number takes
              pin1 when there is no remove column, so the pinned pair is never a
              40px gap with scrolled content showing through. */}
          <colgroup>
            {onRemove ? <col style={{ width: 44 }} /> : null}
            <col style={{ width: onRemove ? 32 : 44 }} />
            {COLS.map((c) => <col key={c.key} style={{ width: c.width }} />)}
          </colgroup>
          <thead>
            <tr>
              {onRemove ? <th className="pin1" /> : null}
              <th className={onRemove ? 'pin2' : 'pin1'}>#</th>
              {COLS.map((c) => <th key={c.key} style={{ minWidth: c.width }}>{c.label}{c.required ? <span className="req">*</span> : null}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.key || row.id}>
                {onRemove ? (
                  <td className="pin1">
                    {/* The last delegate has no remove button — an invoice with no
                        delegates on it is not a state this form can produce. */}
                    {rows.length > 1 ? (
                      <button className="btn btn-g btn-sm btn-ic" aria-label={'Remove delegate ' + (i + 1)} onClick={() => onRemove(i)}><Icon name="trash" size={13} /></button>
                    ) : null}
                  </td>
                ) : null}
                <td className={'dim ' + (onRemove ? 'pin2' : 'pin1')}>{i + 1}</td>
                {COLS.map((c) => <td key={c.key}>{cell(c, row, i)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
