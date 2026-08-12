// Real backend: /api/team/ (see backend/accounts/views.py TeamViewSet) —
// admin-only aggregated sales performance. Non-admin callers get a 403;
// this page has no permission gate in the UI by design, so callers should
// treat a failed fetch as "nothing to show" rather than an error banner.
//
// Known gap: no per-rep activity feed exists on the backend for this view
// (TeamActivityLog is team-based, not per-sales-rep) — always [].
import { http } from './client';

async function withEvents(row) {
  const { data } = await http.get(`team/${row.id}/`);
  return {
    id: row.id, username: row.username, email: row.email, role: 'sales',
    total_events: row.total_events, events: data.events, activity: [],
  };
}

export async function list() {
  const { data } = await http.get('team/');
  return Promise.all(data.map(withEvents));
}
