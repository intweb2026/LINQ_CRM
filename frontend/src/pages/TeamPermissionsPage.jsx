import { useCallback, useState } from 'react';
import { PageHead } from '../components/UI';
import { Av } from '../components/Badge';
import { CRM_MODULES } from '../lib/constants';
import * as teamsApi from '../api/teams';
import * as usersApi from '../api/users';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';
import TeamPermissionsDrawer from './teams/TeamPermissionsDrawer';
import TeamPermissionsModal from './teams/TeamPermissionsModal';

/**
 * Teams & permissions — what was the Roles page.
 *
 * There is no separate role any more: a team carries the grid and its members
 * inherit it. So the card grid that used to show one card per role now shows one
 * per team, and the members on each card are the people that grid actually
 * governs rather than everyone who happened to share a name with it.
 */
export default function TeamPermissionsPage() {
  const { canView, can } = useSession();
  const { data: teams, refetchQuiet: reloadTeams } = useFetch(teamsApi.list, [], { initialData: [] });
  const { data: users, refetchQuiet: reloadUsers } = useFetch(usersApi.list, [], { initialData: [] });
  const TEAMS = teams || [];
  const USERS = users || [];
  const { refreshNow: refresh } = useLiveData(
    useCallback(() => { reloadTeams(); reloadUsers(); }, [reloadTeams, reloadUsers]),
    { resources: ['teams', 'users', 'roles'] },
  );
  const [drawerTeam, setDrawerTeam] = useState(null);
  const [editTeam, setEditTeam] = useState(null);

  if (!canView('roles')) return <NoAccessPage module="Permissions" />;

  const membersOf = (t) => USERS.filter((u) => u.team_id === t.id);
  // Someone whose own grid differs from their team's. Surfaced on the card
  // because an exception nobody can see is an exception nobody reviews.
  const exceptionsIn = (t) => membersOf(t).filter(
    (u) => CRM_MODULES.some((mo) => ['view', 'create', 'update', 'delete'].some(
      (a) => (u.permission_overrides[mo.k] || {})[a] !== null
        && (u.permission_overrides[mo.k] || {})[a] !== undefined,
    )),
  );

  return (
    <>
      <PageHead
        title="Teams & permissions"
        sub="A team is a role. Everyone in it inherits its grid; open a team to change what it opens, or a person to give them an exception."
      />
      <div className="cg" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(270px,1fr))' }}>
        {TEAMS.map((t) => {
          const modsOn = CRM_MODULES.filter((mo) => (t.permissions[mo.k] || {}).view);
          const members = membersOf(t);
          const exceptions = exceptionsIn(t);
          return (
            <div className="rcd" key={t.id} onClick={() => setDrawerTeam(t)}>
              <div className="rcd-b" style={{ background: t.color }} />
              <div className="rcd-h">
                <span className="rcd-n">{t.name}</span>
                {t.is_all_access
                  ? <span className="tg bg-amber">All access</span>
                  : <span className="tg bg-teal">{modsOn.length} module{modsOn.length === 1 ? '' : 's'}</span>}
              </div>
              <p style={{ fontSize: 11.5, color: 'var(--text-3)', lineHeight: 1.5, marginBottom: 11, minHeight: 34 }}>
                {t.description || <span className="dim">No description.</span>}
              </p>
              <div className="prm">
                {t.is_all_access
                  ? <span>Everything</span>
                  : modsOn.length
                    ? modsOn.map((mo) => <span key={mo.k}>{mo.l}</span>)
                    : <span className="dim">Nothing yet</span>}
              </div>
              <div className="rcd-f">
                <span className="c">
                  <b>{members.length}</b> {members.length === 1 ? 'member' : 'members'}
                  {exceptions.length ? <span className="dim"> · {exceptions.length} with exceptions</span> : null}
                </span>
                <span className="av-stk">
                  {members.slice(0, 4).map((u) => <Av key={u.id} name={u.name} size="xs" />)}
                  {members.length > 4 ? <span className="av av-xs" style={{ background: 'var(--n-200)', color: 'var(--text-2)' }}>+{members.length - 4}</span> : null}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {!TEAMS.length ? (
        <p style={{ fontSize: 12.5, color: 'var(--text-4)', marginTop: 16 }}>
          No teams yet. Create one on the Teams board, then set what it opens here.
        </p>
      ) : null}
      {drawerTeam ? (
        <TeamPermissionsDrawer
          team={drawerTeam}
          members={membersOf(drawerTeam)}
          canEdit={can('update', 'roles')}
          onClose={() => setDrawerTeam(null)}
          onEdit={setEditTeam}
        />
      ) : null}
      {editTeam ? (
        <TeamPermissionsModal team={editTeam} onClose={() => setEditTeam(null)} onSaved={refresh} />
      ) : null}
    </>
  );
}
