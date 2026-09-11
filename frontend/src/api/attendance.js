import { fetchPage, http } from './client';

/** The one place the three outcome codes are spelled, so nothing retypes them. */
export const RESULTS = {
  CHECKED_IN: 'checked_in',
  ALREADY: 'already_checked_in',
  WRONG_EVENT: 'wrong_event',
  INVALID_QR: 'invalid_qr',
  INVALID_EVENT: 'invalid_event',
  FORBIDDEN_EVENT: 'forbidden_event',
  UNREACHABLE: 'unreachable',
};

/** The events this caller may work. Empty for a non-admin is an assignment problem. */
export const events = () =>
  http.get('attendance/events/').then((r) => r.data);

/**
 * The confirmed roster for one event, each row carrying its badge token.
 *
 * `search` is sent to the server rather than filtered here: the server splits
 * the term on whitespace and ANDs the tokens across the name, company and email
 * columns, which a client-side `includes` over one joined string cannot do —
 * the stored names carry stray internal whitespace.
 */
export const roster = (event, search) =>
  http
    .get('attendance/roster/', { params: { ...scope(event), search: search || undefined } })
    .then((r) => r.data);

/** Expected against arrived. */
export const summary = (event) =>
  http.get('attendance/summary/', { params: scope(event) }).then((r) => r.data);

/** One page of the arrival log. */
export const log = (event, opts = {}) =>
  fetchPage('attendance/', { ...opts, params: { ...scope(event), ...(opts.params || {}) } });

/**
 * Check somebody in.
 *
 * RESOLVES ON EVERY SERVER ANSWER, including the refusals, because each one is
 * a card the operator has to read rather than an exception. Only a transport
 * failure rejects — and it comes back as its own result code, so the caller
 * renders one thing per outcome and never has to tell a 409 from a dropped
 * connection.
 */
export async function scan(event, payload, source) {
  try {
    const { data } = await http.post('attendance/scan/', {
      ...scope(event), payload, ...(source ? { source } : {}),
    });
    return data;
  } catch (err) {
    if (err?.response?.data?.result) return err.response.data;
    return {
      result: RESULTS.UNREACHABLE,
      detail: 'Could not reach the server. The check-in was not recorded.',
    };
  }
}

/** Mint one printable badge token for somebody on this event. */
export const qrToken = (event, delegateId) =>
  http
    .post('attendance/qr_token/', { ...scope(event), delegate_id: delegateId })
    .then((r) => r.data);

/** Who would receive a QR email right now, and whether Gmail is connected. */
export const qrEmailPreview = (event) =>
  http.get('attendance/qr_email_preview/', { params: scope(event) }).then((r) => r.data);

/** Email every eligible confirmed attendee their QR badge. */
/**
 * The real email the first eligible recipient will get, QR and all, rendered
 * from `values` — the variables as the SCA just edited them.
 *
 * POST because those values are the point; it writes nothing, and nothing is
 * saved back to the event.
 */
export const qrEmailSample = (event, values) =>
  http.post('attendance/qr_email_sample/', { ...scope(event), ...values })
    .then((r) => r.data);

/** `values` must be the ones the preview was built from, or a different email goes out. */
export const sendQrEmails = (event, values) =>
  http.post('attendance/send_qr_emails/', { ...scope(event), ...values })
    .then((r) => r.data);

/**
 * The two params every per-event endpoint takes.
 *
 * `edition` is sent only when it has a value: most delegate rows carry none, and
 * an empty one would filter on it rather than being ignored. The server checks
 * BOTH halves against the caller's assignment, so a code without its edition is
 * refused rather than widened.
 */
function scope(event) {
  return {
    event_code: event?.event_code,
    ...(event?.edition ? { edition: event.edition } : {}),
  };
}

/** The label an event reads as in the picker. Same shape as Pre-Event Docs. */
export const eventLabel = (e) =>
  !e ? '' : [e.event_code, e.edition].filter(Boolean).join(' ') +
    (e.event_name ? ` — ${e.event_name}` : '');
