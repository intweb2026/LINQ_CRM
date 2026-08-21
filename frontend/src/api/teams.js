// Real backend: /api/teams/ (see backend/teams/serializers.py + views.py).
//
// THE TEAM IS THE ROLE. A team carries the permission grid its members inherit,
// so this module also owns the grid endpoint. There is no api/roles.js any more.
import { http, fetchAllPages } from './client';
import { ALL_MODULES, PERM_ACTIONS } from '../lib/constants';

/**
 * A dense {module: {view, create, update, delete}} matrix.
 *
 * The server already sends every module, but this fills in anything missing so
 * a caller can index it without guarding. A missing module means DENIED, and
 * the difference between "denied" and "undefined" is a checkbox that renders
 * unchecked either way and then saves the wrong thing.
 */
export function emptyMatrix(value = false) {
  const m = {};
  ALL_MODULES.forEach((k) => {
    m[k] = {};
    PERM_ACTIONS.forEach((a) => { m[k][a] = value; });
  });
  return m;
}

export function toMatrix(raw) {
  const m = emptyMatrix();
  Object.entries(raw || {}).forEach(([module, cells]) => {
    if (!m[module]) return;
    PERM_ACTIONS.forEach((a) => { m[module][a] = !!(cells || {})[a]; });
  });
  return m;
}

/** The UI grid, in the shape the backend reads. See toPermissionRows' history. */
export function toPermissionRows(matrix) {
  return ALL_MODULES.map((module) => {
    const p = matrix[module] || {};
    return {
      module,
      can_view: !!p.view,
      can_create: !!p.create,
      can_update: !!p.update,
      can_delete: !!p.delete,
    };
  });
}

function toFrontend(t) {
  return {
    id: t.id,
    name: t.name,
    color: t.color || '#6b7280',
    description: t.description || '',
    slug: t.slug || '',
    team_lead_id: t.team_lead_id,
    team_lead_name: t.team_lead_name,
    // EVERY lead, not just the team_lead FK. A team may have any number; Sales
    // Team has two, and the FK names only one of them. The serializer has always
    // sent this list — dropping it here is what limited callers to one lead.
    team_leads: (t.team_leads || []).map((l) => ({ id: l.id, name: l.name })),
    member_count: t.member_count || 0,
    is_archived: t.is_archived,
    is_all_access: !!t.is_all_access,
    permissions: toMatrix(t.permissions),
  };
}

export const list = () => fetchAllPages('teams/').then((rows) => rows.filter((t) => !t.is_archived).map(toFrontend));

function toBackend(patch) {
  const body = {};
  if (patch.name !== undefined) body.name = patch.name;
  if (patch.color !== undefined) body.color = patch.color;
  if (patch.description !== undefined) body.description = patch.description;
  return body;
}

export function create(payload) {
  return http.post('teams/', toBackend(payload)).then((r) => toFrontend(r.data));
}
export function update(id, payload) {
  return http.patch(`teams/${id}/`, toBackend(payload)).then((r) => toFrontend(r.data));
}
// 409 with a `detail` naming the member count when the team is not empty —
// TeamViewSet.destroy refuses rather than orphaning people.
export function remove(id) {
  return http.delete(`teams/${id}/`).then(() => true);
}

/**
 * Replace a team's grid, and with it what every member of that team may do.
 *
 * The whole grid goes every time, never a patch of the changed rows: the server
 * deletes and rewrites, so a module left out of the payload is one that has been
 * turned off. Sending a subset would read as "leave the rest alone" and silently
 * revoke it.
 */
export function savePermissions(id, matrix, { isAllAccess } = {}) {
  const body = { permissions: toPermissionRows(matrix) };
  if (isAllAccess !== undefined) body.is_all_access = !!isAllAccess;
  return http.put(`teams/${id}/permissions/`, body).then((r) => r.data);
}

export function reassign(userId, teamId) {
  return http.post('teams/move-member/', { user_id: userId, destination_team_id: teamId || null }).then((r) => r.data);
}
/**
 * Replace a team's leads. `leadIds` is the WHOLE list, in order, and the first is
 * the primary — the one that lands on Team.team_lead.
 *
 * `user_ids`, never the older `user_id`. The endpoint has accepted a list from
 * the start, but this function only ever sent the singular key, so the UI could
 * appoint exactly one lead per team however many the team really had. Worse, the
 * endpoint clears is_team_lead across the team before applying the payload, so
 * sending one id DEMOTED every other lead — silently, on a screen that said
 * nothing about them.
 *
 * An empty list is meaningful and is passed through: it removes every lead.
 */
export function assignLead(teamId, leadIds) {
  const user_ids = (Array.isArray(leadIds) ? leadIds : [leadIds])
    .filter((id) => id != null && id !== '')
    .map(Number);
  return http.post(`teams/${teamId}/assign-lead/`, { user_ids }).then((r) => r.data);
}
export function archive(teamId) {
  return http.post(`teams/${teamId}/archive/`, {}).then((r) => r.data);
}
export function activity(teamId) {
  return http.get(`teams/${teamId}/activity/`).then((r) => r.data);
}
