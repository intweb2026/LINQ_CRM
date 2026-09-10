import { useCallback, useEffect, useState } from 'react';
import { EmptyState, Tabs } from '../components/UI';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import { Icon } from '../lib/icons';
import * as api from '../api/attendance';
import EventPicker from './preEventDocs/EventPicker';
import QrScanner from './attendance/QrScanner';
import ScanResult from './attendance/ScanResult';
import AttendeeRoster from './attendance/AttendeeRoster';
import ManualCheckIn from './attendance/ManualCheckIn';

const STORE_KEY = 'att_event';
const LOG_PAGE_SIZE = 50;

export const TAB = { SCAN: 'scan', LOG: 'log', ROSTER: 'roster' };

export function tabPlan(mayScan, counts = {}) {
  return {
    tabs: [
      ...(mayScan ? [{ id: TAB.SCAN, label: 'Scan' }] : []),
      { id: TAB.LOG, label: 'Attendance', count: counts.arrived },
      { id: TAB.ROSTER, label: 'Roster', count: counts.expected },
    ],
    landing: TAB.LOG,
  };
}

export const cameraLiveOn = (tab) => tab === TAB.SCAN;

function readStored() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function writeStored(event) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      event_code: event.event_code, edition: event.edition ?? null,
    }));
  } catch { /* a private window is not a reason to break the page */ }
}

const same = (a, b) =>
  a && b && a.event_code === b.event_code && (a.edition ?? null) === (b.edition ?? null);

const stamp = (value) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
};

export default function AttendancePage() {
  const { can, isAdmin } = useSession();
  const mayView = can('view', 'attendance');
  const mayScan = mayView && can('create', 'attendance');

  const [tab, setTab] = useState(tabPlan(true).landing);

  const [scanning, setScanning] = useState(false);
  const [ev, setEv] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  const events = useFetch(api.events, []);

  useEffect(() => {
    if (ev || !events.data) return;
    const list = events.data;
    if (!list.length) return;
    const stored = readStored();
    const found = stored && list.find((e) => same(e, stored));
    setEv(found || (list.length === 1
      ? list[0]
      : list.reduce((a, b) => (b.delegates > a.delegates ? b : a))));
  }, [events.data, ev]);

  function pick(event) {
    setEv(event);
    setOutcome(null);
    writeStored(event);
  }

  function pickTab(next) {
    setTab(next);
    setScanning(cameraLiveOn(next));
  }

  const checkIn = useCallback(async (payload, source) => {
    if (!ev) return null;
    setBusy(true);
    const result = await api.scan(ev, payload, source);
    setOutcome(result);
    setBusy(false);
    if (result?.result === api.RESULTS.CHECKED_IN) setVersion((n) => n + 1);
    return result;
  }, [ev]);

  const summary = useFetch(
    () => (ev ? api.summary(ev) : Promise.resolve(null)),
    [ev?.event_code, ev?.edition, version],
  );

  if (!mayView) {
    return <EmptyState icon="lock" title="No access to QR Attendance" />;
  }

  if (events.loading) return <p className="att-quiet">Loading events…</p>;

  if (!events.data?.length) {
    return (
      <EmptyState
        icon="calendar"
        title="No events assigned to you"
        body={isAdmin
          ? 'No event in the catalogue has any bookings yet, so there is nobody '
            + 'to check in.'
          : 'This is an assignment problem rather than an empty database. Ask an '
            + 'administrator to assign you to an event on the Users page; the '
            + 'door only opens for events you are named on.'}
      />
    );
  }

  const { tabs } = tabPlan(mayScan, {
    arrived: summary.data?.arrived, expected: summary.data?.expected,
  });

  return (
    <div className="att-page">
      <div className="att-sticky">
        <EventPicker events={events.data} value={ev} onPick={pick} />
        {summary.data ? (
          <div className="att-counts">
            <span><b>{summary.data.arrived}</b> arrived</span>
            <span><b>{summary.data.expected}</b> expected</span>
            <span><b>{summary.data.outstanding}</b> to come</span>
          </div>
        ) : null}
        <Tabs list={tabs} active={tab} onPick={pickTab} />
      </div>

      {!ev ? (
        <EmptyState icon="calendar" title="Choose an event to begin" />
      ) : tab === TAB.SCAN ? (
        <div className="att-scan">
          {/* Mounted only while `scanning`, so both Stop and leaving this tab
              release the device through the component's own teardown. */}
          {scanning ? (
            <>
              <QrScanner onCode={checkIn} busy={busy} />
              <button className="btn att-stop" onClick={() => setScanning(false)}>
                <Icon name="pause" size={14} /> Stop camera
              </button>
            </>
          ) : (
            <div className="att-camera-off">
              <Icon name="qr" size={26} />
              <h4>The camera is off</h4>
              <p>Nothing is being recorded and no device is held open.</p>
              <button className="btn pri" onClick={() => setScanning(true)}>
                Start camera
              </button>
            </div>
          )}
          <ScanResult outcome={outcome} onDismiss={() => setOutcome(null)} />
          <details className="att-fallback">
            <summary>A badge will not scan</summary>
            <ManualCheckIn event={ev} onCheckIn={checkIn} />
          </details>
        </div>
      ) : tab === TAB.ROSTER ? (
        <>
          <ScanResult outcome={outcome} onDismiss={() => setOutcome(null)} />
          <AttendeeRoster event={ev} mayScan={mayScan} onCheckIn={checkIn}
            reloadKey={version} />
        </>
      ) : (
        <AttendanceLog event={ev} version={version} />
      )}
    </div>
  );
}

function AttendanceLog({ event, version }) {
  const [page, setPage] = useState(1);

  useEffect(() => { setPage(1); }, [event?.event_code, event?.edition]);

  const rows = useFetch(
    () => api.log(event, { page, pageSize: LOG_PAGE_SIZE }),
    [event?.event_code, event?.edition, page, version],
  );

  if (rows.loading && !rows.data) return <p className="att-quiet">Loading arrivals…</p>;
  if (rows.error) {
    return <EmptyState icon="warn" title="Could not load the arrival log"
      body="Could not reach the server. Try again." />;
  }
  if (!rows.data?.results?.length) {
    return <EmptyState icon="inbox" title="Nobody has checked in yet"
      body="Arrivals appear here the moment a badge is scanned." />;
  }

  const total = rows.data.totalPages || 1;
  return (
    <div className="att-log">
      <div className="att-tablewrap">
        <table className="att-table">
          <thead>
            <tr>
              <th>Attendee</th><th>Type</th><th>Company</th><th>Event</th>
              <th>Status</th><th>Checked in</th><th>Scanned by</th>
            </tr>
          </thead>
          <tbody>
            {rows.data.results.map((r) => (
              <tr key={r.id}>
                <td>
                  <b>{r.attendee_name}</b>
                  {r.attendee_email ? <span className="att-sub">{r.attendee_email}</span> : null}
                </td>
                <td>
                  <span className={`att-tag att-tag-${r.attendee_type}`}>
                    {r.attendee_type === 'speaker' ? 'Speaker' : 'Delegate'}
                  </span>
                </td>
                <td>{r.company_name || '—'}</td>
                <td>{[r.event_code, r.edition].filter(Boolean).join(' ')}</td>
                <td><span className="att-tag att-tag-in">{r.status}</span></td>
                <td>{stamp(r.checked_in_at)}</td>
                <td>{r.checked_in_by_name || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="att-pager">
        <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          <Icon name="chevL" size={14} /> Previous
        </button>
        <span>Page {rows.data.page} of {total} · {rows.data.count} arrivals</span>
        <button className="btn" disabled={page >= total} onClick={() => setPage((p) => p + 1)}>
          Next <Icon name="chevR" size={14} />
        </button>
      </div>
    </div>
  );
}
