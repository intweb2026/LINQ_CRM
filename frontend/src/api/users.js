// Real backend: /api/users/ (see backend/accounts/serializers.py —
// UserListSerializer / UserWriteSerializer, and accounts/views.py for the
// extra actions below).
import { http, fetchAllPages } from './client';

function toFrontend(u) {
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    name: u.full_name || u.username,
    // Kept alongside `name` because the edit form has to round-trip these two
    // exactly. Splitting `name` back apart is not equivalent: full_name falls
    // back to the USERNAME for anyone with no first/last name set, so the split
    // would write the username into first_name on the next save.
    first_name: u.first_name || '',
    last_name: u.last_name || '',
    role: u.role,
    status: u.status || (u.is_active ? 'active' : 'inactive'),
    team_id: u.team_id || null,
    is_lead: !!u.is_team_lead,
    events_count: u.assigned_events_count || 0,
    assigned_events: u.assigned_events || [],
    last_login: u.last_login,
    custom_role_id: u.custom_role_id,
    custom_role_label: u.custom_role_label,
  };
}

// GET /api/users/my-permissions/ — used by SessionContext.
export const myPermissions = () => http.get('users/my-permissions/').then((r) => r.data);

export const list = () => fetchAllPages('users/').then((rows) => rows.map(toFrontend));

/**
 * Translate the frontend user shape into the backend's write shape.
 *
 * Only KEYS THAT ARE PRESENT are forwarded, so a PATCH that names one field
 * touches one field. `password` is dropped when blank: the edit form always
 * renders its "new password" box, and an untouched box must not clear the
 * password. `null` is meaningful for team_id / custom_role_id — it unassigns —
 * so those are tested for `undefined`, not for truthiness.
 */
function toBackend(patch) {
  const body = {};
  if (patch.username !== undefined) body.username = patch.username;
  if (patch.email !== undefined) body.email = patch.email;
  if (patch.first_name !== undefined) body.first_name = patch.first_name;
  if (patch.last_name !== undefined) body.last_name = patch.last_name;
  if (patch.role !== undefined) body.role = patch.role;
  if (patch.status !== undefined) body.status = patch.status;
  if (patch.team_id !== undefined) body.team_id = patch.team_id;
  if (patch.is_lead !== undefined) body.is_team_lead = patch.is_lead;
  if (patch.custom_role_id !== undefined) body.custom_role_id = patch.custom_role_id;
  if (patch.password) body.password = patch.password;
  return body;
}

export function update(id, patch) {
  return http.patch(`users/${id}/`, toBackend(patch)).then((r) => toFrontend(r.data));
}

// PATCH users/{id}/toggle-status/ with no body flips active <-> inactive; pass
// a status to set one explicitly (the backend also accepts "suspended").
export function toggleStatus(id, status) {
  return http.patch(`users/${id}/toggle-status/`, status ? { status } : {}).then((r) => toFrontend(r.data));
}

export function resetPassword(id, password) {
  return http.patch(`users/${id}/reset-password/`, { password, confirm_password: password }).then((r) => r.data);
}

// TODO(developer): the backend has no self-service "invite by email" flow —
// admins create accounts directly via POST /api/users/ (the Add user form).
// Left as a no-op rather than pretending an email went out.
export function inviteByEmail(emails, role, teamId) {
  return Promise.resolve(false);
}

export function create(payload) {
  return http.post('users/', toBackend(payload)).then((r) => toFrontend(r.data));
}

export function remove(id) {
  return http.delete(`users/${id}/`).then(() => true);
}
