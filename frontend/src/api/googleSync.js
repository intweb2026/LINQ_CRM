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

// User-defined pushes: one module's selected columns into one tab.
// Backend: backend/google_sync/views.py SheetSyncTargetViewSet + SyncCatalogView.

/** Modules and their selectable columns. The pickers are drawn from this. */
export const catalog = () => http.get('google-sync/catalog/').then((r) => r.data.modules);

export const listTargets = () => fetchAllPages('google-sync/targets/');

export function createTarget(body) {
  return http.post('google-sync/targets/', body).then((r) => r.data);
}
export function updateTarget(id, body) {
  return http.patch(`google-sync/targets/${id}/`, body).then((r) => r.data);
}
export function deleteTarget(id) {
  return http.delete(`google-sync/targets/${id}/`).then((r) => r.data);
}
export function runTarget(id) {
  return http.post(`google-sync/targets/${id}/run/`, {}).then((r) => r.data);
}
