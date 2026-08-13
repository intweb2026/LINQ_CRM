import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useSession } from '../../context/SessionContext';
import { apiErrorMessage } from '../../api/client';
import * as teamsApi from '../../api/teams';

/**
 * Create or edit a team. `team` null = create, an object = edit.
 *
 * The name is load-bearing beyond its label: User.save() re-derives a member's
 * `role` from keywords in it ("market research", "spex", "sales", …), and a name
 * containing "admin" makes every member a superuser. Renaming an existing team
 * therefore re-roles its members the next time each of them is saved, which is
 * why the edit path says so out loud rather than presenting name as cosmetic.
 */
export default function TeamFormModal({ team: t, onClose, onSaved }) {
  const isNew = !t;
  const toast = useToast();
  const confirm = useConfirm();
  const { can } = useSession();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: t?.name || '',
    color: t?.color || '#009CBC',
    description: t?.description || '',
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save() {
    const name = form.name.trim();
    if (!name) { toast('Team name is required', 'er'); return; }
    const payload = { name, color: form.color, description: form.description.trim() };
    setBusy(true);
    try {
      if (isNew) await teamsApi.create(payload);
      else await teamsApi.update(t.id, payload);
      onClose();
      toast((isNew ? 'Team created: ' : 'Team updated: ') + name, 'ok');
      onSaved();
    } catch (err) {
      toast(apiErrorMessage(err, isNew ? 'Could not create the team.' : 'Could not save the team.'), 'er');
    } finally {
      setBusy(false);
    }
  }

  async function del() {
    const ok = await confirm({
      title: 'Delete ' + t.name + '?', danger: true, ok: 'Delete', sub: 'This cannot be undone.',
      body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Archive instead if you only want it off the board. A team with members cannot be deleted — move them out first.</p>,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await teamsApi.remove(t.id);
      onClose();
      toast('Team deleted: ' + t.name, 'ok');
      onSaved();
    } catch (err) {
      // 409 when the team still has members; the detail names the count.
      toast(apiErrorMessage(err, 'Could not delete the team.'), 'er');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal size="sm" title={isNew ? 'Create team' : 'Edit ' + t.name}
      sub={isNew ? 'Adds a column to the board.' : 'Rename, recolour or describe this team.'}
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        {!isNew && can('delete', 'teams')
          ? <button className="btn btn-do" onClick={del} disabled={busy}><Icon name="trash" size={15} />Delete</button>
          : null}
        <button className="btn btn-p" onClick={save} disabled={busy}>
          <Icon name="check" size={15} />{busy ? 'Saving…' : (isNew ? 'Create team' : 'Save changes')}
        </button>
      </>}>
      <div className="fd" style={{ marginBottom: 12 }}>
        <label className="fd-l">Team name<span className="req">*</span></label>
        <input className="in" value={form.name} onChange={set('name')} placeholder="e.g. Market Research" />
        <span style={{ fontSize: 10.5, color: 'var(--text-4)', lineHeight: 1.45 }}>
          Members take their role from keywords in this name — sales, market research, data mining, spex, operations, speaker sales, telemarketing. A name containing "admin" grants members full administrator rights.
        </span>
      </div>
      <div className="fd" style={{ marginBottom: 12 }}>
        <label className="fd-l">Colour</label>
        <input className="in" type="color" value={form.color} onChange={set('color')} style={{ padding: 3 }} />
      </div>
      <div className="fd">
        <label className="fd-l">Description</label>
        <textarea className="in" value={form.description} onChange={set('description')} placeholder="What does this team do?" />
      </div>
    </Modal>
  );
}
