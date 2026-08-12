// Real backend: /api/google-sync/ (see backend/google_sync/views.py + serializers.py).
// Field names match this UI's log shape 1:1.
import { http, fetchAllPages } from './client';

export const list = () => fetchAllPages('google-sync/logs/');
export const status = () => http.get('google-sync/status/').then((r) => r.data);

export function retry(id) {
  return http.post(`google-sync/retry/${id}/`, {}).then((r) => r.data);
}
export function run(type) {
  return http.post('google-sync/run/', { sync_type: type }).then((r) => r.data);
}
