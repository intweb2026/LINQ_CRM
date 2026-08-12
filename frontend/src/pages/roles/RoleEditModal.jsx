import { useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { CRM_MODULES, PERM_ACTIONS } from '../../lib/constants';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import * as rolesApi from '../../api/roles';

export default function RoleEditModal({ role: r, perms, onClose, onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const isNew = !r;
  const [label, setLabel] = useState(r ? r.display_label : '');
  const [color, setColor] = useState(r ? r.color : '#009CBC');
  const [description, setDescription] = useState(r ? r.description : '');
  const [grid, setGrid] = useState(() => {
    const g = {};
    CRM_MODULES.forEach((mo) => { g[mo.k] = {}; PERM_ACTIONS.forEach((a) => { g[mo.k][a] = !!(perms[mo.k] || {})[a]; }); });
    return g;
  });

  function toggle(mo, a) {
    if (r && r.system) return;
    setGrid((g) => ({ ...g, [mo]: { ...g[mo], [a]: !g[mo][a] } }));
  }

  async function save() {
    if (!label.trim()) { toast('Display label is required', 'er'); return; }
    await rolesApi.save({ id: r?.id, name: r?.name, display_label: label.trim(), color, description, permissions: grid });
    onClose();
    toast((isNew ? 'Role created: ' : 'Role updated: ') + label.trim(), 'ok');
    onSaved();
  }
  async function del() {
    onClose();
    const ok = await confirm({ title: 'Delete role?', sub: r.display_label, danger: true, ok: 'Delete', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Members holding this role keep their access until reassigned.</p> });
    if (ok) {
      await rolesApi.remove(r.name);
      toast('Role deleted: ' + r.display_label, 'ok');
      onSaved();
    }
  }

  return (
    <Modal size="lg" title={isNew ? 'Create role' : 'Edit ' + r.display_label} sub={isNew ? 'Define a new permission set.' : 'Adjust view / create / update / delete per module.'} onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose}>Cancel</button>
        {r && !r.system ? <button className="btn btn-do" onClick={del}><Icon name="trash" size={15} />Delete role</button> : null}
        <button className="btn btn-p" onClick={save}><Icon name="check" size={15} />{isNew ? 'Create role' : 'Save changes'}</button>
      </>}>
      <div className="fs">
        <div className="fs-t"><Icon name="shield" size={13} />Identity</div>
        <div className="fg">
          <div className="fd"><label className="fd-l">Display label<span className="req">*</span></label><input className="in" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Regional Manager" /></div>
          <div className="fd"><label className="fd-l">Colour</label><input className="in" type="color" value={color} onChange={(e) => setColor(e.target.value)} style={{ padding: 3 }} /></div>
          <div className="fd f"><label className="fd-l">Description</label><textarea className="in" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What can this role do?" /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="cols" size={13} />Permissions</div>
        <table className="pm">
          <thead><tr><th>Module</th>{PERM_ACTIONS.map((a) => <th key={a}>{a}</th>)}</tr></thead>
          <tbody>
            {CRM_MODULES.map((mo) => (
              <tr key={mo.k}>
                <td>{mo.l}</td>
                {PERM_ACTIONS.map((a) => (
                  <td key={a}><input type="checkbox" className="ck" checked={grid[mo.k][a]} disabled={r && r.system} onChange={() => toggle(mo.k, a)} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
