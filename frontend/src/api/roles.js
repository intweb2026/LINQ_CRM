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

export async function save(role) {
  let saved;
  if (role.id) {
    saved = await http.patch(`roles/${role.id}/`, {
      display_label: role.display_label, color: role.color, description: role.description,
    }).then((r) => r.data);
  } else {
    const name = role.display_label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_');
    saved = await http.post('roles/', {
      name, display_label: role.display_label, color: role.color, description: role.description,
    }).then((r) => r.data);
  }
  if (role.permissions) {
    const items = ALL_MODULES.map((m) => ({ module: m, ...role.permissions[m] }));
    saved = await http.put(`roles/${saved.id}/permissions/`, { permissions: items }).then((r) => r.data);
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
