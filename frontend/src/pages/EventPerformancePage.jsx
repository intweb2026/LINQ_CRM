import { useMemo, useState } from 'react';
import { PageHead } from '../components/UI';
import DataTable from '../components/DataTable';
import Drawer from '../components/Drawer';
import { Icon } from '../lib/icons';
import { EvBadge, Who } from '../components/Badge';
import { nf } from '../lib/helpers';
import * as perfApi from '../api/eventPerformance';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';

function PerfDrawer({ row, onClose }) {
  const { data } = useFetch(() => perfApi.detail(row.event_code), [row.event_code], { initialData: null });
  if (!row) return null;
  const reps = (data?.reps || []).map((r) => r.rep_name);
  const fu = (data?.follow_ups || []).map((f) => ({ who: f.created_by_name || f.contact_name, when: f.follow_up_date, note: f.notes }));
  return (
    <Drawer wide onClose={onClose}
      head={<div><span className="mono" style={{ color: 'var(--t-600)' }}>{row.event_code}</span><h2>{row.name}</h2><p><EvBadge value={row.status} /></p></div>}
      foot={<><button className="btn btn-s" onClick={onClose}>Close</button><button className="btn btn-p"><Icon name="plus" size={15} />Add follow-up</button></>}
    >
      <div className="ms">
        <div><div className="l">Reps</div><div className="v">{row.reps}</div></div>
        <div><div className="l">Mailshots</div><div className="v">{row.mailshots}</div></div>
        <div><div className="l">Notes</div><div className="v">{row.notes}</div></div>
      </div>
      <div className="sl">Assigned reps</div>
      {reps.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 18 }}>{reps.map((n, i) => <Who key={i} name={n} />)}</div>
      ) : <p style={{ fontSize: 12, color: 'var(--text-4)', marginBottom: 18 }}>No reps assigned yet.</p>}
      <div className="sl">Open follow-ups</div>
      {fu.length ? fu.map((f, i) => (
        <div className="nt" key={i}><div className="nt-h"><span className="w">{f.who}</span><span className="d">{f.when}</span></div><div className="nt-x">{f.note}</div></div>
      )) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>No open follow-ups.</p>}
    </Drawer>
  );
}

export default function EventPerformancePage() {
  const { canView } = useSession();
  const toast = useToast();
  const [drawerRow, setDrawerRow] = useState(null);
  const { data: perfRows } = useFetch(perfApi.list, [], { initialData: [] });

  const rows = useMemo(() => (perfRows || []).map((e, i) => ({
    id: e.event_code, event_code: e.event_code, name: e.event_name, status: e.status,
    reps: 0, followups: 0, mailshots: 0, notes: 0, bookings: e.total_delegates, offset: i,
  })), [perfRows]);

  if (!canView('performance')) return <NoAccessPage module="Event Performance" />;

  return (
    <>
      <PageHead title="Event Performance" sub="Active-edition tracker — rep coverage, follow-ups, mailshots and notes for every event with an owner."
        actions={<button className="btn btn-p" onClick={() => toast('Pick an event row to log activity against it', 'nf')}><Icon name="plus" size={15} />Log activity</button>} />
      <DataTable
        rows={rows} noun="events" pageSize={50} defaultSort={{ key: 'offset', dir: 'asc' }} searchPlaceholder="Search event…"
        cols={[
          { key: 'event_code', label: 'Event', cell: (v) => <span className="mono lnk">{v}</span> },
          { key: 'name', label: 'Name', cls: 'st' },
          { key: 'status', label: 'Status', cell: (v) => <EvBadge value={v} /> },
          { key: 'bookings', label: 'Bookings', num: true, cell: (v) => nf(v) },
          { key: 'reps', label: 'Reps assigned', num: true },
          { key: 'followups', label: 'Follow-ups', num: true, cell: (v) => (v ? <b style={{ color: 'var(--amber-tx)' }}>{v}</b> : <span className="dim">0</span>) },
          { key: 'mailshots', label: 'Mailshots', num: true },
          { key: 'notes', label: 'Notes', num: true },
        ]}
        onRow={(r) => setDrawerRow(r)}
      />
      {drawerRow ? <PerfDrawer row={drawerRow} onClose={() => setDrawerRow(null)} /> : null}
    </>
  );
}
