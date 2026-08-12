import { useState, useEffect } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { Av } from '../../components/Badge';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import * as teamsApi from '../../api/teams';

export default function AssignLeadModal({ team: t, onClose, onSaved }) {
  const toast = useToast();
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const mem = (users || []).filter((u) => u.team_id === t.id);
  const [sel, setSel] = useState(null);
  useEffect(() => {
    const currentLead = mem.find((u) => u.is_lead);
    if (currentLead) setSel(currentLead.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [users]);

  async function save() {
    if (!sel) { toast('Choose a member', 'er'); return; }
    await teamsApi.assignLead(t.id, sel);
    onClose(); toast('Lead updated for ' + t.name, 'ok'); onSaved();
  }

  return (
    <Modal size="sm" title="Assign lead" sub={t.name} onClose={onClose}
      footer={<><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" onClick={save}><Icon name="check" size={15} />Assign</button></>}>
      {mem.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {mem.map((u) => (
            <label className="pop-i" style={{ padding: 9 }} key={u.id}>
              <input type="radio" name="lead" checked={sel === u.id} onChange={() => setSel(u.id)} />
              <Av name={u.name} size="xs" /><span style={{ marginLeft: 8 }}>{u.name}</span>
            </label>
          ))}
        </div>
      ) : <p style={{ fontSize: 12.5, color: 'var(--text-4)' }}>No members in this team.</p>}
    </Modal>
  );
}
