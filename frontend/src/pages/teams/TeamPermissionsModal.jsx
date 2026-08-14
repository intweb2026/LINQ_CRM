import { useState } from 'react';
import Modal from '../../components/Modal';
import PermissionGrid from '../../components/PermissionGrid';
import { Icon } from '../../lib/icons';
import { useToast } from '../../context/ToastContext';
import { apiErrorMessage } from '../../api/client';
import * as teamsApi from '../../api/teams';

/**
 * Edit what a team opens.
 *
 * Saving here changes access for EVERY member at once, which is the whole point
 * of the team being the role — and also the reason the footer says how many
 * people that is rather than leaving it to be discovered.
 */
export default function TeamPermissionsModal({ team: t, onClose, onSaved }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [allAccess, setAllAccess] = useState(!!t.is_all_access);
  const [grid, setGrid] = useState(() => teamsApi.toMatrix(t.permissions));

  function toggle(module, action) {
    if (allAccess) return;
    setGrid((g) => ({ ...g, [module]: { ...g[module], [action]: !g[module][action] } }));
  }

  async function save() {
    setBusy(true);
    try {
      await teamsApi.savePermissions(t.id, grid, { isAllAccess: allAccess });
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not save the permissions.'), 'er');
      setBusy(false);
      return;
    }
    setBusy(false);
    onClose();
    toast('Permissions updated for ' + t.name, 'ok');
    onSaved();
  }

  const count = t.member_count || 0;

  return (
    <Modal
      size="lg"
      title={t.name}
      sub={`What this team can open. Applies to all ${count} member${count === 1 ? '' : 's'}.`}
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-p" onClick={save} disabled={busy}>
          <Icon name="check" size={15} />{busy ? 'Saving…' : 'Save permissions'}
        </button>
      </>}
    >
      <div className="fs">
        <div className="fs-t"><Icon name="shield" size={13} />Full access</div>
        <label className="fd-l" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" className="ck" checked={allAccess} onChange={(e) => setAllAccess(e.target.checked)} />
          Everything, in every module
        </label>
        <span style={{ fontSize: 10.5, color: 'var(--text-4)', display: 'block', marginTop: 6, lineHeight: 1.45 }}>
          Overrides the grid below and keeps overriding it as new modules are added. Reserve it for administrators.
        </span>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="cols" size={13} />Modules</div>
        {allAccess ? (
          <p style={{ fontSize: 12, color: 'var(--text-4)', marginBottom: 10 }}>
            Not used while full access is on. Untick it above to set the grid.
          </p>
        ) : null}
        <div style={allAccess ? { opacity: 0.45, pointerEvents: 'none' } : undefined}>
          <PermissionGrid value={grid} onToggle={toggle} disabled={allAccess} />
        </div>
      </div>
    </Modal>
  );
}
