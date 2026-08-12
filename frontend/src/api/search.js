// Global search — GET /api/search/ (config/views.py GlobalSearchView).
//
// Searches invoices, delegates, events and companies in one request, and is
// RBAC-scoped server-side: a sales user's results are restricted to their
// assigned event codes, and company results are admin-only. That scoping is the
// main reason to use this rather than filtering client-side — a browser-side
// filter can only ever search rows the browser already holds.
import { http } from './client';

// The backend rejects anything shorter with a 400.
export const MIN_QUERY = 2;

/**
 * Returns { query, total, results: { invoices, delegates, events, companies } },
 * where each bucket is { count, items }. Buckets are absent when the caller has
 * no access to that type, so read them defensively.
 */
export function global(q, { limit = 6, signal } = {}) {
  return http
    .get('search/', { params: { q, limit }, signal })
    .then((r) => r.data);
}

export default global;
