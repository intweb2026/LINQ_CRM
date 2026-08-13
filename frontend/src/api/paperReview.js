// Paper Review — real REST module following this codebase's standard
// conventions (see api/events.js, api/proposalSubmission.js). The backend
// DOES exist and is live: paper_review is in INSTALLED_APPS and
// PaperReviewViewSet is registered at /api/paper-reviews/ (config/urls.py).
// (An earlier version of this comment said the endpoint was unbuilt and pointed
// at a spec document — see api/proposalSubmission.js's own note on carrying that
// same stale comment over by mistake; both are corrected now.)
import { fetchAllPages, http } from './client';

// fetchAllPages, not `r.data.results` — the list endpoint is paginated at 50
// (config/pagination.py), so reading `results` off page 1 silently returns the
// first 50 rows and the table reads as "there are only 50 reviews" with nothing
// indicating more exist. Same fix proposalSubmission.js's own comment documents.
export const list = () => fetchAllPages('paper-reviews/');

export const get = (id) => http.get(`paper-reviews/${id}/`).then((r) => r.data);

export function create(payload) {
  return http.post('paper-reviews/', payload).then((r) => r.data);
}
export function update(id, payload) {
  return http.patch(`paper-reviews/${id}/`, payload).then((r) => r.data);
}
export function remove(id) {
  return http.delete(`paper-reviews/${id}/`).then(() => true);
}

// DELETE /api/paper-reviews/clear_all/ — HP only (accounts/permissions.py
// IsHPAccount), and the WHOLE table rather than the caller's event scope. The
// proposals this module generated survive: the FK is SET_NULL, so they are unlinked
// and remain Proposal Submission's data, with its own wipe. The response reports
// `proposals_unlinked` for that reason.
export const clearAll = () => http.delete('paper-reviews/clear_all/').then((r) => r.data);

// The event codes this user may actually attach a review to — NOT the whole
// catalogue. The form picker reads this because access.py is the only authority
// on scope: offering all 142 events to a scoped user means every code they are
// not assigned to answers 400 on save, which reads as a broken module rather
// than a scoped one. Single-sourced from the same predicate the validator uses,
// so picker and validator cannot disagree.
export const permittedEvents = () =>
  http.get('paper-reviews/permitted_events/').then((r) => r.data.results || []);

// ── Import (two-phase) ──────────────────────────────────────────────────────
// The server caps one call at 500 rows; the modal chunks anything larger and
// carries ONE import_batch_id across every chunk of the same file.
export const IMPORT_MAX_ROWS = 500;

export function importPreview(rows, importBatchId) {
  const body = { rows };
  if (importBatchId) body.import_batch_id = importBatchId;
  return http.post('paper-reviews/import/preview/', body).then((r) => r.data);
}

export function importCommit(rows, planHash, importBatchId, filename) {
  return http.post('paper-reviews/import/commit/', {
    rows, plan_hash: planHash, import_batch_id: importBatchId, filename,
  }).then((r) => r.data);
}
