// Google Sheet sources — /api/reports/sources/ (backend/reports/views.py
// GoogleSheetSourceViewSet).
//
// The only caller is AddSheetModal, reached from the Google Sync page.
// These two calls are all that is left of the old api/reports.js: the Reports
// page that listed, synced and previewed these sources is gone, so a source
// registered here is a stored connection and nothing reads its rows.
import { http } from './client';

/**
 * `frequency` must be one of the backend's real SyncFrequency choices
 * (manual/hourly/daily/weekly) — see GoogleSheetSource.SyncFrequency. Both
 * sheet_id and sheet_url are sent as the raw pasted URL; the serializer's
 * create() re-extracts sheet_id from it and keeps sheet_url intact for
 * display, so this populates both fields correctly in one request.
 */
export function addSource(payload) {
  return http.post('reports/sources/', {
    name: payload.name,
    sheet_id: payload.url || '',
    sheet_url: payload.url || '',
    worksheet_name: payload.worksheet || 'Sheet1',
    sheet_type: payload.type || 'custom',
    sync_frequency: payload.frequency || 'manual',
    sync_enabled: payload.syncEnabled !== false,
    description: payload.description || '',
    notes: payload.notes || '',
  }).then((r) => r.data);
}

export function listWorksheets(url) {
  return http.post('reports/sources/list-worksheets/', { sheet_url: url }).then((r) => r.data.worksheets || []);
}
