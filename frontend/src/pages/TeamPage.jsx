import { useState } from 'react';
import { PageHead, Tabs } from '../components/UI';
import DataTable from '../components/DataTable';
import Drawer from '../components/Drawer';
import { Icon } from '../lib/icons';
import { Who, RoleBadge, EvBadge } from '../components/Badge';
import { nf, rel } from '../lib/helpers';
import { ROLE_FULL } from '../lib/constants';
import * as myTeamApi from '../api/myTeam';
import { useFetch } from '../hooks/useFetch';

// Mirrors TeamPage.jsx from the original design — routed with NO permission
// gate, so every signed-in user can see rep-level performance here.
function MyTeamDrawer({ rep, onClose }) {
  const [tab, setTab] = useState('events');
  if (!rep) return null;

  return (
    <Drawer
      wide onClose={onClose}
      head={<div style={{ display: 'flex', alignItems: 'center', gap: 11 }}><Who name={rep.username} size="lg" /><div><h2>{rep.username}</h2><p>{rep.email} · {ROLE_FULL[rep.role]}</p></div></div>}
      tabs={<Tabs list={[{ id: 'events', label: 'Event-wise breakdown' }, { id: 'activity', label: 'Activity log' }]} active={tab} onPick={setTab} />}
      foot={<button className="btn btn-s" onClick={onClose}>Close</button>}
    >
      {tab === 'events' ? (
        !rep.events.length ? (
          <div className="mt"><div className="mt-i"><Icon name="calendar" size={21} /></div><h3>No assigned events</h3><p>Nothing tracked against {rep.username} yet.</p></div>
        ) : (
          <table className="gt">
            <thead><tr><th>Event</th><th>Status</th><th className="num">Invoices</th><th className="num">Paid</th><th className="num">Pending</th></tr></thead>
            <tbody>
              {rep.events.map((e) => (
                <tr key={e.event_id}>
                  <td><div style={{ fontWeight: 650, color: 'var(--text)' }}>{e.event_name}</div><div className="mono" style={{ fontSize: 10.5, color: 'var(--t-600)' }}>{e.event_code}</div></td>
                  <td><EvBadge value={e.event_status} /></td>
                  <td className="num">{nf(e.total_invoices)}</td>
                  <td className="num" style={{ color: 'var(--green)', fontWeight: 650 }}>{nf(e.paid_invoices)}</td>
                  <td className="num" style={{ color: 'var(--amber)', fontWeight: 650 }}>{nf(e.pending_invoices)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : rep.activity.length ? (
        <div className="tl">
          {rep.activity.map((a) => (
            <div className="tl-i" key={a.id}><span className="tl-d"><Icon name="receipt" size={10} /></span><div><div className="tl-t">{a.action}</div>{a.details ? <div className="tl-s">{a.details}</div> : null}<div className="tl-m">{rel(a.created_at)}</div></div></div>
          ))}
        </div>
      ) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>No recent activity.</p>}
    </Drawer>
  );
}

export default function TeamPage() {
  const { data: reps } = useFetch(myTeamApi.list, [], { initialData: [] });
  const MYTEAM_REPS = reps || [];
  const [drawerRep, setDrawerRep] = useState(null);

  return (
    <>
      <PageHead title="My Team" sub="Event-wise performance across the team — pick anyone to see their invoice and attendance breakdown." />
      <DataTable
        rows={MYTEAM_REPS} noun="people" pageSize={50} defaultSort={{ key: 'username', dir: 'asc' }} searchPlaceholder="Search a name…"
        cols={[
          { key: 'username', label: 'Team member', cls: 'st', cell: (v, r) => <Who name={v} sub={r.email} size="md" mono /> },
          { key: 'role', label: 'Role', cell: (v) => <RoleBadge value={v} /> },
          { key: 'total_events', label: 'Events', num: true },
          { key: 'events', label: 'Open invoices', num: true, cell: (v) => nf(v.reduce((s, e) => s + e.pending_invoices, 0)) },
        ]}
        onRow={(r) => setDrawerRep(r)}
      />
      {drawerRep ? <MyTeamDrawer rep={drawerRep} onClose={() => setDrawerRep(null)} /> : null}
    </>
  );
}
