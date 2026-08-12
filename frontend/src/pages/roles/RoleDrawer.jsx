import Drawer from '../../components/Drawer';
import { Icon } from '../../lib/icons';
import { Av, StatusPill } from '../../components/Badge';
import { CRM_MODULES, PERM_ACTIONS } from '../../lib/constants';
import * as usersApi from '../../api/users';
import * as teamsApi from '../../api/teams';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';

export default function RoleDrawer({ role: r, perms, onClose, onEdit }) {
  const { can } = useSession();
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: teams } = useFetch(teamsApi.list, [], { initialData: [] });
  const teamName = (id) => ((teams || []).find((t) => t.id === id) || {}).name || 'Unassigned';
  if (!r) return null;
  const members = (users || []).filter((u) => u.role === r.name);

  return (
    <Drawer
      wide onClose={onClose}
      head={
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          <span className="kpi-i" style={{ width: 40, height: 40, background: r.color + '22', color: r.color }}><Icon name="shield" size={18} /></span>
          <div><h2>{r.display_label}</h2><p>{members.length} member{members.length === 1 ? '' : 's'} · {r.system ? 'system role' : 'custom role'}</p></div>
        </div>
      }
      foot={<>
        <button className="btn btn-s" onClick={onClose}>Close</button>
        {can('update', 'roles') ? <button className="btn btn-p" onClick={() => { onClose(); onEdit(r); }}><Icon name="edit" size={15} />Edit permissions</button> : null}
      </>}
    >
      <div className="sl">Description</div>
      <p style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.6, marginBottom: 18 }}>{r.description}</p>
      <div className="sl">Permission matrix</div>
      <table className="pm" style={{ marginBottom: 20 }}>
        <thead><tr><th>Module</th>{PERM_ACTIONS.map((a) => <th key={a}>{a}</th>)}</tr></thead>
        <tbody>
          {CRM_MODULES.map((mo) => (
            <tr key={mo.k}><td>{mo.l}</td>{PERM_ACTIONS.map((a) => <td key={a}>{(perms[mo.k] || {})[a] ? <span style={{ color: 'var(--green)' }}><Icon name="check" size={14} /></span> : <span className="dim">—</span>}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <div className="sl">Members</div>
      {members.length ? members.map((u) => (
        <div key={u.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderBottom: '1px solid var(--n-50)' }}>
          <Av name={u.name} size="sm" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 650, fontSize: 12.5, color: 'var(--text)' }}>{u.name}</div>
            <div style={{ fontSize: 10.5, color: 'var(--text-4)' }}>{teamName(u.team_id)}</div>
          </div>
          {u.is_lead ? <span className="bg bg-amber"><i />Lead</span> : null}
          <StatusPill value={u.status} />
        </div>
      )) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>No one currently holds this role.</p>}
    </Drawer>
  );
}
