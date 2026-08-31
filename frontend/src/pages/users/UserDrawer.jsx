import Drawer from '../../components/Drawer';
import { Icon } from '../../lib/icons';
import { Av, ReportsTo, RoleBadge, StatusPill } from '../../components/Badge';
import { reportingManagerOf } from '../../lib/reporting';
import { rel } from '../../lib/helpers';
import { ROLE_FULL } from '../../lib/constants';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';
import { useFetch } from '../../hooks/useFetch';
import PermissionGrid, { PermissionLegend } from '../../components/PermissionGrid';
import { apiErrorMessage } from '../../api/client';
import * as usersApi from '../../api/users';
import * as teamsApi from '../../api/teams';

/**
 * `onEdit` / `onResetPassword` hand the user back up to UsersPage rather than
 * opening a modal from in here. `.dr` slides in on a `transform`, which makes it
 * a containing block for `position: fixed` — a Modal rendered as its child would
 * be laid out inside the 520px drawer and clipped by `.dr-b`'s overflow, not
 * centred on the viewport.
 */
export default function UserDrawer({ user: u, users, onClose, onChanged, onEdit, onResetPassword }) {
  const { can } = useSession();
  const toast = useToast();
  const { data: teams } = useFetch(teamsApi.list, [], { initialData: [] });
  if (!u) return null;
  // Module access comes from the team, plus this person's own exceptions. The
  // serializer resolves both, so the drawer renders the answer rather than
  // recomputing it and risking a different one.
  const perms = u.effective_permissions;
  const team = (teams || []).find((t) => t.id === u.team_id) || null;
  const reportsTo = reportingManagerOf(u, teams, users);
  const teamGrid = team ? team.permissions : teamsApi.emptyMatrix();

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
        <div className="ro-c"><div className="ro-l">Team</div><div className="ro-v">{team ? (team.is_all_access ? team.name + ' · full access' : team.name) : <span className="dim">None — no module access</span>}</div></div>
        <div className="ro-c"><div className="ro-l">Team lead</div><div className="ro-v">{u.is_lead ? 'Yes' : 'No'}</div></div>
        {/* Separate row from Team lead on purpose: one is who they report to,
            the other is whose accounts they administer. */}
        <div className="ro-c"><div className="ro-l">Manages</div><div className="ro-v">{u.managed_team_name || <span className="dim">No team</span>}</div></div>
        <div className="ro-c f"><div className="ro-l">Reporting manager</div><div className="ro-v"><ReportsTo value={reportsTo} /></div></div>
        <div className="ro-c"><div className="ro-l">Status</div><div className="ro-v"><StatusPill value={u.status} /></div></div>
        <div className="ro-c f"><div className="ro-l">Email</div><div className="ro-v" style={{ fontWeight: 500 }}>{u.email}</div></div>
        <div className="ro-c f"><div className="ro-l">Last active</div><div className="ro-v">{rel(u.last_login)}</div></div>
      </div>
      <div className="sl">Module access</div>
      <div style={{ marginBottom: 18 }}>
        {/* Effective access, tinted so an exception is visible as one rather
            than blending into what the team already grants. */}
        <PermissionGrid value={perms} inherited={teamGrid} onToggle={() => {}} disabled />
        <PermissionLegend />
      </div>
      <div className="sl">Events assigned</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {(u.assigned_events || []).map((e) => <span className="tg bg-neutral mono" key={e.id}>{e.event_code}</span>)}
      </div>
    </Drawer>
  );
}
