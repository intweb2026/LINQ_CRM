// Global search — GET /api/search/ (config/views.py GlobalSearchView).
//
// Searches invoices, delegates and events in one request, and is RBAC-scoped
// server-side: a sales user's results are restricted to their assigned event
// codes. That scoping is the main reason to use this rather than filtering
// client-side — a browser-side filter can only ever search rows the browser
// already holds.
//
// The backend also returns a `companies` bucket to admins, but there is no
// Companies page to open a hit in, so CommandPalette does not read it.
import { http } from './client';

// The backend rejects anything shorter with a 400.
export const MIN_QUERY = 2;

/**
 * Returns { query, total, results: { invoices, delegates, events, companies } },
 * where each bucket is { count, items }. Buckets are absent when the caller has
 * no access to that type, so read them defensively. `companies` is admin-only and
 * currently unused — see the note above.
 */
export function global(q, { limit = 6, signal } = {}) {
  return http
    .get('search/', { params: { q, limit }, signal })
    .then((r) => r.data);
}

export default global;
