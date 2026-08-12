// Real backend: /api/event-performance/ (see backend/event_performance/views.py + services.py).
// Admin-only. `list()` returns live delegate/revenue metrics per event but no
// follow-up/mailshot/note/rep counts (those require the per-event `detail`
// call) — the table shows 0 for those columns until a row's drawer is opened
// and fetches the real counts, rather than fabricating a number.
import { http } from './client';

export const list = () => http.get('event-performance/').then((r) => r.data);

export const detail = (eventCode) => http.get(`event-performance/${encodeURIComponent(eventCode)}/`).then((r) => r.data);
