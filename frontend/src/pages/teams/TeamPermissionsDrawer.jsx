import Drawer from '../../components/Drawer';
import PermissionGrid from '../../components/PermissionGrid';
import { Icon } from '../../lib/icons';
import { Av, StatusPill } from '../../components/Badge';
import { CRM_MODULES, PERM_ACTIONS } from '../../lib/constants';

/** Does this person differ from their team anywhere? */
function exceptionCount(u) {
  let n = 0;
  CRM_MODULES.forEach((mo) => {
    PERM_ACTIONS.forEach((a) => {
      const v = (u.permission_overrides[mo.k] || {})[a];
      if (v !== null && v !== undefined) n += 1;
    });
  });
  return n;
}

export default function TeamPermissionsDrawer({ team: t, members, canEdit, onClose, onEdit }) {
  if (!t) return null;

  return (
    <Drawer
      wide
      onClose={onClose}
      head={
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          <span className="kpi-i" style={{ width: 40, height: 40, background: t.color + '22', color: t.color }}>
            <Icon name="team" size={18} />
          </span>
          <div>
            <h2>{t.name}</h2>
            <p>{members.length} member{members.length === 1 ? '' : 's'} · {t.is_all_access ? 'full access' : 'permissions below'}</p>
          </div>
        </div>
      }
      foot={<>
        <button className="btn btn-s" onClick={onClose}>Close</button>
        {canEdit ? (
          <button className="btn btn-p" onClick={() => { onClose(); onEdit(t); }}>
            <Icon name="edit" size={15} />Edit permissions
          </button>
        ) : null}
      </>}
    >
      <div className="sl">Description</div>
      <p style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.6, marginBottom: 18 }}>
        {t.description || <span className="dim">No description.</span>}
      </p>

      <div className="sl">What this team opens</div>
      {t.is_all_access ? (
        <p style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 20 }}>
          Full access to every module, including any added later.
        </p>
      ) : (
        <div style={{ marginBottom: 20 }}>
          <PermissionGrid value={t.permissions} onToggle={() => {}} disabled />
        </div>
      )}

      <div className="sl">Members</div>
      {members.length ? members.map((u) => {
        const n = exceptionCount(u);
        return (
          <div key={u.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderBottom: '1px solid var(--n-50)' }}>
            <Av name={u.name} size="sm" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 650, fontSize: 12.5, color: 'var(--text)' }}>{u.name}</div>
              <div style={{ fontSize: 10.5, color: 'var(--text-4)' }}>
                {n ? `${n} permission${n === 1 ? '' : 's'} set individually` : 'Inherits the team'}
              </div>
            </div>
            {u.is_lead ? <span className="bg bg-amber"><i />Lead</span> : null}
            <StatusPill value={u.status} />
          </div>
        );
      }) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>Nobody is in this team yet.</p>}
    </Drawer>
  );
}
