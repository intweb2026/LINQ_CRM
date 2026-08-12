// Real backend: /api/tickets/ (see backend/ticket_central/serializers.py).
// Field names match this UI's TICKETS shape almost 1:1 — the backend picks
// the write-serializer per the caller's role automatically, so `update()`
// just PATCHes whatever fields are given.
//
// Known gap: `source_event` has no backend equivalent — defaulted to ''.
import { assertIdArray, http, fetchAllPages, fetchPage } from './client';

function toFrontend(t) {
  return { ...t, source_event: t.source_event || '' };
}

/** Map a raw API row to the shape the table reads (adds the UI-only source_event). */
export const fromApi = toFrontend;

export const list = () => fetchAllPages('tickets/').then((rows) => rows.map(toFrontend));

export const stats = () => http.get('tickets/stats/').then((r) => r.data);

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
  // `source_event`/`ticket_number` are UI-only concepts with no backend
  // equivalent (event_code is the real field; ticket_number is server-assigned).
  const { source_event, ticket_number, event_name, ...rest } = payload;
  const body = { ...rest, event_code: source_event || payload.event_code, event_name: event_name || '' };
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
