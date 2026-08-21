import { useCallback, useMemo, useState } from 'react';
import Popover from '../components/Popover';
import { Icon } from '../lib/icons';
import { Av } from '../components/Badge';
import { nf } from '../lib/helpers';
import { TEAM_ROLES, ROLE_LABEL, ROLE_FULL } from '../lib/constants';
import * as teamsApi from '../api/teams';
import * as usersApi from '../api/users';
import * as statsApi from '../api/stats';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import NoAccessPage from './NoAccessPage';
import AssignLeadModal from './teams/AssignLeadModal';
import TeamActivityDrawer from './teams/TeamActivityDrawer';
import TeamFormModal from './teams/TeamFormModal';
import { apiErrorMessage } from '../api/client';

function Card({ u, metric, onDragStart, onDragEnd }) {
  return (
    <div className="kk" draggable="true" onDragStart={() => onDragStart(u.name)} onDragEnd={onDragEnd}>
      <div className="kk-r"><Av name={u.name} size="sm" /><span className="kk-i"><span className="kk-n">{u.name}</span><span className="kk-r2">{ROLE_FULL[u.role]}</span></span></div>
      <div className="kk-f">
        <span><Icon name="calendar" size={10} />{u.events_count} events</span>
        <span><Icon name={metric.ic} size={10} />{nf(metric.v)}</span>
        {u.is_lead ? <span className="kk-ld"><Icon name="star" size={9} />Lead</span> : null}
      </div>
    </div>
  );
}

function Column({ id, name, color, members, isOver, match, secondMetric, onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop, onEdit, onAssignLead, onViewActivity, onArchive }) {
  const vis = members.filter(match);
  return (
    <div className={'kc' + (isOver ? ' ov' : '')} onDragOver={(e) => { e.preventDefault(); onDragOver(id); }} onDragLeave={() => onDragLeave(id)} onDrop={(e) => { e.preventDefault(); onDrop(id); }}>
      <div className="kc-h">
        <span className="kc-d" style={{ background: color }} /><span className="kc-n">{name}</span><span className="kc-c">{members.length}</span>
        {id !== 0 ? (
          <Popover align="right" trigger={({ toggle }) => <button className="btn btn-g btn-sm btn-ic" style={{ marginLeft: 2 }} onClick={toggle}><Icon name="cols" size={13} /></button>}>
            {({ close }) => (
              <>
                <div className="pop-t">{name}</div>
                <button className="pop-i" onClick={() => { close(); onEdit(id); }}><Icon name="edit" size={15} />Edit team</button>
                <button className="pop-i" onClick={() => { close(); onAssignLead(id); }}><Icon name="star" size={15} />Assign lead</button>
                <button className="pop-i" onClick={() => { close(); onViewActivity(id); }}><Icon name="chart" size={15} />View activity</button>
                <button className="pop-i del" onClick={() => { close(); onArchive(id, name, members.length); }}><Icon name="trash" size={15} />Archive team</button>
              </>
            )}
          </Popover>
        ) : null}
      </div>
      <div className="kc-b">
        {vis.length ? vis.map((u) => <Card key={u.id} u={u} metric={secondMetric(u)} onDragStart={onDragStart} onDragEnd={onDragEnd} />) : <div className="kc-e">{members.length ? 'No match in this column' : 'No members yet'}</div>}
      </div>
    </div>
  );
}

export default function TeamsManagementPage() {
  const { canView, can } = useSession();
  const toast = useToast();
  const confirm = useConfirm();
  const [roleFilter, setRoleFilter] = useState('all');
  const [q, setQ] = useState('');
  const { data: teams, refetchQuiet: reloadTeams } = useFetch(teamsApi.list, [], { initialData: [] });
  const { data: users, refetchQuiet: reloadUsers } = useFetch(usersApi.list, [], { initialData: [] });
  // One aggregate request instead of walking every delegate and every ticket.
  const { data: memberStats, refetchQuiet: reloadStats } = useFetch(statsApi.teamMemberStats, [], { initialData: {} });
  const TEAMS = teams || [];
  const USERS = users || [];
  /**
   * BOTH lists, always.
   *
   * The board's columns come from `teams` but every CARD in them comes from
   * `users` — `team_id` and `is_lead` live on the user, not the team. Refreshing
   * teams alone meant a drag between columns and an Assign lead both returned a
   * success toast while the card stayed exactly where it was; only a page reload
   * showed the move that had in fact already been saved.
   *
   * The per-member counters come with them, and the whole thing now runs on
   * useLiveData — so a member moved between teams from someone else's browser
   * lands on this board too, which matters on a page two people reorganising the
   * same teams will have open at once.
   */
  const { refreshNow: refresh } = useLiveData(
    useCallback(() => { reloadTeams(); reloadUsers(); reloadStats(); }, [reloadTeams, reloadUsers, reloadStats]),
    { resources: ['teams', 'users'] },
  );
  const [leadModalTeam, setLeadModalTeam] = useState(null);
  const [activityTeam, setActivityTeam] = useState(null);
  const [formTeam, setFormTeam] = useState(undefined); // undefined = closed, null = create new, object = edit
  const [dragName, setDragName] = useState(null);
  const [dragOverId, setDragOverId] = useState(null);

  // Both maps come from /api/stats/dashboard_aggregate/ per_user_by_name, keyed on
  // the same display name the board renders.
  const userBookings = useMemo(() => {
    const m = {};
    Object.entries(memberStats || {}).forEach(([name, v]) => { m[name] = v.bookings; });
    return m;
  }, [memberStats]);
  const userTickets = useMemo(() => {
    const m = {};
    Object.entries(memberStats || {}).forEach(([name, v]) => { m[name] = v.tickets; });
    return m;
  }, [memberStats]);

  if (!canView('teams')) return <NoAccessPage module="Teams" />;

  const board = {}; TEAMS.forEach((t) => { board[t.id] = []; });
  const un = [];
  USERS.forEach((u) => { if (board[u.team_id]) board[u.team_id].push(u); else un.push(u); });

  function match(u) { if (roleFilter !== 'all' && u.role !== roleFilter) return false; if (q && !u.name.toLowerCase().includes(q.toLowerCase())) return false; return true; }
  function secondMetric(u) { return u.role === 'market_research' || u.role === 'data_mining' ? { ic: 'ticket', v: userTickets[u.name] || 0 } : { ic: 'receipt', v: userBookings[u.name] || 0 }; }

  async function drop(destId) {
    setDragOverId(null);
    if (!dragName) return;
    const u = USERS.find((x) => x.name === dragName);
    setDragName(null);
    if (!u || u.team_id === destId) return;
    try {
      await teamsApi.reassign(u.id, destId || null);
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not move ' + u.name + '.'), 'er');
      return;
    }
    toast(u.name + ' moved to ' + (destId === 0 ? 'Unassigned' : TEAMS.find((t) => t.id === destId)?.name), 'ok');
    refresh();
  }

  async function archive(id, name, memberCount) {
    const ok = await confirm({ title: 'Archive ' + name + '?', danger: true, ok: 'Archive', sub: memberCount + ' member(s) will need reassignment.', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Archived teams are hidden from the board but not deleted.</p> });
    if (ok) {
      try {
        await teamsApi.archive(id);
      } catch (err) {
        toast(apiErrorMessage(err, 'Could not archive ' + name + '.'), 'er');
        return;
      }
      refresh();
      toast(name + ' archived', 'ok');
    }
  }

  const columnProps = {
    match, secondMetric,
    onDragStart: setDragName, onDragEnd: () => setDragName(null),
    onDragOver: setDragOverId, onDragLeave: (id) => setDragOverId((cur) => (cur === id ? null : cur)), onDrop: drop,
    onEdit: (id) => setFormTeam(TEAMS.find((t) => t.id === id)),
    onAssignLead: (id) => setLeadModalTeam(TEAMS.find((t) => t.id === id)),
    onViewActivity: (id) => setActivityTeam(TEAMS.find((t) => t.id === id)),
    onArchive: archive,
  };

  return (
    <>
      {/* No tab strip or table toolbar on this page (it's a hand-rolled board,
          not a DataTable), so the actions ride directly in this row instead
          of a PageHead row of their own — one fewer row of height, matching
          BookingsPage / TicketCentralPage. */}
      <div className="tb">
        <div className="tb-s"><input className="in in-s" placeholder="Find a person…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="chips">
          <span className={'chip' + (roleFilter === 'all' ? ' on' : '')} onClick={() => setRoleFilter('all')}>All roles</span>
          {TEAM_ROLES.map((r) => <span key={r} className={'chip' + (roleFilter === r ? ' on' : '')} onClick={() => setRoleFilter(r)}>{ROLE_LABEL[r]}</span>)}
        </div>
        <div className="tb-sp" /><span className="tb-m">{USERS.length} members · {TEAMS.length} teams</span>
        {can('create', 'teams') ? <>
          <button className="btn btn-s" onClick={() => toast('Roster exported', 'ok')}><Icon name="download" size={15} />Export roster</button>
          <button className="btn btn-p" onClick={() => setFormTeam(null)}><Icon name="plus" size={15} />Create team</button>
        </> : null}
      </div>
      <div className="kb">
        <Column id={0} name="Unassigned" color="var(--n-300)" members={un} isOver={dragOverId === 0} {...columnProps} />
        {TEAMS.map((t) => <Column key={t.id} id={t.id} name={t.name} color={t.color} members={board[t.id] || []} isOver={dragOverId === t.id} {...columnProps} />)}
      </div>
      {leadModalTeam ? <AssignLeadModal team={leadModalTeam} onClose={() => setLeadModalTeam(null)} onSaved={refresh} /> : null}
      {activityTeam ? <TeamActivityDrawer team={activityTeam} onClose={() => setActivityTeam(null)} /> : null}
      {formTeam !== undefined ? <TeamFormModal team={formTeam} onClose={() => setFormTeam(undefined)} onSaved={refresh} /> : null}
    </>
  );
}
