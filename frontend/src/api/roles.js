// Real backend: /api/roles/ (see backend/accounts/serializers.py —
// CustomRoleSerializer/RolePermissionSerializer — and CustomRoleViewSet.set_permissions).
import { http, fetchAllPages } from './client';
import { ALL_MODULES } from '../lib/constants';

function toFrontendRole(r) {
  return {
    id: r.id,
    name: r.name,
    display_label: r.display_label,
    color: r.color,
    description: r.description,
    system: r.is_system_role,
    is_all_access: r.is_all_access,
    user_count: r.user_count || 0,
  };
}

function permsMatrix(role) {
  const m = {};
  ALL_MODULES.forEach((k) => { m[k] = { view: false, create: false, update: false, delete: false }; });
  if (role.is_all_access) {
    ALL_MODULES.forEach((k) => { m[k] = { view: true, create: true, update: true, delete: true }; });
    return m;
  }
  (role.permissions || []).forEach((p) => {
    m[p.module] = { view: p.can_view, create: p.can_create, update: p.can_update, delete: p.can_delete };
  });
  return m;
}

let _cache = null;
async function fetchAll() {
  _cache = await fetchAllPages('roles/');
  return _cache;
}

export async function list() {
  const roles = await fetchAll();
  return roles.map(toFrontendRole);
}

// Returns { [role.name]: { module: {view,create,update,delete} } } — same
// shape the UI previously read off the hardcoded seed.ROLE_PERMS table.
export async function permissions() {
  const roles = _cache || (await fetchAll());
  const out = {};
  roles.forEach((r) => { out[r.name] = permsMatrix(r); });
  return out;
}

/**
 * The permission grid, in the shape PUT /api/roles/{id}/permissions/ reads.
 *
 * THE BUG THIS FIXES
 * The grid is held in the UI as {view, create, update, delete} and used to be
 * spread into the request as-is:
 *
 *     ALL_MODULES.map((m) => ({ module: m, ...role.permissions[m] }))
 *
 * CustomRoleViewSet.set_permissions reads `can_view` / `can_create` /
 * `can_update` / `can_delete` and defaults each MISSING key to False. So every
 * save — creating a role or editing one — wrote a fully denied permission set,
 * silently: the request returned 200, the toast said "Role created", and the
 * role came back with nothing ticked. Anyone holding it saw No Access on every
 * module. The names have to be translated, not spread.
 */
function toPermissionRows(grid) {
  return ALL_MODULES.map((m) => {
    const p = grid[m] || {};
    return {
      module: m,
      can_view: !!p.view,
      can_create: !!p.create,
      can_update: !!p.update,
      can_delete: !!p.delete,
    };
  });
}

export async function save(role) {
  let saved;
  if (role.id) {
    saved = await http.patch(`roles/${role.id}/`, {
      display_label: role.display_label, color: role.color, description: role.description,
    }).then((r) => r.data);
  } else {
    // `name` is the stable key permissions and users are matched on, and it is
    // UNIQUE. Trailing underscores from a label like "Ops (EU)" would otherwise
    // survive into it, and a label with nothing alphanumeric in it would derive
    // an empty name the backend rejects with an unreadable error.
    const name = role.display_label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'role';
    saved = await http.post('roles/', {
      name, display_label: role.display_label, color: role.color, description: role.description,
    }).then((r) => r.data);
  }
  if (role.permissions) {
    // The id we already hold wins over the one echoed back. Reading it only off
    // the response meant a body that did not carry `id` addressed the second
    // request to "roles/undefined/permissions/" — a 404 whose only symptom is
    // that the identity saved and the permissions did not.
    const roleId = role.id || saved.id;
    if (!roleId) throw new Error('Role was saved but its id was not returned, so permissions could not be written.');
    saved = await http.put(`roles/${roleId}/permissions/`, {
      permissions: toPermissionRows(role.permissions),
    }).then((r) => r.data);
  }
  _cache = null;
  return toFrontendRole(saved);
}

export async function remove(name) {
  const roles = _cache || (await fetchAll());
  const role = roles.find((r) => r.name === name);
  if (!role) return true;
  await http.delete(`roles/${role.id}/`);
  _cache = null;
  return true;
}
