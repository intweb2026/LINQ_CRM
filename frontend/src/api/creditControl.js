// Real backend: /api/credit-control/ (see backend/credit_control/views.py).
//
// A LEAD IS AN INVOICE, and its id is the invoice number, which is why every
// path here interpolates a string rather than a numeric id. One invoice is one
// phone call however many delegates sit under it, and the delegates arrive
// inside the row for the drill-down rather than as a second request.
//
// THE QUEUE IS NOT PAGINATED. Four buckets of one table, and a caller works
// their whole list in a shift; the server caps the response instead. The
// dashboard is one aggregate for the same reason the Mining Matrix is: its
// totals are over the entire active set, and a paginated total is not a total.
//
// Nothing here talks to HubSpot or Anthropic. Phone numbers and call counts
// come back from the backend's own cache, refreshed by cron, so a slow HubSpot
// can never make this page hang.
import { http } from './client';

export const BUCKETS = {
  ACTIVE: 'active',
  NOT_INVOICED: 'not_invoiced',
  SPEX: 'spex',
  DONE: 'done',
};

export const BUCKET_LABEL = {
  active: 'Active',
  not_invoiced: 'Not invoiced yet',
  spex: 'SpEx & Speaker Table',
  // "Resolved", not "Done". It says the debt was settled rather than that we
  // stopped looking at it, matching the nav label and the page heading.
  done: 'Resolved',
};

/** One bucket's rows. `mine` narrows an all-access caller to their own queue. */
export const list = ({ bucket = BUCKETS.ACTIVE, mine = false } = {}) =>
  http
    .get('credit-control/leads/', {
      params: { bucket, ...(mine ? { mine: 1 } : {}) },
    })
    .then((r) => r.data);

/**
 * Save one caller's edit.
 *
 * Deliberately per-field-set and immediate. Every edit is durable the moment it
 * is typed, which is what makes the whole "my remark vanished on refresh" class
 * of bug impossible: the routing pass reads the database, and the database
 * already has the edit. The server accepts only the four caller-owned fields,
 * so nothing here can move a lead to another queue.
 */
export const save = (invoiceNumber, changes) =>
  http
    .patch(`credit-control/leads/${encodeURIComponent(invoiceNumber)}/`, changes)
    .then((r) => r.data);

/**
 * The human verdict on Blind versus Requested.
 *
 * Its own endpoint because it is a different question from a daily edit: it
 * overrules the classifier permanently, and no automated path may write it.
 * Send an empty string to clear the override and fall back to the classifier's.
 */
export const setInvoiceType = (invoiceNumber, value) =>
  http
    .patch(
      `credit-control/leads/${encodeURIComponent(invoiceNumber)}/invoice-type/`,
      { invoice_type_override: value },
    )
    .then((r) => r.data);

export const dashboard = () =>
  http.get('credit-control/dashboard/').then((r) => r.data);

export const dispositions = () =>
  http.get('credit-control/dispositions/').then((r) => r.data.results || []);

/** Run the routing pass now. Idempotent, so a double click is harmless. */
export const refresh = () =>
  http.post('credit-control/refresh/').then((r) => r.data);

/**
 * Run ONE scheduled job now. Admin only; the server refuses everybody else.
 *
 * `job` is a key from the dashboard's own schedule rows, and the server holds a
 * whitelist rather than accepting a command name, so a key it does not know is
 * a 400 rather than something it tries to execute.
 *
 * The HubSpot and classifier jobs talk to third parties and can take a minute,
 * so this resolves when the job has actually finished and returns its output.
 */
export const runJob = (job) =>
  http.post(`credit-control/run/${encodeURIComponent(job)}/`).then((r) => r.data);
