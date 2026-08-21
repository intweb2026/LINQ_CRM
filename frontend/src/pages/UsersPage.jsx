import { useCallback, useState } from 'react';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import { Icon } from '../lib/icons';
import { Who, RoleBadge, ReportsTo, StatusPill } from '../components/Badge';
import { reportingManagerOf } from '../lib/reporting';
import { avc, ini, rel } from '../lib/helpers';
import { TEAM_ROLES, ROLE_FULL } from '../lib/constants';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import NoAccessPage from './NoAccessPage';
import UserDrawer from './users/UserDrawer';
import UserFormModal from './users/UserFormModal';
import ResetPasswordModal from './users/ResetPasswordModal';
import * as usersApi from '../api/users';
import * as teamsApi from '../api/teams';

export default function UsersPage() {
  const { canView, can } = useSession();
  const toast = useToast();
  const { data: users, refetchQuiet: reloadUsers } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: teams, refetchQuiet: reloadTeams } = useFetch(teamsApi.list, [], { initialData: [] });
  const USERS = users || [];
  const TEAMS = teams || [];
  const teamName = (id) => (TEAMS.find((t) => t.id === id) || {}).name || 'Unassigned';
  /**
   * BOTH lists, and not only after a save on this page.
   *
   * The table is in client mode, so nothing under it re-fetches on its own: this
   * is the single path by which anything here changes. Routing it through
   * useLiveData means it also fires when a user is created in another tab, when a
   * team is renamed on the Teams board, or when a role's permissions change — all
   * of which this page renders, and none of which it used to hear about.
   */
  const { refreshNow: refresh } = useLiveData(
    useCallback(() => { reloadUsers(); reloadTeams(); }, [reloadUsers, reloadTeams]),
    { resources: ['users', 'teams', 'roles'] },
  );
  const [drawerUser, setDrawerUser] = useState(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [formUser, setFormUser] = useState(undefined); // undefined = closed, null = create new, object = edit
  const [pwUser, setPwUser] = useState(null);

  if (!canView('users')) return <NoAccessPage module="Users" />;

  async function invite(e) {
    e.preventDefault();
    const ok = await usersApi.inviteByEmail(e.target.elements.emails.value, e.target.elements.role.value, e.target.elements.team.value);
    setInviteOpen(false);
    toast(ok ? 'Invitations sent' : 'Email invites are not available yet — create accounts directly for now', ok ? 'ok' : 'nf');
  }

  return (
    <>
      <DataTable
        rows={USERS} noun="users" pageSize={50} defaultSort={{ key: 'name', dir: 'asc' }} searchPlaceholder="Search name or username…"
        // No tabs or date-range row on this page to fold these into (see
        // BookingsPage / TicketCentralPage, PaperReviewPage / ProposalSubmissionPage),
        // so they ride on the table's own toolbar row instead of a PageHead row
        // of their own — one fewer row of height above the table.
        extraToolbar={can('create', 'users') ? <>
          <button className="btn btn-s" onClick={() => setInviteOpen(true)}><Icon name="mail" size={15} />Invite</button>
          <button className="btn btn-p" onClick={() => setFormUser(null)}><Icon name="plus" size={15} />Add user</button>
        </> : null}
        cols={[
          { key: 'name', label: 'User', cls: 'st usr-name', cell: (v, r) => <Who name={v} sub={r.username} mono avatar={false} /> },
          { key: 'role', label: 'Role', cell: (v) => <RoleBadge value={v} />, opts: () => TEAM_ROLES },
          { key: 'team_id', label: 'Team', cell: (v) => teamName(v), opts: () => TEAMS.map((t) => t.name) },
          { key: 'is_lead', label: 'Lead', cell: (v) => (v ? <span className="bg bg-amber"><i />Lead</span> : <span className="dim">—</span>) },
          // Sorted and filtered on the raw mapped_lead_name, which is '' for
          // everyone until somebody records one; the cell falls back to the
          // team's leads so the column is readable in the meantime.
          { key: 'mapped_lead_name', label: 'Reporting Manager', cell: (v, r) => <ReportsTo value={reportingManagerOf(r, TEAMS, USERS)} avatar={false} /> },
          { key: 'events_count', label: 'Events', num: true },
          { key: 'last_login', label: 'Last active', cell: (v) => rel(v) },
          { key: 'status', label: 'Status', cell: (v) => <StatusPill value={v} />, opts: () => ['active', 'inactive'] },
        ]}
        card={(r) => (
          <div className="rc">
            <div className="rc-t"><span className="av av-lg" style={{ background: avc(r.name) }}>{ini(r.name)}</span>
              <span className="who-t" style={{ flex: 1 }}><span className="who-n">{r.name}</span><span className="who-s mono">@{r.username}</span></span>
              <StatusPill value={r.status} />
            </div>
            <div className="rc-m">
              <div><div className="l">Role</div><div className="v">{ROLE_FULL[r.role]}</div></div>
              <div><div className="l">Team</div><div className="v">{teamName(r.team_id)}</div></div>
              <div><div className="l">Events</div><div className="v">{r.events_count}</div></div>
              <div><div className="l">Lead</div><div className="v">{r.is_lead ? 'Yes' : 'No'}</div></div>
              <div><div className="l">Reports to</div><div className="v"><ReportsTo value={reportingManagerOf(r, TEAMS, USERS)} avatar={false} /></div></div>
            </div>
          </div>
        )}
        onRow={(r) => setDrawerUser(r)}
      />
      {/* `users` is passed down, not re-fetched in each child. Both need the list
          only to work out who could be a reporting manager, and api/client.js
          warns about exactly the duplicate fetchAllPages walk that would be. */}
      {drawerUser ? <UserDrawer user={drawerUser} users={USERS} onClose={() => setDrawerUser(null)} onChanged={refresh} onEdit={setFormUser} onResetPassword={setPwUser} /> : null}
      {formUser !== undefined ? <UserFormModal user={formUser} users={USERS} onClose={() => setFormUser(undefined)} onSaved={refresh} /> : null}
      {pwUser ? <ResetPasswordModal user={pwUser} onClose={() => setPwUser(null)} /> : null}
      {inviteOpen ? (
        <Modal size="sm" title="Invite by email" sub="They receive a link to set their own password." onClose={() => setInviteOpen(false)}
          footer={<><button className="btn btn-s" onClick={() => setInviteOpen(false)}>Cancel</button><button className="btn btn-p" type="submit" form="inviteForm"><Icon name="mail" size={15} />Send invites</button></>}>
          <form id="inviteForm" onSubmit={invite}>
            <div className="fd" style={{ marginBottom: 12 }}><label className="fd-l">Email addresses</label><textarea className="in" name="emails" placeholder="one@iq-hub.com, two@iq-hub.com" /></div>
            <div className="fg">
              <div className="fd"><label className="fd-l">Role</label><select className="in" name="role">{TEAM_ROLES.map((r) => <option key={r} value={r}>{ROLE_FULL[r]}</option>)}</select></div>
              <div className="fd"><label className="fd-l">Team</label><select className="in" name="team">{TEAMS.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></div>
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  );
}
