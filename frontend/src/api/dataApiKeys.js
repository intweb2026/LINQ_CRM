// Real backend: /api/data/keys/ (see backend/dataapi/views.py
// DataApiKeyManagementViewSet). Admin-only, session/token authenticated.
//
// NOTE the surface this does NOT touch. The sibling routes under /api/data/
// (bookings, delegates, events, tickets) are the export surface and authenticate
// with an X-DATA-API-KEY header instead; nothing in this module sends that
// header, and the keys it mints are never used by this app.
import { http } from './client';

/** Newest first. No pagination: the key table is tens of rows, not thousands. */
export const list = () => http.get('data/keys/').then((r) => r.data);

/**
 * Returns the created row PLUS `raw_key`, which exists in this one response and
 * nowhere else. The caller must show it to the admin before discarding it; there
 * is no endpoint that can produce it again.
 *
 * payload: { name, scopes: string[], expires_at?: ISO string | null }
 */
export const create = (payload) => http.post('data/keys/', payload).then((r) => r.data);

/** One-way. There is no un-revoke, by design on the backend. */
export const revoke = (id) => http.post(`data/keys/${id}/revoke/`).then((r) => r.data);

/**
 * The resources a key can be scoped to, mirroring DATA_API_SCOPES in
 * backend/dataapi/models.py. The create form is drawn from this, and the
 * backend rejects anything outside it, so a value added there must be added
 * here too or the UI simply will not offer it.
 */
export const SCOPES = ['bookings', 'delegates', 'events', 'tickets'];
