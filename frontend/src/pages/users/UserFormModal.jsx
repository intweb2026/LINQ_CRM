import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { TEAM_ROLES, ROLE_FULL, CRM_MODULES, PERM_ACTIONS } from '../../lib/constants';
import { roleFromTeamName } from '../../lib/roleFromTeam';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useSession } from '../../context/SessionContext';
import { useFetch } from '../../hooks/useFetch';
import { managerOptionGroups } from '../../lib/reporting';
import { apiErrorMessage } from '../../api/client';
import PermissionGrid, { PermissionLegend } from '../../components/PermissionGrid';
import * as usersApi from '../../api/users';
import * as teamsApi from '../../api/teams';

/**
 * Create or edit a user account.
 *
 * `user` null/undefined = create, an object = edit. Both paths post to the same
 * endpoints; the only real difference is that create sends every field and edit
 * sends the ones that changed.
 *
 * ACCESS COMES FROM THE TEAM. There is no permission set to pick any more: put
 * someone in a team and they inherit its grid. The grid shown here is their
 * EFFECTIVE access, tinted to say which cells came from the team and which were
 * set for this person, and every difference is saved as a delta so that widening
 * the team later still reaches them.
 *
 * The grid is editable on BOTH paths. Giving one person an extra module is part
 * of hiring them, not a follow-up task, so a create that could only inherit
 * meant saving the form, finding the row again and reopening it — with a window
 * in between where the account existed with the wrong access. Create takes two
 * requests because the exceptions need an id to hang off; see save().
 *
 * `Role` remains, and remains a label: it names a job function, drives the
 * Users list filters, and User.save() fills it in from the team's name. It
 * grants nothing.
 */
export default function UserFormModal({ user: u, users, onClose, onSaved }) {
  const isNew = !u;
  const toast = useToast();
  const confirm = useConfirm();
  const { can, user: me } = useSession();
  const { data: teams } = useFetch(teamsApi.list, [], { initialData: [] });
  const TEAMS = teams || [];
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState(() => {
    return {
      first_name: u?.first_name || '',
      last_name: u?.last_name || '',
      username: u?.username || '',
      email: u?.email || '',
      password: '',
      role: u?.role || 'sales',
      team_id: u?.team_id ? String(u.team_id) : '',
      mapped_lead_id: u?.mapped_lead_id ? String(u.mapped_lead_id) : '',
      status: u?.status || 'active',
      is_lead: !!u?.is_lead,
      login_access: u ? u.login_access !== false : true,
    };
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setChk = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.checked }));

  const chosenTeam = TEAMS.find((t) => String(t.id) === String(form.team_id)) || null;
  // Two groups — the chosen team's leads, then the administrators — and the person
  // being edited never appears in either: nobody reports to themselves, and a
  // team's primary lead is exactly the case where that would otherwise happen.
  // Administrators are offered to everyone, because a team lead has no lead above
  // them and would otherwise have nothing to pick.
  const managerGroups = managerOptionGroups(chosenTeam, u, users);
  const managerChoices = managerGroups.flatMap((g) => g.items);
  const impliedRole = chosenTeam ? roleFromTeamName(chosenTeam.name) : null;
  const roleOverridden = !!impliedRole && form.role !== impliedRole;

  /**
   * Picking a team fills the role in, and leaves it editable.
   *
   * The server derives the role from the team's name on save, so a form that
   * did not show that left the user staring at a Role they had picked and a
   * Role that was about to be stored, with no hint they differed. Filling it in
   * here makes the two agree by default; changing it afterwards is honoured,
   * because the request names a role and a named role wins server-side.
   */
  function setTeam(e) {
    const teamId = e.target.value;
    const team = TEAMS.find((t) => String(t.id) === String(teamId));
    const implied = team ? roleFromTeamName(team.name) : null;
    // The reporting manager is a lead OF THIS TEAM, so moving team invalidates it.
    // Left alone it would keep pointing at a lead of the team the person just left.
    setForm((f) => ({ ...f, team_id: teamId, role: implied || f.role, mapped_lead_id: '' }));
    // Exceptions were relative to the OLD team's grid. Carrying them across
    // would mean "revoke Bookings delete" following someone into a team that
    // never granted it, and reading afterwards as a deliberate decision about
    // the new team. Moving team is a fresh start; grant again if still needed.
    setGrid(teamsApi.toMatrix(team ? team.permissions : null));
  }

  /**
   * The grid is EFFECTIVE access, not the stored deltas.
   *
   * It starts as what this person resolves to today — team plus their own
   * exceptions — and api/users.savePermissions works the delta out again by
   * comparing against the team on save. So ticking a cell back to whatever the
   * team says removes the exception rather than freezing agreement, and the
   * person keeps following the team on every cell nobody touched.
   */
  const teamGrid = chosenTeam ? teamsApi.toMatrix(chosenTeam.permissions) : teamsApi.emptyMatrix();
  const [grid, setGrid] = useState(
    () => (u ? teamsApi.toMatrix(u.effective_permissions) : teamsApi.emptyMatrix()),
  );
  const allAccess = !!chosenTeam?.is_all_access;

  function differs(a, b) {
    return CRM_MODULES.some((mo) => PERM_ACTIONS.some(
      (act) => !!(a[mo.k] || {})[act] !== !!(b[mo.k] || {})[act],
    ));
  }

  /**
   * Two different questions, and they have different answers on an edit.
   *
   *   hasExceptions  the grid on screen differs from the TEAM, so there is
   *                  something to offer to clear.
   *   needsSave      the grid differs from where this form STARTED, so the
   *                  second request is worth making.
   *
   * They come apart when an existing exception is cleared: the grid then agrees
   * with the team, so there is nothing to reset, but the stored delta still has
   * to be deleted. Gating the save on hasExceptions would leave it behind and
   * the cleared box would come back on the next open.
   */
  const hasExceptions = differs(grid, teamGrid);
  const needsSave = isNew
    ? hasExceptions
    : differs(grid, teamsApi.toMatrix(u.effective_permissions));

  function toggleCell(module, action) {
    if (allAccess) return;
    setGrid((g) => ({ ...g, [module]: { ...g[module], [action]: !g[module][action] } }));
  }
  const resetGrid = () => setGrid(teamsApi.toMatrix(teamGrid));

  function validate() {
    if (!form.username.trim()) return 'Username is required';
    if (!form.email.trim()) return 'Email is required — it is how people sign in';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) return 'That email address does not look right';
    if (form.password && form.password.length < 8) return 'Password must be at least 8 characters';
    return null;
  }

  async function save() {
    const problem = validate();
    if (problem) { toast(problem, 'er'); return; }
    const payload = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      username: form.username.trim(),
      email: form.email.trim(),
      role: form.role,
      status: form.status,
      team_id: form.team_id ? +form.team_id : null,
      // null, not omitted: clearing a reporting manager has to reach the server.
      mapped_lead_id: form.mapped_lead_id ? +form.mapped_lead_id : null,
      is_lead: form.is_lead,
      login_access: form.login_access,
      password: form.password,
    };
    setBusy(true);
    let saved;
    try {
      saved = isNew ? await usersApi.create(payload) : await usersApi.update(u.id, payload);
    } catch (err) {
      // The modal STAYS OPEN on failure with the server's own reason. It used to
      // be possible for a save to fail and still close behind a success toast.
      toast(apiErrorMessage(err, isNew ? 'Could not create the user.' : 'Could not save the user.'), 'er');
      setBusy(false);
      return;
    }

    // Permissions are a SECOND request, and it has to be second either way: on a
    // create there is no id to hang them off until the account exists, and on an
    // edit the delta is worked out against the team, so a team change has to have
    // landed first or the exceptions would be measured against the old grid.
    //
    // Reported separately when it fails. The account is real by this point and
    // saying "could not create the user" would send someone off to create it a
    // second time; what actually needs redoing is the grid.
    if (needsSave) {
      try {
        await usersApi.savePermissions(saved.id, grid, teamGrid);
      } catch (err) {
        setBusy(false);
        onSaved();
        toast(
          `${saved.name || payload.username} was ${isNew ? 'created' : 'saved'}, but their permissions were not: `
          + apiErrorMessage(err, 'the request failed.') + ' Reopen them to try again.',
          'er',
        );
        return;
      }
    }

    setBusy(false);
    onClose();
    toast((isNew ? 'User created: ' : 'User updated: ') + (saved.name || payload.username), 'ok');
    onSaved();
  }

  async function del() {
    const ok = await confirm({
      title: 'Delete ' + u.name + '?', danger: true, ok: 'Delete',
      sub: '@' + u.username,
      body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>The account is removed permanently. Deactivate instead if they may come back.</p>,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await usersApi.remove(u.id);
      onClose();
      toast('User deleted: ' + u.name, 'ok');
      onSaved();
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not delete the user.'), 'er');
    } finally {
      setBusy(false);
    }
  }

  const isSelf = !isNew && me && (me.user_id === u.id || me.username === u.username);

  return (
    <Modal size="lg" title={isNew ? 'Add user' : 'Edit ' + u.name}
      sub={isNew ? 'Create an account and set what it can reach.' : '@' + u.username}
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        {!isNew && !isSelf && can('delete', 'users')
          ? <button className="btn btn-do" onClick={del} disabled={busy}><Icon name="trash" size={15} />Delete user</button>
          : null}
        <button className="btn btn-p" onClick={save} disabled={busy}>
          <Icon name="check" size={15} />{busy ? 'Saving…' : (isNew ? 'Create user' : 'Save changes')}
        </button>
      </>}>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Identity</div>
        <div className="fg">
          <div className="fd"><label className="fd-l">First name</label><input className="in" value={form.first_name} onChange={set('first_name')} placeholder="e.g. Ada" /></div>
          <div className="fd"><label className="fd-l">Last name</label><input className="in" value={form.last_name} onChange={set('last_name')} placeholder="e.g. Lovelace" /></div>
          <div className="fd"><label className="fd-l">Username<span className="req">*</span></label><input className="in mono" value={form.username} onChange={set('username')} placeholder="e.g. ada" autoComplete="off" /></div>
          <div className="fd"><label className="fd-l">Email<span className="req">*</span></label><input className="in" type="email" value={form.email} onChange={set('email')} placeholder="ada@iq-hub.com" autoComplete="off" /></div>
          <div className="fd f">
            <label className="fd-l">{isNew ? 'Password' : 'New password'}</label>
            <input className="in" type="password" value={form.password} onChange={set('password')} placeholder={isNew ? 'Optional — 8 characters minimum' : 'Leave blank to keep the current one'} autoComplete="new-password" />
            <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>Sign-in is by emailed one-time code, so a password is optional.</span>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="team" size={13} />Team, and the role it implies</div>
        <div className="fg">
          <div className="fd">
            <label className="fd-l">Team</label>
            <select className="in" value={form.team_id} onChange={setTeam}>
              <option value="">— Unassigned —</option>
              {TEAMS.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div className="fd">
            <label className="fd-l">Role</label>
            <select className="in" value={form.role} onChange={set('role')}>
              {TEAM_ROLES.map((r) => <option key={r} value={r}>{ROLE_FULL[r]}</option>)}
            </select>
            <span style={{ fontSize: 10.5, color: 'var(--text-4)', lineHeight: 1.45 }}>
              {roleOverridden
                ? `Set by hand. ${chosenTeam.name} would otherwise make this ${ROLE_FULL[impliedRole]}; your choice is kept.`
                : impliedRole
                  ? `Filled in from ${chosenTeam.name}. Change it if this person does something else.`
                  : 'Job function, shown on the Users list and used to filter it. Grants nothing by itself.'}
            </span>
          </div>
          <div className="fd">
            <label className="fd-l">Status</label>
            <select className="in" value={form.status} onChange={set('status')} disabled={isSelf}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="suspended">Suspended</option>
            </select>
            {isSelf ? <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>You cannot deactivate your own account.</span> : null}
          </div>
          <div className="fd">
            <label className="fd-l" style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 22 }}>
              <input type="checkbox" className="ck" checked={form.is_lead} onChange={setChk('is_lead')} />
              Team lead
            </label>
          </div>
          <div className="fd">
            <label className="fd-l">Reporting manager</label>
            {/* Scoped to the leads of the chosen team, because that is what the
                column means — "the specific team lead this member is mapped
                under". A team may have several, so this is a real choice rather
                than a formality: Sales Team has two. Any stored value that is no
                longer a lead is kept as an option so opening the form and saving
                it does not quietly drop it. */}
            <select className="in" value={form.mapped_lead_id} onChange={set('mapped_lead_id')} disabled={!managerChoices.length && !form.mapped_lead_id}>
              <option value="">— Not recorded —</option>
              {form.mapped_lead_id && !managerChoices.some((m) => String(m.id) === String(form.mapped_lead_id))
                ? <option value={form.mapped_lead_id}>{u?.mapped_lead_name || 'Current manager'}</option>
                : null}
              {managerGroups.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.items.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </optgroup>
              ))}
            </select>
            <span style={{ fontSize: 10.5, color: 'var(--text-4)', lineHeight: 1.45 }}>
              {!managerChoices.length
                ? 'Nobody available to report to \u2014 no team leads and no administrators.'
                : 'Left unrecorded, the profile shows the leads of this person\u2019s team, or the administrators if they lead it themselves.'}
            </span>
          </div>
          <div className="fd">
            <label className="fd-l" style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 22 }}>
              <input type="checkbox" className="ck" checked={form.login_access} onChange={setChk('login_access')} />
              Provide login access
            </label>
            <span style={{ fontSize: 10.5, color: 'var(--text-4)', lineHeight: 1.45 }}>
              Off means the account still exists, and everything assigned to it stays
              assigned, but Google Sign-In refuses it.
            </span>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t">
          <Icon name="shield" size={13} />What they can open
          {hasExceptions && !allAccess ? (
            <button className="btn btn-g btn-sm" style={{ marginLeft: 'auto' }} onClick={resetGrid} disabled={busy}>
              <Icon name="refresh" size={13} />Back to the team
            </button>
          ) : null}
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-4)', lineHeight: 1.5, marginBottom: 10 }}>
          {chosenTeam
            ? <>Inherited from <b>{chosenTeam.name}</b>. Tick to add something on top, untick to take something away; anything you leave alone keeps following the team, including when {chosenTeam.name} changes later.</>
            : 'No team, so nothing is inherited. Anything ticked here is granted to this person alone.'}
        </p>
        {allAccess ? (
          <p style={{ fontSize: 12, color: 'var(--text-4)' }}>
            <b>{chosenTeam.name}</b> has full access, so there is nothing to add.
          </p>
        ) : (
          <>
            <PermissionGrid value={grid} inherited={teamGrid} onToggle={toggleCell} disabled={busy} />
            <PermissionLegend />
          </>
        )}
      </div>
    </Modal>
  );
}
