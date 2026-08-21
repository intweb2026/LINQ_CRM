import { useState, useEffect } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { Av } from '../../components/Badge';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { apiErrorMessage } from '../../api/client';
import * as teamsApi from '../../api/teams';

/**
 * Appoint a team's leads — ANY NUMBER OF THEM.
 *
 * This was a radio group posting a single id. A team may have several leads (Sales
 * Team has two), and the endpoint has always accepted a list, so the single-select
 * did two wrong things at once: it could not appoint a second lead, and because
 * assign-lead clears is_team_lead across the whole team before applying what it
 * was sent, saving here DEMOTED every lead the form had not offered to keep. Leads
 * appointed through the per-user "Team lead" checkbox were wiped by anyone who
 * opened this modal and pressed Save.
 *
 * Both faults come from the same root — the form has to submit the complete list —
 * so the checkboxes below are pre-ticked with every current lead. Saving without
 * touching anything is now a no-op instead of a demotion.
 *
 * PRIMARY is a separate choice on top of the selection, because the two are
 * genuinely different: which people lead the team, and which one of them the rest
 * of the app treats as its single team_lead. It is sent first in the list, which
 * is how the endpoint reads it.
 */
export default function AssignLeadModal({ team: t, onClose, onSaved }) {
  const toast = useToast();
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const mem = (users || []).filter((u) => u.team_id === t.id);

  const [sel, setSel] = useState([]);
  const [primary, setPrimary] = useState(null);
  const [saving, setSaving] = useState(false);

  // Seed from what the team already has, so an untouched save changes nothing.
  // The FK lead is the primary; the rest are the is_team_lead members.
  useEffect(() => {
    const current = mem.filter((u) => u.is_lead).map((u) => u.id);
    const fk = t.team_lead_id && current.includes(t.team_lead_id) ? t.team_lead_id : current[0] || null;
    setSel(current);
    setPrimary(fk);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [users, t.id]);

  function toggle(id) {
    setSel((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      // Primary has to stay one of the selected. Dropping the primary promotes
      // whoever is left rather than posting a primary who is not a lead.
      setPrimary((p) => (next.includes(p) ? p : next[0] || null));
      return next;
    });
  }

  async function save() {
    // Primary first — the endpoint takes leads[0] as the team_lead FK.
    const ordered = primary ? [primary, ...sel.filter((id) => id !== primary)] : sel;
    setSaving(true);
    try {
      await teamsApi.assignLead(t.id, ordered);
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not update the leads.'), 'er');
      setSaving(false);
      return;
    }
    onClose();
    toast(
      ordered.length
        ? `${t.name} now has ${ordered.length} lead${ordered.length > 1 ? 's' : ''}`
        : `${t.name} has no leads`,
      'ok',
    );
    onSaved();
  }

  return (
    <Modal
      size="sm" title="Assign leads" sub={t.name} onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose}>Cancel</button>
        <button className="btn btn-p" onClick={save} disabled={saving}>
          <Icon name="check" size={15} />{saving ? 'Saving…' : 'Save leads'}
        </button>
      </>}
    >
      {mem.length ? (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {mem.map((u) => {
              const on = sel.includes(u.id);
              return (
                <div className="pop-i" style={{ padding: 9, display: 'flex', alignItems: 'center', gap: 8 }} key={u.id}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0, cursor: 'pointer' }}>
                    <input type="checkbox" className="ck" checked={on} onChange={() => toggle(u.id)} />
                    <Av name={u.name} size="xs" />
                    <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{u.name}</span>
                  </label>
                  {/* Only a selected lead can be the primary one. */}
                  <label
                    style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em', color: on ? 'var(--text-4)' : 'var(--n-200)', cursor: on ? 'pointer' : 'default', flexShrink: 0 }}
                    title={on ? 'The one lead the rest of the app treats as this team’s lead' : 'Tick this person as a lead first'}
                  >
                    <input
                      type="radio" name={`primary-${t.id}`} checked={primary === u.id}
                      disabled={!on} onChange={() => setPrimary(u.id)}
                    />
                    Primary
                  </label>
                </div>
              );
            })}
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-4)', lineHeight: 1.5, marginTop: 12 }}>
            {sel.length === 0
              ? 'Nothing ticked — saving will leave this team with no leads.'
              : `${sel.length} lead${sel.length > 1 ? 's' : ''}. Tick as many as you need; the primary is the one shown wherever a single team lead is expected.`}
          </p>
        </>
      ) : <p style={{ fontSize: 12.5, color: 'var(--text-4)' }}>No members in this team.</p>}
    </Modal>
  );
}
