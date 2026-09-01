import { useMemo, useState } from 'react';
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

/**
 * Admin-only, and gated BEFORE anything fetches.
 *
 * The gate used to be `canView('performance')`, which the Permissions grid can
 * grant to any team — while /api/event-performance/ has always been
 * IsAdminRole (backend/event_performance/views.py). Two different questions
 * about one surface, and the looser one was the one the UI asked: a team ticked
 * into the Performance module got the rail entry, the table and this drawer,
 * all showing per-event revenue and paid/unpaid delegate counts across the
 * whole catalogue. `isAdmin` asks exactly what the server asks, so the page and
 * the endpoint can no longer disagree about who this is for.
 *
 * The hooks below still run unconditionally — the early return sits after them
 * — but they must not FETCH for a denied session, or a non-admin opening
 * /performance directly would fire two 403s on the way to the No Access screen.
 * `useFetch` is therefore handed a no-op for anyone who fails the gate.
 *
 * The route stays ungated in App.jsx like every other page; this is the guard.
 * `reason` overrides the default "ask an administrator under Roles" copy, which
 * would be a lie here: no grant under Roles opens this page.
 */
export default function EventPerformancePage() {
  const { isAdmin } = useSession();
  const toast = useToast();
  const [drawerRow, setDrawerRow] = useState(null);
  const { data: perfRows } = useFetch(
    isAdmin ? perfApi.list : () => Promise.resolve([]),
    [isAdmin],
    { initialData: [] },
  );

  const rows = useMemo(() => (perfRows || []).map((e, i) => ({
    id: e.event_code, event_code: e.event_code, name: e.event_name, status: e.status,
    reps: 0, followups: 0, mailshots: 0, notes: 0, bookings: e.total_delegates, offset: i,
  })), [perfRows]);

  if (!isAdmin) {
    return (
      <NoAccessPage
        module="Event Performance"
        reason="Event performance figures are restricted to administrators. This page is not part of the module permissions, so it cannot be granted under Roles."
      />
    );
  }


  return (
    <>
      <DataTable
        rows={rows} noun="events" pageSize={1000} defaultSort={{ key: 'offset', dir: 'asc' }} searchPlaceholder="Search event…"
        // No tab strip here to fold this into (see BookingsPage /
        // TicketCentralPage), so it rides on the table's own toolbar row
        // instead of a PageHead row of its own — one fewer row of height.
        extraToolbar={<button className="btn btn-p" onClick={() => toast('Pick an event row to log activity against it', 'nf')}><Icon name="plus" size={15} />Log activity</button>}
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
