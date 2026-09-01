// Real backend: /api/tickets/ (see backend/ticket_central/serializers.py).
// Field names match this UI's shape 1:1 — the backend picks the write-serializer
// per the caller's role automatically, so `update()` just PATCHes whatever fields
// are given, and it refuses a patch that mixes the MR and DMD sections (the ticket
// form sends only the fields that actually changed, for that reason).
import { assertIdArray, chunk, http, fetchAllPages, fetchPage, mapLimit } from './client';

/**
 * submit_dmd requests in flight at once.
 *
 * There is no batch submit endpoint, so bulkSubmit issues one POST per ticket.
 * That was Promise.all over a one-page selection; the header checkbox now
 * selects every matching row, and Ticket Central holds ~35,690 — opening that
 * many at once locks the tab and buries the API from a single click.
 */
const SUBMIT_CONCURRENCY = 6;

// Nothing to remap: the list serializer already names every field the way the
// table and the form read it. Kept as the single hook for any future rename so
// DataTable's `mapRow` contract stays in one place.
function toFrontend(t) {
  return t;
}

/** Map a raw API row to the shape the table reads. */
export const fromApi = toFrontend;

export const list = () => fetchAllPages('tickets/').then((rows) => rows.map(toFrontend));

// `period` is a DASH_PERIODS key and reaches TicketViewSet.stats verbatim, which
// applies the SAME window as the list. These counts label the tabs directly above
// the filtered table, so they have to answer the same question the rows do —
// "Completed 35,690" over eleven visible rows is the defect this avoids.
export const stats = (period) => http.get('tickets/stats/', { params: { period } }).then((r) => r.data);

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
/** Matches the cap in ticket_central/views.py bulk_delete; past it, a 400. */
const BULK_DELETE_MAX = 1000;

/** Delete one ticket. TicketViewSet.perform_destroy writes the ActionLog row. */
export function remove(id) {
  return http.delete(`tickets/${id}/`).then(() => true);
}

/**
 * Delete tickets, in batches the endpoint will accept.
 *
 * A single row goes through DELETE /tickets/{id}/ rather than bulk_delete, and
 * that is not a micro-optimisation: bulk_delete is IsAdminRole, while the
 * per-row destroy is gated by crm_permission('ticket_central') — so routing one
 * row through the batch endpoint would 403 an MR deleting a ticket they are
 * allowed to delete. Batches are sequential, so the totals returned describe
 * what actually happened up to any failure rather than a half-known state.
 */
export async function bulkRemove(ids) {
  assertIdArray(ids, 'tickets.bulkRemove');
  if (ids.length === 1) {
    await remove(ids[0]);
    return { deleted: 1, requested: 1, permitted: 1, out_of_scope: 0 };
  }
  const totals = { deleted: 0, requested: 0, permitted: 0, out_of_scope: 0 };
  for (const batch of chunk(ids, BULK_DELETE_MAX)) {
    // eslint-disable-next-line no-await-in-loop
    const res = await http.post('tickets/bulk_delete/', { ids: batch }).then((r) => r.data);
    totals.deleted += res.deleted || 0;
    totals.requested += res.requested || 0;
    totals.permitted += res.permitted || 0;
    totals.out_of_scope += res.out_of_scope || 0;
  }
  return totals;
}

export function bulkSubmit(ids) {
  assertIdArray(ids, 'tickets.bulkSubmit');
  return mapLimit(ids, SUBMIT_CONCURRENCY, (id) => submitToDMD(id).then(() => 1).catch(() => 0))
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
