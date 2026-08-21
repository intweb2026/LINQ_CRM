// Real backend: /api/delegates/ is the flattened invoice+delegate row this
// UI's "BOOKINGS" concept models 1:1 (see backend/book_delegate/serializers.py
// BookDelegateListSerializer, which already joins invoice fields onto each
// delegate). /api/invoices/ is used only for invoice-level create/update
// (an invoice groups 1+ delegate rows) — see book_event/serializers.py.
//
// There are no longer any fields here without a backend equivalent.
//
// `transfer_to_event` used to be one: a free-text column with nothing behind it, so
// naming an event in it moved nothing. Transferring is now an action —
// transferDelegate() below, against BookDelegateViewSet.transfer.
//
// `checked_in` USED to be in that list — a Yes/No flag with nothing behind it,
// shown next to the real `attendance` enum as if the two were different facts.
// They are not: the Zoho importers already treat Zoho's "Attendance - IN?"
// checkbox as `attendance` ("true" → Confirmed, else Pending — see
// book_event/management/commands/import_booking_excel.py:291). The Bookings tab
// now shows ONE field, the checkbox, backed by `attendance`.
import {
  http, fetchAllPages, fetchPage, assertIdArray, chunk, mapLimit,
  bulkUpdate as bulkUpdateOn, fetchBulkUpdateSchema,
} from './client';

// The DRF resource these rows come from. Shared by the mass-update and
// filter_spec surfaces so the path is declared once.
export const RESOURCE = 'delegates';

/** Matches the cap in book_delegate/views.py bulk_delete; past it, a 400. */
const BULK_DELETE_MAX = 1000;

/**
 * PATCHes in flight at once for bulkMarkPaid, which has no batch endpoint.
 *
 * Six is what browsers allow per host over HTTP/1.1 anyway, so a higher number
 * buys queueing rather than throughput, while a much lower one makes a 13,264
 * delegate run needlessly serial.
 */
const MARK_PAID_CONCURRENCY = 6;

// ── Discount units ──────────────────────────────────────────────────────────
// The database stores a FRACTION: 0.2 means 20%, on both book_delegates.discount
// and book_events.discount, and every non-zero value in the export is one of
// 0.1/0.2/0.25/0.3/0.5. The UI works in PERCENT throughout — a row's `discount`
// is the number 20, and the editor's option is the label '20%' — so the two
// converters below are the only places the units meet.
//
// The previous discountToNumber() read '20%' as 20 and sent that, writing a value
// 100× larger than every row already in the table.

/** Stored fraction → the percent number the UI holds. '0.20' → 20, '0.00' → 0. */
function fractionToPercent(v) {
  const n = parseFloat(String(v ?? ''));
  if (!Number.isFinite(n)) return 0;
  // Scale then round to one decimal: 0.2 * 100 is not exactly 20 in binary
  // floating point, and 20.000000000000004 must not reach the cell.
  return Math.round(n * 1000) / 10;
}

/** Percent from the UI → the fraction to store. 20 | '20' | '20%' → 0.2. */
function percentToFraction(v) {
  if (v == null || v === '') return 0;
  const n = parseFloat(String(v).replace('%', ''));
  if (!Number.isFinite(n)) return 0;
  return Math.round((n / 100) * 10000) / 10000;
}

/**
 * The five fields stored BOTH on the invoice and as a per-delegate override that
 * shadows it, paired [what the UI calls it, the override column].
 *
 * Why the pairing matters on write: the modal shows one value per delegate, and
 * writing it as an override on every row leaves the invoice's own column stale —
 * so the Bookings table (which reads the resolved value) and every report that
 * reads invoice.payment_status would disagree about the same booking. When all
 * the delegates on an invoice agree, the value therefore goes on the INVOICE and
 * the overrides are cleared; overrides are only used to carry a genuine
 * per-delegate difference.
 */
const OVERRIDE_FIELDS = [
  ['payment_status', 'delegate_payment_status'],
  ['payment_type', 'delegate_payment_type'],
  ['payment_date', 'delegate_payment_date'],
  ['paid_or_free', 'delegate_paid_or_free'],
  ['ticket_tier', 'delegate_ticket_tier'],
];

/** The value every delegate shares for `key`, or undefined if they differ. */
function agreedValue(delegates, key) {
  if (!delegates.length) return undefined;
  const first = delegates[0][key] ?? '';
  return delegates.every((d) => (d[key] ?? '') === first) ? first : undefined;
}

/**
 * Split the delegates' person-level values into "belongs on the invoice" and
 * "stays as a per-delegate override".
 *
 * Returns { invoiceFields, inherited } where `inherited` names the override
 * columns to NULL out because the invoice now carries the value.
 */
function splitPersonLevel(delegates) {
  const invoiceFields = {};
  const inherited = {};
  OVERRIDE_FIELDS.forEach(([uiKey, overrideKey]) => {
    const agreed = agreedValue(delegates, uiKey);
    if (agreed !== undefined && agreed !== '' && agreed !== null) {
      invoiceFields[uiKey] = agreed;
      inherited[overrideKey] = true;
    }
  });
  // booking_code is per delegate now (book_delegate/models.py), but revenue
  // classification still reads invoice__booking_code (book_event/views.py:195,
  // config/views.py:244). Keeping the invoice in step whenever the delegates agree
  // stops those figures drifting away from what the Bookings tab shows. The
  // delegate values are NOT cleared — they are the authoritative ones.
  const agreedCode = agreedValue(delegates, 'booking_code');
  if (agreedCode) invoiceFields.booking_code = agreedCode;
  return { invoiceFields, inherited };
}

function toFrontend(d) {
  return {
    id: d.id,
    book_event_id: d.book_event_id,
    payment_status: d.effective_payment_status,
    event_code: d.event_code,
    booking_code: d.booking_code || '',
    request_date: d.request_date,
    invoice_date: d.invoice_date,
    invoice_number: d.invoice_number,
    name: d.full_name,
    company_name: d.company_display || '',
    email: d.email,
    phone_number: d.phone_number || '',
    accounts_contact_email: d.accounts_contact_email || '',
    delegate_number: d.delegate_number,
    paid_or_free: d.effective_paid_or_free,
    payment_date: d.effective_payment_date,
    payment_type: d.effective_payment_type,
    ticket_tier: d.effective_ticket_tier,
    // Percent, not the stored fraction — see fractionToPercent. The raw value
    // reached the cell as the string "0.00", which is what made a zero discount
    // read as 0.00 instead of 0.
    discount: fractionToPercent(d.discount),
    add_ons: d.add_ons || '',
    reference: d.reference || '',
    event_name: d.event_name || '',
    added_time: d.created_at,
    modified_time: d.updated_at,
    owner: d.sales_executive_name || '—',
    position: d.position || '',
    attendance: d.attendance,
    // Read for the "MANUAL"/"WEBSITE" chip in the edit modal's header only. The
    // editable Source field and its column are gone — it was never a decision
    // anyone made per booking, it records how the row arrived.
    source: d.source,
  };
}

/**
 * The invoice half of a booking write — ONLY the keys the caller actually set.
 *
 * THE BUG THIS FIXES
 * Every key used to be emitted with an `|| <default>` fallback, and the edit modal
 * passes a meta of just {invoice_number, event_code, event_name}. So a PATCH from
 * "Save changes" carried, silently:
 *
 *     payment_status: 'Pending'   ← reset, whatever the invoice actually was
 *     booking_code:   ''          ← wiped; SpEx/speaker revenue is classified from it
 *     company_name:   ''          ← wiped, along with contact_name/email/phone
 *     request_date:   null        ← wiped, along with invoice_date (the default sort)
 *     discount:       0           ← wiped
 *     source:         'manual'    ← a website booking relabelled as hand-entered
 *
 * Nothing surfaced it: the request succeeded, and the delegate rows the modal
 * shows are resolved through their own overrides, so the table looked unchanged
 * while the invoice underneath had been emptied.
 *
 * Undefined-means-absent is the whole point — a PATCH must be able to leave a
 * column alone. Pass an explicit null to CLEAR a nullable column.
 */
function invoiceToBackend(meta) {
  const out = {
    invoice_number: meta.invoice_number,
    event_code: meta.event_code,
    event_name: meta.event_name,
    event_date: meta.event_date,
    request_date: meta.request_date,
    invoice_date: meta.invoice_date,
    booking_code: meta.booking_code,
    company_name: meta.company_name,
    contact_name: meta.name ?? meta.contact_name,
    contact_email: meta.email ?? meta.contact_email,
    contact_phone: meta.phone_number,
    accounts_contact_email: meta.accounts_contact_email,
    currency: meta.currency,
    ticket_tier: meta.ticket_tier,
    payment_status: meta.payment_status,
    payment_type: meta.payment_type,
    payment_date: meta.payment_date,
    paid_or_free: meta.paid_or_free,
    discount: meta.discount === undefined ? undefined : percentToFraction(meta.discount),
    reference: meta.reference,
    source: meta.source,
  };
  Object.keys(out).forEach((k) => { if (out[k] === undefined) delete out[k]; });
  return out;
}

/**
 * The delegate half. `inherited` names the override columns to NULL because the
 * invoice is carrying that value — see splitPersonLevel.
 *
 * booking_code, delegate_number, delegate_payment_date and delegate_paid_or_free
 * were all missing from this payload, so the modal's Booking Code, Delegate
 * Number, Date Paid and Paid/Free edits were dropped in the browser before the
 * request was even built.
 *
 * company_name_raw was missing for the same reason and cost more: Delegate
 * Company is a REQUIRED column in both booking modals (DelegateTable marks it
 * with an asterisk and neither modal will submit without it), and the value
 * typed into it reached nothing. It is the column the Bookings table shows as
 * "Delegate Company" (BookDelegate.company_display falls back to it) and one of
 * the fields that tab searches — so every booking entered by hand read blank
 * there and could not be found by company name.
 */
function delegateToBackend(d, inherited = {}) {
  const override = (column, value) => (inherited[column] ? null : (value || null));
  // Whitespace is stripped at the boundary rather than trusted from the cell:
  // " jane@acme.test " passes the server's "@" test and stores a padded address,
  // which then fails to match the unique (invoice, email) pair it should have.
  const name = typeof d.name === 'string' ? d.name.trim() : d.name;
  const out = {
    id: (d.id && String(d.id).match(/^\d+$/)) ? d.id : undefined,
    first_name: name ? name.split(/\s+/)[0] : d.first_name,
    last_name: name ? name.split(/\s+/).slice(1).join(' ') : d.last_name || '',
    email: typeof d.email === 'string' ? d.email.trim() : d.email,
    company_name_raw: d.company_name === undefined ? undefined : String(d.company_name ?? '').trim(),
    phone_number: d.phone_number || '',
    position: d.position || '',
    ticket_package: d.ticket_package || '',
    sponsorship_level: d.sponsorship_level || '',
    // The Attendance - IN? checkbox. 'Pending' is the model default, so an
    // unchecked box on a row with no stored value is not a change.
    attendance: d.attendance || 'Pending',
    notes: d.notes || '',
    dietary_requirements: d.dietary_requirements || '',
    discount: percentToFraction(d.discount),
    add_ons: d.add_ons || '',
    reference: d.reference || '',
    booking_code: d.booking_code || '',
    delegate_number: Number.isFinite(Number(d.delegate_number)) && d.delegate_number !== ''
      ? Number(d.delegate_number) : 1,
    delegate_payment_status: override('delegate_payment_status', d.payment_status),
    delegate_payment_type: override('delegate_payment_type', d.payment_type),
    delegate_payment_date: override('delegate_payment_date', d.payment_date),
    delegate_paid_or_free: override('delegate_paid_or_free', d.paid_or_free),
    delegate_ticket_tier: override('delegate_ticket_tier', d.ticket_tier),
  };
  Object.keys(out).forEach((k) => { if (out[k] === undefined) delete out[k]; });
  return out;
}

/**
 * Every delegate row, all pages. Kept for callers that genuinely need the whole
 * set, but NOT for the Bookings table: this is ~35k rows and walking it pulls
 * all of them into the browser before a single filter runs. The table uses
 * server-side pagination via DataTable's `server` prop instead.
 */
export const list = () => fetchAllPages('delegates/').then((rows) => rows.map(toFrontend));

/** Map a raw API row to the shape the table and drawers read. */
export const fromApi = toFrontend;

/**
 * Tab counts, computed by the DATABASE rather than by counting loaded rows.
 *
 * Each status is one request with page_size=1, read for its `count` — six small
 * queries instead of 35k rows over the wire. It also has to be the server that
 * answers: payment_status is resolved as
 * COALESCE(NULLIF(delegate_payment_status,''), invoice.payment_status), so a
 * count taken over a partial page is not merely approximate, it is a different
 * question. `total` comes from an unfiltered count.
 */
export function countsByPaymentStatus(statuses, period) {
  return Promise.all([
    count(null, period),
    ...statuses.map((s) => count([{ field: 'payment_status', op: 'is', value: s }], period)),
  ]).then(([total, ...perStatus]) => {
    const out = { total };
    statuses.forEach((s, i) => { out[s] = perStatus[i]; });
    return out;
  });
}

/**
 * How many delegate rows match `criteria` — one request, one row, read for the
 * paginator's `count`.
 *
 * Anything that only needs a NUMBER must use this rather than `list().length`.
 * Sidebar and AppShell both wanted "how many bookings are pending" and each was
 * walking every page at page_size=500 to get it — roughly 70 requests and 35k
 * rows deserialised, on every route change, because both components mount in the
 * app shell.
 *
 * `period` is an optional DASH_PERIODS key. It is a separate param rather than
 * another criterion because the booking window is COALESCE(request_date,
 * invoice_date) — see backend/accounts/period_filter.py — which no single-column
 * criterion can express.
 */
export function count(criteria, period) {
  return fetchPage(`${RESOURCE}/`, {
    page: 1,
    pageSize: 1,
    filterSpec: criteria ? JSON.stringify({ match: 'all', criteria }) : null,
    params: period ? { period } : undefined,
  }).then((r) => r.count);
}

/**
 * Convenience for the shell badges: how many bookings are awaiting payment.
 *
 * Deliberately NOT windowed. The sidebar badge is a worklist count, and a booking
 * does not stop awaiting payment because it was raised five weeks ago — the same
 * reasoning that keeps the Dashboard's attention queue on all-time figures.
 */
export function countPending() {
  return count([{ field: 'payment_status', op: 'is', value: 'Pending' }]);
}

// Row-level edits from the table go through the delegate's own override
// fields (delegate_payment_status/_type/_ticket_tier) rather than the shared
// invoice, so editing one delegate's cell never silently changes payment
// status for every other delegate on the same invoice.
export function update(id, patch) {
  const body = {};
  if (patch.payment_status !== undefined) body.delegate_payment_status = patch.payment_status;
  if (patch.paid_or_free !== undefined) body.delegate_paid_or_free = patch.paid_or_free;
  if (patch.payment_type !== undefined) body.delegate_payment_type = patch.payment_type;
  if (patch.payment_date !== undefined) body.delegate_payment_date = patch.payment_date;
  if (patch.ticket_tier !== undefined) body.delegate_ticket_tier = patch.ticket_tier;
  if (patch.discount !== undefined) body.discount = percentToFraction(patch.discount);
  if (patch.attendance !== undefined) body.attendance = patch.attendance;
  if (patch.add_ons !== undefined) body.add_ons = patch.add_ons;
  if (patch.reference !== undefined) body.reference = patch.reference;
  if (!Object.keys(body).length) return Promise.resolve(null);
  return http.patch(`delegates/${id}/`, body).then((r) => r.data);
}

export function markPaid(id) {
  return update(id, { payment_status: 'Paid', payment_date: new Date().toISOString().slice(0, 10) });
}
/**
 * There is no batch "mark paid" endpoint, so this is one PATCH per delegate.
 *
 * Written as Promise.all(ids.map(...)), which was fine while a selection could
 * only hold the rows on one page — 50 parallel requests. The header checkbox now
 * selects every matching row, so the same expression on Bookings opens 13,264
 * connections at once: the browser queues them behind its six-per-host limit,
 * the tab stops responding, and the API takes the whole burst from one click.
 * mapLimit holds it to a steady handful in flight.
 *
 * Per-row failures are still swallowed to null, unchanged: one delegate that
 * cannot be marked paid must not abandon the rest of the batch.
 */
export function bulkMarkPaid(ids) {
  // Guarded like bulkRemove and bulkUpdate. A Set would reach `.map` and throw a
  // bare "ids.map is not a function" with no indication of which call site or
  // what type arrived; assertIdArray names both.
  assertIdArray(ids, 'bookings.bulkMarkPaid');
  return mapLimit(ids, MARK_PAID_CONCURRENCY, (id) => markPaid(id).catch(() => null));
}

/**
 * Delete delegates, in batches the endpoint will accept.
 *
 * delegates/bulk_delete/ caps at 1000 ids per request (book_delegate/views.py)
 * and 400s past it. A select-all is routinely larger, and the 400 surfaced as
 * "Maximum 1000 IDs per request" on a Delete the user had already confirmed —
 * so the whole delete appeared to fail. Batches are sequential, so the totals
 * returned describe what actually happened up to any failure rather than a
 * half-known state.
 */
export async function bulkRemove(ids) {
  // Guarded for the same reason bulkUpdate is: this posts the collection as JSON,
  // and a Set has no enumerable own properties, so JSON.stringify turns it into
  // {} — the backend then answers "ids list required" and the delete silently
  // does nothing. Throw at the call site with the real type named instead.
  assertIdArray(ids, 'bookings.bulkRemove');
  const totals = { deleted: 0, requested: 0, permitted: 0, out_of_scope: 0 };
  for (const batch of chunk(ids, BULK_DELETE_MAX)) {
    // eslint-disable-next-line no-await-in-loop
    const res = await http.post('delegates/bulk_delete/', { ids: batch }).then((r) => r.data);
    totals.deleted += res.deleted || 0;
    totals.requested += res.requested || 0;
    totals.permitted += res.permitted || 0;
    totals.out_of_scope += res.out_of_scope || 0;
  }
  return totals;
}

// ── Mass update (accounts/bulk_update.py) ───────────────────────────────────
// The generic engine, not a per-field endpoint: `field` must be one the server
// declared in bulk_update_schema, and every write is previewed first.
//
// The five person-level fields are edited through their delegate_* OVERRIDE
// names, never the bare name. `payment_status` and friends are read-only
// @property on BookDelegate and read_only on the serializer, so a write to the
// bare name is discarded silently — the request succeeds and nothing changes.
export const bulkUpdateSchema = () => fetchBulkUpdateSchema(RESOURCE);

/**
 * Dry run — returns the plan (distribution, no_op, collateral, side_effects,
 * plan_hash) and writes nothing. Pass `value` as undefined to preview with no
 * target chosen; the key is then omitted entirely, which is how the backend
 * distinguishes "not chosen yet" from null ("clear this field").
 */
export function bulkUpdateDryRun(ids, field, value) {
  return bulkUpdateOn(RESOURCE, { ids, field, value, commit: false });
}

/** Commit. `planHash` must be the hash from the plan the user was shown. */
export function bulkUpdateApply(ids, field, value, planHash) {
  return bulkUpdateOn(RESOURCE, { ids, field, value, commit: true, planHash });
}
// DELETE /api/invoices/clear_all/ — backend restricts this to username "HP".
export function clearAll() {
  return http.delete('invoices/clear_all/').then((r) => r.data);
}

export const listByInvoice = (invoiceNumber) =>
  http.get(`delegates/by_invoice/${invoiceNumber}/`).then((r) => r.data.map(toFrontend));

export function createInvoice(meta, delegates) {
  const { invoiceFields, inherited } = splitPersonLevel(delegates);
  const body = {
    ...invoiceToBackend({ ...invoiceFields, ...meta }),
    delegates: delegates.map((d) => delegateToBackend(d, inherited)),
  };
  return http.post('invoices/', body).then((r) => r.data);
}

/**
 * Save the invoice and its full delegate list in one PATCH.
 *
 * `meta` wins over the delegates' shared values, so an explicit invoice-level
 * field from the caller is never second-guessed by the consensus rule.
 * sales_executive is deliberately NOT sent: the backend derives it from the event
 * code (book_event/serializers.py _apply_event_sales_executive), which is what
 * makes the Sales Executive column follow a transfer to another event.
 */
export function saveInvoiceDelegates(invoiceNumber, meta, delegates, bookEventId) {
  const { invoiceFields, inherited } = splitPersonLevel(delegates);
  const body = {
    ...invoiceToBackend({ ...invoiceFields, ...meta }),
    delegates: delegates.map((d) => delegateToBackend(d, inherited)),
  };
  return http.patch(`invoices/${bookEventId}/`, body).then((r) => r.data);
}

export function removeInvoice(bookEventId) {
  return http.delete(`invoices/${bookEventId}/`).then(() => true);
}

/**
 * Move one delegate's credit to another event.
 *
 * ONE request, not three. The transfer marks this row "Credit Transferred" and
 * creates a booking on the target event as "Paid (Transferred)"; done as separate
 * calls from here, a failure in the middle would leave the delegate credited on two
 * events or on none. The server does all of it in one transaction — see
 * BookDelegateViewSet.transfer.
 *
 * Resolves with { source, created }; rejects with the server's `detail` for the
 * cases the UI must show verbatim (invoice number taken on another event, delegate
 * already on the target invoice).
 */
export function transferDelegate(delegateId, { targetEventCode, invoiceNumber }) {
  return http.post(`delegates/${delegateId}/transfer/`, {
    target_event_code: targetEventCode,
    invoice_number: invoiceNumber,
  }).then((r) => r.data);
}

/**
 * Move SEVERAL of one invoice's delegates to another event — a PARTIAL transfer.
 *
 * Five delegates on an invoice where only two are moving is the ordinary case. It
 * was expressible before this by calling transferDelegate() twice, the second call
 * reusing the invoice number the first one created, but as separate requests:
 *
 *   - a failure on the second left one delegate moved and one behind, with nothing
 *     in the data saying that was not the intent;
 *   - the source invoice's status flipped to Credit Transferred on whichever call
 *     happened to empty it, so the outcome depended on the order they were sent in.
 *
 * One request decides both over the whole set. See BookDelegateViewSet.transfer_batch.
 *
 * Every id must be a delegate on the SAME invoice — the operation is "split this
 * invoice" and the server refuses a mixed set rather than guessing.
 *
 * Resolves with { source, created, count, delegates } — `source.left_behind` is how
 * many stayed, and `source.scope` is 'invoice' when the transfer emptied the invoice
 * (its own status becomes Credit Transferred) or 'delegate' when it did not (only
 * the moved rows carry that status, as overrides).
 */
export function transferDelegates(delegateIds, { targetEventCode, invoiceNumber }) {
  return http.post('delegates/transfer/', {
    delegate_ids: delegateIds,
    target_event_code: targetEventCode,
    invoice_number: invoiceNumber,
  }).then((r) => r.data);
}

/** The number the transfer modal offers for the new booking. */
export const suggestTransferInvoiceNumber = (invoiceNumber) => `${invoiceNumber || 'INV'}-T`;

/**
 * The single oldest delegate still awaiting payment, or null.
 *
 * The dashboard needs one row to say "oldest is 3 weeks ago". It used to get it
 * by sorting all 13,269 loaded rows in the browser. `_sort_request_date` is the
 * viewset's ordering term for invoice.request_date.
 */
export function oldestPending() {
  return fetchPage(`${RESOURCE}/`, {
    pageSize: 1,
    ordering: '_sort_request_date',
    filterSpec: JSON.stringify({
      match: 'all',
      criteria: [{ field: 'payment_status', op: 'is', value: 'Pending' }],
    }),
  }).then((r) => (r.results.length ? toFrontend(r.results[0]) : null));
}

/** The `n` most recent delegate rows, for the activity feed. */
export function recent(n = 5) {
  return fetchPage(`${RESOURCE}/`, { pageSize: n, ordering: '-_sort_request_date' })
    .then((r) => r.results.map(toFrontend));
}
