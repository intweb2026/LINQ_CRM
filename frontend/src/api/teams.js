// Real backend: /api/teams/ (see backend/teams/serializers.py + views.py).
import { http, fetchAllPages } from './client';

function toFrontend(t) {
  return {
    id: t.id,
    name: t.name,
    color: t.color || '#6b7280',
    description: t.description || '',
    team_lead_id: t.team_lead_id,
    team_lead_name: t.team_lead_name,
    member_count: t.member_count || 0,
    is_archived: t.is_archived,
  };
}

export const list = () => fetchAllPages('teams/').then((rows) => rows.filter((t) => !t.is_archived).map(toFrontend));

export function reassign(userId, teamId) {
  return http.post('teams/move-member/', { user_id: userId, destination_team_id: teamId || null }).then((r) => r.data);
}
export function assignLead(teamId, userId) {
  return http.post(`teams/${teamId}/assign-lead/`, { user_id: userId }).then((r) => r.data);
}
export function archive(teamId) {
  return http.post(`teams/${teamId}/archive/`, {}).then((r) => r.data);
}
export function activity(teamId) {
  return http.get(`teams/${teamId}/activity/`).then((r) => r.data);
}
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
