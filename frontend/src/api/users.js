// Real backend: /api/users/ (see backend/accounts/serializers.py —
// UserListSerializer / UserWriteSerializer, and accounts/views.py for the
// extra actions below).
import { http, fetchAllPages } from './client';
import { toMatrix, emptyMatrix } from './teams';
import { ALL_MODULES, PERM_ACTIONS } from '../lib/constants';

/**
 * The stored deltas, as a dense {module: {action: true|false|null}} map.
 *
 * null is the third state and it is the DEFAULT one: inherit whatever the team
 * says, now and after the team changes. Only a real true or false is an
 * exception. Collapsing null to false here would turn every untouched cell into
 * a revoke the moment the form saved.
 */
function overridesToMatrix(rows) {
  const m = {};
  ALL_MODULES.forEach((k) => {
    m[k] = {};
    PERM_ACTIONS.forEach((a) => { m[k][a] = null; });
  });
  (rows || []).forEach((row) => {
    if (!m[row.module]) return;
    PERM_ACTIONS.forEach((a) => {
      const v = row[`can_${a}`];
      m[row.module][a] = v === null || v === undefined ? null : !!v;
    });
  });
  return m;
}

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
    // Absent on any response predating the column: treat that as access granted,
    // which is the column default, so an old payload never reads as locked out.
    login_access: u.login_access !== false,
    team_id: u.team_id || null,
    is_lead: !!u.is_team_lead,
    // Who this person reports to. The backend has exposed these two since the
    // column was added; nothing in the UI read them, so the field was invisible
    // and unsettable and every row is still null. lib/reporting.js falls back to
    // the team's leads when it is unset.
    mapped_lead_id: u.mapped_lead_id || null,
    mapped_lead_name: u.mapped_lead_name || '',
    events_count: u.assigned_events_count || 0,
    assigned_events: u.assigned_events || [],
    last_login: u.last_login,
    has_all_access: !!u.has_all_access,
    // Three matrices, because the user form has to draw a cell that is on
    // BECAUSE OF THE TEAM differently from one somebody ticked for this person.
    // `effective` alone cannot tell them apart, and `overrides` alone cannot say
    // what a cleared cell would fall back to.
    team_permissions: toMatrix(u.team_permissions),
    effective_permissions: toMatrix(u.effective_permissions),
    permission_overrides: overridesToMatrix(u.permission_overrides),
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
  // `null` unassigns, so this is tested for `undefined` like team_id above it and
  // not for truthiness — clearing a reporting manager must reach the server.
  if (patch.mapped_lead_id !== undefined) body.mapped_lead_id = patch.mapped_lead_id;
  if (patch.username !== undefined) body.username = patch.username;
  if (patch.email !== undefined) body.email = patch.email;
  if (patch.first_name !== undefined) body.first_name = patch.first_name;
  if (patch.last_name !== undefined) body.last_name = patch.last_name;
  if (patch.role !== undefined) body.role = patch.role;
  if (patch.status !== undefined) body.status = patch.status;
  if (patch.login_access !== undefined) body.login_access = patch.login_access;
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

/**
 * Save one person's exceptions, given the grid the form is SHOWING.
 *
 * The form works in effective terms — a checkbox is either ticked or it is not —
 * and the delta is derived here by comparing against the team. That direction
 * matters:
 *
 *   * a cell matching the team is sent as null, so it goes back to inheriting.
 *     Tick something on, then off again, and the exception disappears rather
 *     than lingering as an explicit "no" that happens to agree today;
 *   * a cell differing from the team is sent as the value the user chose, which
 *     is a grant when the team says no and a revoke when the team says yes.
 *
 * The alternative — storing the resolved matrix — would freeze each person at
 * the moment they were last edited, and the next widening of their team would
 * pass them by.
 */
export function savePermissions(id, desired, teamMatrix) {
  const rows = ALL_MODULES.map((module) => {
    const want = desired[module] || {};
    const team = (teamMatrix || {})[module] || {};
    const row = { module };
    PERM_ACTIONS.forEach((a) => {
      row[`can_${a}`] = !!want[a] === !!team[a] ? null : !!want[a];
    });
    return row;
  });
  return http.put(`users/${id}/permissions/`, { permissions: rows }).then((r) => toFrontend(r.data));
}

export { emptyMatrix };

export function remove(id) {
  return http.delete(`users/${id}/`).then(() => true);
}
