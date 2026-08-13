import Drawer from '../../components/Drawer';
import { Icon } from '../../lib/icons';
import { Av, RoleBadge, StatusPill } from '../../components/Badge';
import { rel } from '../../lib/helpers';
import { CRM_MODULES, PERM_ACTIONS, ROLE_FULL } from '../../lib/constants';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';
import { useFetch } from '../../hooks/useFetch';
import { apiErrorMessage } from '../../api/client';
import * as usersApi from '../../api/users';
import * as rolesApi from '../../api/roles';
import * as teamsApi from '../../api/teams';

/**
 * `onEdit` / `onResetPassword` hand the user back up to UsersPage rather than
 * opening a modal from in here. `.dr` slides in on a `transform`, which makes it
 * a containing block for `position: fixed` — a Modal rendered as its child would
 * be laid out inside the 520px drawer and clipped by `.dr-b`'s overflow, not
 * centred on the viewport.
 */
export default function UserDrawer({ user: u, onClose, onChanged, onEdit, onResetPassword }) {
  const { can } = useSession();
  const toast = useToast();
  const { data: rolePerms } = useFetch(rolesApi.permissions, [], { initialData: {} });
  const { data: roles } = useFetch(rolesApi.list, [], { initialData: [] });
  const { data: teams } = useFetch(teamsApi.list, [], { initialData: [] });
  if (!u) return null;
  // Module access comes from the CUSTOM ROLE, which is the only thing
  // crm_permission() consults. Indexing the matrix by `u.role` — the legacy enum
  // — showed the right answer only where a seeded CustomRole happened to share
  // its name, and an empty grid for everyone else regardless of what they hold.
  const assignedRole = (roles || []).find((r) => r.id === u.custom_role_id);
  const perms = (rolePerms || {})[assignedRole?.name] || {};
  const teamName = (id) => ((teams || []).find((t) => t.id === id) || {}).name || 'Unassigned';

  async function toggleStatus() {
    const next = u.status === 'active' ? 'inactive' : 'active';
    try {
      await usersApi.toggleStatus(u.id);
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not change the status.'), 'er');
      return;
    }
    onClose();
    toast(u.name + ' is now ' + next, 'ok');
    onChanged();
  }

  return (
    <Drawer
      onClose={onClose}
      head={<div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><Av name={u.name} size="xl" /><div style={{ minWidth: 0 }}><h2>{u.name}</h2><p>@{u.username} · {ROLE_FULL[u.role]}</p></div></div>}
      foot={<>
        <button className="btn btn-s" onClick={onClose}>Close</button>
        {can('update', 'users') ? <>
          <button className="btn btn-s" onClick={() => { onClose(); onResetPassword?.(u); }}><Icon name="key" size={15} />Reset password</button>
          <button className="btn btn-s" onClick={toggleStatus}><Icon name={u.status === 'active' ? 'lock' : 'check'} size={15} />{u.status === 'active' ? 'Deactivate' : 'Activate'}</button>
          <button className="btn btn-p" onClick={() => { onClose(); onEdit?.(u); }}><Icon name="edit" size={15} />Edit user</button>
        </> : null}
      </>}
    >
      <div className="sl">Access</div>
      <div className="ro">
        <div className="ro-c"><div className="ro-l">Role</div><div className="ro-v"><RoleBadge value={u.role} /></div></div>
        <div className="ro-c"><div className="ro-l">Permission set</div><div className="ro-v">{assignedRole ? assignedRole.display_label : <span className="dim">None — no module access</span>}</div></div>
        <div className="ro-c"><div className="ro-l">Team</div><div className="ro-v">{teamName(u.team_id)}</div></div>
        <div className="ro-c"><div className="ro-l">Team lead</div><div className="ro-v">{u.is_lead ? 'Yes' : 'No'}</div></div>
        <div className="ro-c"><div className="ro-l">Status</div><div className="ro-v"><StatusPill value={u.status} /></div></div>
        <div className="ro-c f"><div className="ro-l">Email</div><div className="ro-v" style={{ fontWeight: 500 }}>{u.email}</div></div>
        <div className="ro-c f"><div className="ro-l">Last active</div><div className="ro-v">{rel(u.last_login)}</div></div>
      </div>
      <div className="sl">Module access</div>
      <table className="pm" style={{ marginBottom: 18 }}>
        <thead><tr><th>Module</th>{PERM_ACTIONS.map((a) => <th key={a}>{a}</th>)}</tr></thead>
        <tbody>
          {CRM_MODULES.map((mo) => (
            <tr key={mo.k}><td>{mo.l}</td>{PERM_ACTIONS.map((a) => <td key={a}>{(perms[mo.k] || {})[a] ? <Icon name="check" size={14} /> : <span className="dim">—</span>}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <div className="sl">Events assigned</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {(u.assigned_events || []).map((e) => <span className="tg bg-neutral mono" key={e.id}>{e.event_code}</span>)}
      </div>
    </Drawer>
  );
}
