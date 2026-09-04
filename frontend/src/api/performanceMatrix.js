// Real backend: /api/performance-matrix/ (backend/performance_matrix/views.py +
// services.py). Admin only on every call.
//
// One request per view and no pagination: the matrix is read across the whole
// set and the totals bar is over every row. The verdict is the one thing written
// from here; it lands on the Event row itself (events.verdict), so the Events
// module and the matrix can never disagree about it.
import { http } from './client';

export const VIEWS = { UPCOMING: 'upcoming', ALL: 'all' };

// Mirrors Event.Verdict in backend/events/models.py. `slug` picks the row tint in
// styles/components.css [performance_matrix]; Standby carries none on purpose.
export const VERDICTS = [
  { v: 'Standby', slug: '' },
  { v: 'Going Ahead', slug: 'ahead' },
  { v: 'Needs a push', slug: 'push' },
  { v: 'Full Efforts Req.', slug: 'full' },
  { v: 'Postponed', slug: 'postponed' },
  { v: 'TBP', slug: 'tbp' },
  { v: 'Cancelled', slug: 'cancelled' },
];
export const VERDICT_NAMES = VERDICTS.map((x) => x.v);

export function verdictClass(value) {
  const slug = (VERDICTS.find((x) => x.v === value) || {}).slug;
  return slug ? 'vd vd-' + slug : '';
}

export const list = (view = VIEWS.UPCOMING) =>
  http.get('performance-matrix/', { params: { view } }).then((r) => r.data);

export const setVerdict = (eventId, verdict) =>
  http.patch(`performance-matrix/${eventId}/verdict/`, { verdict }).then((r) => r.data);

