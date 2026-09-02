// Who a person reports to.
//
// The column exists on the model as `mapped_lead` ("the specific team lead this
// user/member is mapped under") and the API has always exposed it, but no screen
// read it, so it was invisible, unsettable, and null on every one of the 46 live
// users. Showing it raw would therefore have shown nothing at all — so an unset
// value falls back through the chain below, marked as inherited rather than
// passed off as something somebody recorded.
//
// The chain is: the person's own team leads, then the administrators. That is the
// whole hierarchy — a member answers to their team's leads, and a team lead
// answers to an administrator. Without the second step a team lead was a dead end
// showing no manager at all.

/** Active administrators. Role, or the all-access team, either one. */
export function administratorsOf(users) {
  return (users || [])
    .filter((u) => (u.role === 'admin' || u.has_all_access) && u.status === 'active')
    .map((u) => ({ id: u.id, name: u.name }));
}

/**
 * The people who MANAGE this team, which is `managed_team` on the user.
 *
 * NOT `is_team_lead` and not `Team.team_lead`; backend accounts/models.py
 * User.is_team_manager says why the two are separate columns. A manager need not
 * be IN the team they run, so this is looked up over the whole user directory
 * rather than read off the team; /api/teams/ carries a team's leads and says
 * nothing at all about its managers.
 */
export function managersOf(team, users) {
  if (!team) return [];
  return (users || [])
    .filter((u) => u.managed_team_id === team.id && u.status === 'active')
    .map((u) => ({ id: u.id, name: u.name }));
}

function isAdministrator(user) {
  return !!user && (user.role === 'admin' || user.has_all_access);
}

/**
 * Returns `{ names, source, team }`.
 *
 * `source` is one of:
 *   'explicit' — recorded against this person; the only one that is not a guess
 *   'team'     — the leads of their team
 *   'manager'  — the manager of their team, when the team records no lead above
 *                them
 *   'admin'    — the administrators, for someone who leads their own team
 *   'top'      — this person is an administrator, so nobody is above them
 *   ''         — nothing to show
 *
 * `names` is a LIST and holds every applicable person — a team may have any
 * number of leads and Sales Team has two, so a plain member of it reports to
 * both until someone records which one specifically. Nothing here truncates.
 */
export function reportingManagerOf(user, teams, users) {
  const none = { names: [], source: '', team: '' };
  if (!user) return none;

  // An explicitly recorded manager is the answer, and is never labelled inherited.
  if (user.mapped_lead_name) {
    return { names: [user.mapped_lead_name], source: 'explicit', team: '' };
  }

  // An administrator is the top of the tree. Falling through to the step below
  // would have them reporting to their fellow admins, which is a peer, not a
  // manager.
  if (isAdministrator(user)) return { names: [], source: 'top', team: '' };

  const names = [];
  const add = (id, name) => {
    if (!name || id === user.id || names.includes(name)) return;
    names.push(name);
  };

  // Primary lead first, then the rest, skipping the person themselves: a second
  // lead reports to the primary one, not to themselves.
  //
  // The team's PRIMARY lead skips this step entirely rather than merely being
  // filtered out of it. Filtering alone left the other leads standing, so Sales
  // Team's primary lead was shown reporting to their own second lead — a peer,
  // and the wrong way round. They belong to the administrators step below.
  const team = (teams || []).find((t) => t.id === user.team_id) || null;
  const leadsOwnTeam = !!team && !!team.team_lead_id && team.team_lead_id === user.id;
  if (team && !leadsOwnTeam) {
    add(team.team_lead_id, team.team_lead_name);
    (team.team_leads || []).forEach((l) => add(l.id, l.name));
    if (names.length) return { names, source: 'team', team: team.name };
  }

  // Nobody above them inside their own team — they lead it, or it records no
  // lead. The team's MANAGER comes next, because a team can be run by somebody
  // who is not in it and is not flagged a lead, which is what `managed_team`
  // means. Without this step the chain skipped them entirely and jumped straight
  // to the administrators.
  managersOf(team, users).forEach((m) => add(m.id, m.name));
  if (names.length) return { names, source: 'manager', team: team ? team.name : '' };

  // Nobody named for the team at all. Administrators are who is left.
  administratorsOf(users).forEach((a) => add(a.id, a.name));
  if (names.length) return { names, source: 'admin', team: team ? team.name : '' };

  return none;
}

/**
 * The people who could be recorded as `user`'s manager, grouped for a <select>.
 *
 * Three groups, because they are different kinds of answer; the leads of the
 * person's own team, that team's MANAGER, and the administrators. All are
 * offered for everyone, because a team lead has no lead above them and would
 * otherwise have nothing to pick.
 *
 * The manager group is why this takes `users`. Appointing somebody manager of a
 * team is recorded on the PERSON, in `managed_team`, and the team payload knows
 * nothing about it, so a team whose manager was set before anyone was added to
 * it offered leads that did not exist yet and the administrators, never the
 * manager who had just been appointed to run it.
 */
export function managerOptionGroups(team, user, users) {
  const seen = new Set([user && user.id]);
  const groups = [];

  const collect = (label, candidates) => {
    const items = [];
    candidates.forEach(({ id, name }) => {
      if (!id || !name || seen.has(id)) return;
      seen.add(id);
      items.push({ id, name });
    });
    if (items.length) groups.push({ label, items });
  };

  collect(team ? `${team.name} leads` : 'Team leads', team
    ? [{ id: team.team_lead_id, name: team.team_lead_name }, ...(team.team_leads || [])]
    : []);
  const managers = managersOf(team, users);
  collect(team ? `${team.name} manager${managers.length > 1 ? 's' : ''}` : 'Team managers', managers);
  collect('Administrators', administratorsOf(users));

  return groups;
}

