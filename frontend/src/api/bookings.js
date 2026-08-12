// Real backend: /api/delegates/ is the flattened invoice+delegate row this
// UI's "BOOKINGS" concept models 1:1 (see backend/book_delegate/serializers.py
// BookDelegateListSerializer, which already joins invoice fields onto each
// delegate). /api/invoices/ is used only for invoice-level create/update
// (an invoice groups 1+ delegate rows) — see book_event/serializers.py.
//
// Known gaps — fields this UI expects with no backend equivalent, defaulted
// rather than fabricated: `transfer_to_event`, `checked_in` (a separate
// Yes/No "checked in" flag distinct from the `attendance` status enum).
import {
  http, fetchAllPages, fetchPage, assertIdArray,
  bulkUpdate as bulkUpdateOn, fetchBulkUpdateSchema,
} from './client';

// The DRF resource these rows come from. Shared by the mass-update and
// filter_spec surfaces so the path is declared once.
export const RESOURCE = 'delegates';

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
    discount: d.discount,
    add_ons: d.add_ons || '',
    reference: d.reference || '',
    event_name: d.event_name || '',
    transfer_to_event: '',
    added_time: d.created_at,
    modified_time: d.updated_at,
    owner: d.sales_executive_name || '—',
    checked_in: 'No',
    position: d.position || '',
    attendance: d.attendance,
    delegate_count: d.delegate_count,
    source: d.source,
  };
}

function invoiceToBackend(meta) {
  return {
    invoice_number: meta.invoice_number,
    event_code: meta.event_code,
    event_date: meta.event_date || null,
    request_date: meta.request_date || null,
    invoice_date: meta.invoice_date || null,
    booking_code: meta.booking_code || '',
    company_name: meta.company_name || '',
    contact_name: meta.name || meta.contact_name || '',
    contact_email: meta.email || meta.contact_email || '',
    contact_phone: meta.phone_number || '',
    accounts_contact_email: meta.accounts_contact_email || '',
    currency: meta.currency || 'USD',
    ticket_tier: meta.ticket_tier || '',
    payment_status: meta.payment_status || 'Pending',
    payment_type: meta.payment_type || '',
    payment_date: meta.payment_date || null,
    paid_or_free: meta.paid_or_free || '',
    discount: meta.discount || 0,
    reference: meta.reference || '',
    source: meta.source || 'manual',
  };
}

// The UI presents discount as a percent string ('10%', '25%', ...) but the
// backend's `discount` column is a plain DecimalField (book_delegate/models.py)
// with no '%' handling — sending the raw string 400s on every create/update.
function discountToNumber(v) {
  if (v == null || v === '') return 0;
  const n = parseFloat(String(v).replace('%', ''));
  return Number.isFinite(n) ? n : 0;
}

function delegateToBackend(d) {
  const out = {
    id: (d.id && String(d.id).match(/^\d+$/)) ? d.id : undefined,
    first_name: d.name ? d.name.split(' ')[0] : d.first_name,
    last_name: d.name ? d.name.split(' ').slice(1).join(' ') : d.last_name || '',
    email: d.email,
    phone_number: d.phone_number || '',
    position: d.position || '',
    ticket_package: d.ticket_package || '',
    sponsorship_level: d.sponsorship_level || '',
    attendance: d.attendance || 'Pending',
    notes: d.notes || '',
    dietary_requirements: d.dietary_requirements || '',
    discount: discountToNumber(d.discount),
    add_ons: d.add_ons || '',
    reference: d.reference || '',
    delegate_payment_status: d.payment_status || d.delegate_payment_status,
    delegate_payment_type: d.payment_type || d.delegate_payment_type,
    delegate_ticket_tier: d.ticket_tier || d.delegate_ticket_tier,
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
export function countsByPaymentStatus(statuses) {
  return Promise.all([
    count(null),
    ...statuses.map((s) => count([{ field: 'payment_status', op: 'is', value: s }])),
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
 */
export function count(criteria) {
  return fetchPage(`${RESOURCE}/`, {
    page: 1,
    pageSize: 1,
    filterSpec: criteria ? JSON.stringify({ match: 'all', criteria }) : null,
  }).then((r) => r.count);
}

/** Convenience for the shell badges: how many bookings are awaiting payment. */
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
  if (patch.discount !== undefined) body.discount = discountToNumber(patch.discount);
  if (patch.attendance !== undefined) body.attendance = patch.attendance;
  if (patch.add_ons !== undefined) body.add_ons = patch.add_ons;
  if (patch.reference !== undefined) body.reference = patch.reference;
  if (!Object.keys(body).length) return Promise.resolve(null);
  return http.patch(`delegates/${id}/`, body).then((r) => r.data);
}

export function markPaid(id) {
  return update(id, { payment_status: 'Paid', payment_date: new Date().toISOString().slice(0, 10) });
}
export function bulkMarkPaid(ids) {
  // Guarded like bulkRemove and bulkUpdate. A Set would reach `.map` and throw a
  // bare "ids.map is not a function" with no indication of which call site or
  // what type arrived; assertIdArray names both.
  assertIdArray(ids, 'bookings.bulkMarkPaid');
  return Promise.all(ids.map((id) => markPaid(id).catch(() => null)));
}
export function bulkRemove(ids) {
  // Guarded for the same reason bulkUpdate is: this posts the collection as JSON,
  // and a Set has no enumerable own properties, so JSON.stringify turns it into
  // {} — the backend then answers "ids list required" and the delete silently
  // does nothing. Throw at the call site with the real type named instead.
  assertIdArray(ids, 'bookings.bulkRemove');
  return http.post('delegates/bulk_delete/', { ids }).then((r) => r.data);
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
  const body = { ...invoiceToBackend(meta), delegates: delegates.map(delegateToBackend) };
  return http.post('invoices/', body).then((r) => r.data);
}

export function saveInvoiceDelegates(invoiceNumber, meta, delegates, bookEventId) {
  const body = { ...invoiceToBackend(meta), delegates: delegates.map(delegateToBackend) };
  return http.patch(`invoices/${bookEventId}/`, body).then((r) => r.data);
}

export function removeInvoice(bookEventId) {
  return http.delete(`invoices/${bookEventId}/`).then(() => true);
}

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
