import Drawer from '../../components/Drawer';
import { Icon } from '../../lib/icons';
import { plur, rel } from '../../lib/helpers';
import * as usersApi from '../../api/users';
import * as teamsApi from '../../api/teams';
import { useFetch } from '../../hooks/useFetch';

const ACTION_LABEL = {
  member_moved: 'moved', member_removed: 'removed', member_added: 'joined the team',
  lead_assigned: 'set as lead', team_renamed: 'renamed the team', team_deleted: 'deleted the team',
  team_archived: 'archived the team', team_created: 'created the team',
};

export default function TeamActivityDrawer({ team: t, onClose }) {
  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: feed } = useFetch(() => teamsApi.activity(t.id), [t.id], { initialData: [] });
  const mem = (users || []).filter((u) => u.team_id === t.id);
  const rows = feed || [];

  return (
    <Drawer onClose={onClose} head={<div><h2>{t.name}</h2><p>{plur(mem.length, 'member')} · activity</p></div>} foot={<button className="btn btn-s" onClick={onClose}>Close</button>}>
      {rows.length ? (
        <div className="af">
          {rows.map((f, i) => (
            <div className="af-i" key={i}><span className="af-d"><Icon name="receipt" size={10} /></span><span className="af-b"><span className="t"><b>{f.user_name || f.actor_name || 'Someone'}</b> {ACTION_LABEL[f.action_type] || f.action_type}{f.notes ? ' — ' + f.notes : ''}</span><span className="m">{rel(f.created_at)}</span></span></div>
          ))}
        </div>
      ) : <p style={{ fontSize: 12.5, color: 'var(--text-4)' }}>No recent activity.</p>}
    </Drawer>
  );
}
