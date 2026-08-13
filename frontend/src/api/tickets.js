// Real backend: /api/tickets/ (see backend/ticket_central/serializers.py).
// Field names match this UI's shape 1:1 — the backend picks the write-serializer
// per the caller's role automatically, so `update()` just PATCHes whatever fields
// are given, and it refuses a patch that mixes the MR and DMD sections (the ticket
// form sends only the fields that actually changed, for that reason).
import { assertIdArray, http, fetchAllPages, fetchPage } from './client';

// Nothing to remap: the list serializer already names every field the way the
// table and the form read it. Kept as the single hook for any future rename so
// DataTable's `mapRow` contract stays in one place.
function toFrontend(t) {
  return t;
}

/** Map a raw API row to the shape the table reads. */
export const fromApi = toFrontend;

export const list = () => fetchAllPages('tickets/').then((rows) => rows.map(toFrontend));

export const stats = () => http.get('tickets/stats/').then((r) => r.data);

// DELETE /api/tickets/clear_all/ — the backend restricts this to the HP account
// (accounts/permissions.py IsHPAccount) and it also resets the ticket-number
// sequences, so the next ticket after a wipe numbers from 1 rather than 35,000.
export const clearAll = () => http.delete('tickets/clear_all/').then((r) => r.data);

export function update(id, patch) {
  return http.patch(`tickets/${id}/`, patch).then((r) => r.data);
}
export function submitToDMD(id) {
  return http.post(`tickets/${id}/submit_mr/`, {}).then((r) => r.data);
}
export function returnToMR(id, reason = '') {
  return http.post(`tickets/${id}/return_to_mr/`, { reason }).then((r) => r.data);
}
export function markComplete(id) {
  return http.post(`tickets/${id}/submit_dmd/`, {}).then((r) => r.data);
}
export function create(payload) {
  // ticket_number is server-assigned (purpose + type code, gaps reused — see
  // utils.assign_next_ticket_number), so a caller-supplied one is dropped rather
  // than sent to a serializer that has no such field.
  const { ticket_number, ...body } = payload;
  return http.post('tickets/', body).then((r) => toFrontend(r.data));
}
export function bulkSubmit(ids) {
  assertIdArray(ids, 'tickets.bulkSubmit');
  return Promise.all(ids.map((id) => submitToDMD(id).then(() => 1).catch(() => 0)))
    .then((results) => results.reduce((a, b) => a + b, 0));
}

/**
 * The `n` most recently completed tickets, for the dashboard activity feed.
 *
 * Uses filter_spec rather than a query param because TicketViewSet carries
 * FilterSpecMixin, so `status` is validated against its registry server-side and
 * an unknown value is a clean 400 instead of a silently unfiltered list.
 */
export function recentCompleted(n = 5) {
  return fetchPage('tickets/', {
    pageSize: n,
    filterSpec: JSON.stringify({
      match: 'all',
      criteria: [{ field: 'status', op: 'is', value: 'completed' }],
    }),
  }).then((r) => r.results.map(toFrontend));
}
