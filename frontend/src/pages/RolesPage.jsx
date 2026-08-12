import { useState } from 'react';
import { PageHead } from '../components/UI';
import { Icon } from '../lib/icons';
import { Av } from '../components/Badge';
import { CRM_MODULES } from '../lib/constants';
import * as rolesApi from '../api/roles';
import * as usersApi from '../api/users';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import RoleDrawer from './roles/RoleDrawer';
import RoleEditModal from './roles/RoleEditModal';

export default function RolesPage() {
  const { canView, can } = useSession();
  const { data: roles, refetch } = useFetch(rolesApi.list, [], { initialData: [] });
  const { data: rolePerms } = useFetch(rolesApi.permissions, [], { initialData: {} });
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const CUSTOM_ROLES = roles || [];
  const ROLE_PERMS = rolePerms || {};
  const USERS = users || [];
  const refresh = () => refetch();
  const [drawerRole, setDrawerRole] = useState(null);
  const [editRole, setEditRole] = useState(undefined); // undefined = closed, null = create new, object = edit

  if (!canView('roles')) return <NoAccessPage module="Roles" />;

  return (
    <>
      <PageHead title="Roles" sub="Permission sets by module. Open a role to see who holds it and adjust view/create/update/delete."
        actions={can('create', 'roles') ? <button className="btn btn-p" onClick={() => setEditRole(null)}><Icon name="plus" size={15} />Create role</button> : null} />
      <div className="cg" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(270px,1fr))' }}>
        {CUSTOM_ROLES.map((r) => {
          const p = ROLE_PERMS[r.name] || {};
          const modsOn = CRM_MODULES.filter((mo) => p[mo.k] && p[mo.k].view);
          const members = USERS.filter((u) => u.role === r.name);
          return (
            <div className="rcd" key={r.id} onClick={() => setDrawerRole(r)}>
              <div className="rcd-b" style={{ background: r.color }} />
              <div className="rcd-h"><span className="rcd-n">{r.display_label}</span>{r.system ? <span className="tg bg-neutral">System</span> : <span className="tg bg-teal">Custom</span>}</div>
              <p style={{ fontSize: 11.5, color: 'var(--text-3)', lineHeight: 1.5, marginBottom: 11, minHeight: 34 }}>{r.description}</p>
              <div className="prm">{modsOn.map((mo) => <span key={mo.k}>{mo.l}</span>)}</div>
              <div className="rcd-f">
                <span className="c"><b>{r.user_count}</b> {r.user_count === 1 ? 'member' : 'members'}</span>
                <span className="av-stk">
                  {members.slice(0, 4).map((u) => <Av key={u.id} name={u.name} size="xs" />)}
                  {r.user_count > 4 ? <span className="av av-xs" style={{ background: 'var(--n-200)', color: 'var(--text-2)' }}>+{r.user_count - 4}</span> : null}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {drawerRole ? <RoleDrawer role={drawerRole} perms={ROLE_PERMS[drawerRole.name] || {}} onClose={() => setDrawerRole(null)} onEdit={setEditRole} /> : null}
      {editRole !== undefined ? <RoleEditModal role={editRole} perms={editRole ? ROLE_PERMS[editRole.name] || {} : {}} onClose={() => setEditRole(undefined)} onSaved={refresh} /> : null}
    </>
  );
}
