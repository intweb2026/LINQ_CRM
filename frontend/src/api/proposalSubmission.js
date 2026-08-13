// Proposal Submission — real REST module following this codebase's standard
// conventions (see api/events.js).
//
// The backend DOES exist and is live: proposal_submission is in INSTALLED_APPS
// and ProposalSubmissionViewSet is registered at /api/proposal-submissions/
// (config/urls.py). The field names below match proposal_submission/serializers.py
// EDITABLE_FIELDS exactly. (An earlier version of this comment said the endpoint
// was unbuilt and pointed at a spec document — that was true of api/paperReview.js,
// which really has no backend, and was carried over here by mistake.)
//
// Not yet used by the UI, but present on the ViewSet if wanted:
//   GET  proposal-submissions/filter_options/
//   GET  proposal-submissions/permitted_events/
//   GET  proposal-submissions/export/
//   POST proposal-submissions/import/preview/  and  import/commit/
//   POST proposal-submissions/bulk_update/
//   POST proposal-submissions/{id}/duplicate/
import { fetchAllPages, http } from './client';

// fetchAllPages, not `r.data.results` — the list endpoint is paginated at 50
// (config/pagination.py), so reading `results` off page 1 silently returns the
// first 50 rows and the table reads as "there are only 50 proposals" with
// nothing indicating more exist.
export const list = () => fetchAllPages('proposal-submissions/');

export const get = (id) => http.get(`proposal-submissions/${id}/`).then((r) => r.data);

export function create(payload) {
  return http.post('proposal-submissions/', payload).then((r) => r.data);
}
export function update(id, payload) {
  return http.patch(`proposal-submissions/${id}/`, payload).then((r) => r.data);
}
export function remove(id) {
  return http.delete(`proposal-submissions/${id}/`).then(() => true);
}

// DELETE /api/proposal-submissions/clear_all/ — HP only (accounts/permissions.py
// IsHPAccount), and the WHOLE table rather than the caller's event scope. Paper
// reviews are untouched; the ones that generated proposals through the bridge will
// regenerate them if those reviews are re-imported.
export const clearAll = () => http.delete('proposal-submissions/clear_all/').then((r) => r.data);

// The event codes this user may actually attach a proposal to — NOT the whole
// catalogue. The endpoint existed on the ViewSet from the start but nothing read
// it, so the form picker offered all 142 events and every code the user was not
// assigned to answered 400 on save. Single-sourced from the same access.py
// predicate the validator uses, so picker and validator cannot disagree.
export const permittedEvents = () =>
  http.get('proposal-submissions/permitted_events/').then((r) => r.data.results || []);

// ── Import (two-phase) ──────────────────────────────────────────────────────
// Mirrors api/paperReview.js. The endpoints existed on the ViewSet
// (proposal_submission/views.py:613, :629) but nothing exported a client for
// them, so ProposalImportModal.jsx destructured `proposalSubmissionApi` and
// `IMPORT_MAX_ROWS` from this module and got undefined for both.
//
// The server caps one call at 500 rows; the modal chunks anything larger and
// carries ONE import_batch_id across every chunk of the same file.
export const IMPORT_MAX_ROWS = 500;

export function importPreview(rows, importBatchId) {
  const body = { rows };
  if (importBatchId) body.import_batch_id = importBatchId;
  return http.post('proposal-submissions/import/preview/', body).then((r) => r.data);
}

export function importCommit(rows, planHash, importBatchId, filename) {
  return http.post('proposal-submissions/import/commit/', {
    rows, plan_hash: planHash, import_batch_id: importBatchId, filename,
  }).then((r) => r.data);
}
