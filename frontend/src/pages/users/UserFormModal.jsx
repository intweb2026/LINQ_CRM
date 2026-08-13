import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { TEAM_ROLES, ROLE_FULL } from '../../lib/constants';
import { roleFromTeamName } from '../../lib/roleFromTeam';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useSession } from '../../context/SessionContext';
import { useFetch } from '../../hooks/useFetch';
import { apiErrorMessage } from '../../api/client';
import * as usersApi from '../../api/users';
import * as rolesApi from '../../api/roles';
import * as teamsApi from '../../api/teams';

/**
 * Create or edit a user account.
 *
 * `user` null/undefined = create, an object = edit. Both paths post to the same
 * endpoints; the only real difference is that create sends every field and edit
 * sends the ones that changed.
 *
 * TWO FIELDS LOOK ALIKE AND ARE NOT
 *   Role            the legacy `User.role` enum. It labels people and gates the
 *                   handful of `is_admin` checks left in the backend, and
 *                   User.save() RE-DERIVES it from the team's name for anyone
 *                   who is not an Admin — so it is shown with that caveat.
 *   Permission set  the CustomRole, which is what crm_permission() actually
 *                   reads. A user without one can see NOTHING, which is the
 *                   quiet failure mode this form exists to make visible.
 */
export default function UserFormModal({ user: u, onClose, onSaved }) {
  const isNew = !u;
  const toast = useToast();
  const confirm = useConfirm();
  const { can, user: me } = useSession();
  const { data: teams } = useFetch(teamsApi.list, [], { initialData: [] });
  const { data: roles } = useFetch(rolesApi.list, [], { initialData: [] });
  const TEAMS = teams || [];
  const ROLES = roles || [];
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
      custom_role_id: u?.custom_role_id ? String(u.custom_role_id) : '',
      status: u?.status || 'active',
      is_lead: !!u?.is_lead,
    };
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setChk = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.checked }));

  const chosenTeam = TEAMS.find((t) => String(t.id) === String(form.team_id)) || null;
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
    setForm((f) => ({ ...f, team_id: teamId, role: implied || f.role }));
  }

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
      custom_role_id: form.custom_role_id ? +form.custom_role_id : null,
      is_lead: form.is_lead,
      password: form.password,
    };
    setBusy(true);
    try {
      const saved = isNew ? await usersApi.create(payload) : await usersApi.update(u.id, payload);
      onClose();
      toast((isNew ? 'User created: ' : 'User updated: ') + (saved.name || payload.username), 'ok');
      onSaved();
    } catch (err) {
      // The modal STAYS OPEN on failure with the server's own reason. It used to
      // be possible for a save to fail and still close behind a success toast.
      toast(apiErrorMessage(err, isNew ? 'Could not create the user.' : 'Could not save the user.'), 'er');
    } finally {
      setBusy(false);
    }
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
        <div className="fs-t"><Icon name="shield" size={13} />What they can open</div>
        <div className="fg">
          <div className="fd f">
            <label className="fd-l">Permission set<span className="req">*</span></label>
            <select className="in" value={form.custom_role_id} onChange={set('custom_role_id')}>
              <option value="">— None. This user will see No Access everywhere —</option>
              {ROLES.map((r) => <option key={r.id} value={r.id}>{r.display_label}</option>)}
            </select>
            <span style={{ fontSize: 10.5, color: 'var(--text-4)', lineHeight: 1.45 }}>
              One of the sets you defined on the Roles page. It is the only thing on this form that decides which modules open and whether they are read-only.
            </span>
          </div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="team" size={13} />Where they sit</div>
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
        </div>
      </div>
    </Modal>
  );
}
