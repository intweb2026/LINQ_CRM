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

export function update(id, patch) {
  const body = {};
  if (patch.role !== undefined) body.role = patch.role;
  if (patch.status !== undefined) body.status = patch.status;
  if (patch.team_id !== undefined) body.team_id = patch.team_id;
  if (patch.is_lead !== undefined) body.is_team_lead = patch.is_lead;
  if (patch.custom_role_id !== undefined) body.custom_role_id = patch.custom_role_id;
  return http.patch(`users/${id}/`, body).then((r) => toFrontend(r.data));
}

export function toggleStatus(id) {
  return http.patch(`users/${id}/toggle-status/`, {}).then((r) => toFrontend(r.data));
}

// TODO(developer): the backend has no self-service "invite by email" flow —
// admins create accounts directly via POST /api/users/. Wire a real modal
// (username/email/password/role) against usersApi.create once designed;
// left as a no-op toast for now rather than pretending an email went out.
export function inviteByEmail(emails, role, teamId) {
  return Promise.resolve(false);
}

export function create(payload) {
  return http.post('users/', payload).then((r) => toFrontend(r.data));
}

export function remove(id) {
  return http.delete(`users/${id}/`).then(() => true);
}
