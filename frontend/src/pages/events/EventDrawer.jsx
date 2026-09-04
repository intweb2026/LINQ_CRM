import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Drawer from '../../components/Drawer';
import { Tabs } from '../../components/UI';
import { Icon } from '../../lib/icons';
import { Who } from '../../components/Badge';
import { fdate, nf, pc } from '../../lib/helpers';
import { OWNER_FIELDS, ownerOf } from '../../lib/owners';
import * as bookingsApi from '../../api/bookings';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';

export default function EventDrawer({ event: ev, onClose, onEdit }) {
  const { can } = useSession();
  const nav = useNavigate();
  const [tab, setTab] = useState('overview');
  const { data: allBookings } = useFetch(bookingsApi.list, [], { initialData: [] });
  if (!ev) return null;
  const bk = (allBookings || []).filter((b) => b.event_code === ev.event_code);
  const paid = bk.filter((b) => b.payment_status === 'Paid').length;
  const pend = bk.filter((b) => b.payment_status === 'Pending').length;
  const eds = [{ year: ev.year || new Date(ev.event_date).getFullYear(), seats: bk.length, paid, growth: 0, current: true }];
  const mx = Math.max(1, ...eds.map((e) => e.seats));

  return (
    <Drawer
      wide onClose={onClose}
      head={
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3, flexWrap: 'wrap' }}>
            <span className="mono" style={{ color: 'var(--t-600)' }}>{ev.event_code}</span>
          </div>
          <h2>{ev.name}</h2><p>{ev.location} · {fdate(ev.event_date)} → {fdate(ev.end_date)}</p>
        </div>
      }
      tabs={<Tabs list={[{ id: 'overview', label: 'Overview' }, { id: 'editions', label: 'Edition history' }, { id: 'teams', label: 'Teams' }]} active={tab} onPick={setTab} />}
      foot={<>
        <button className="btn btn-s" onClick={onClose}>Close</button>
        <button className="btn btn-s" onClick={() => { onClose(); nav('/bookings'); }}><Icon name="receipt" size={15} />View bookings</button>
        {can('update', 'events') ? <button className="btn btn-p" onClick={() => { onClose(); onEdit(ev); }}><Icon name="edit" size={15} />Edit event</button> : null}
      </>}
    >
      {tab === 'overview' && (
        <>
          <div className="sl">Schedule</div>
          <div className="ro">
            <div className="ro-c"><div className="ro-l">Starts</div><div className="ro-v">{fdate(ev.event_date)}</div></div>
            <div className="ro-c"><div className="ro-l">Ends</div><div className="ro-v">{fdate(ev.end_date)}</div></div>
            <div className="ro-c"><div className="ro-l">Base code</div><div className="ro-v mono">{ev.base_code || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Year</div><div className="ro-v">{ev.year || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Event type</div><div className="ro-v">{ev.event_type}</div></div>
            <div className="ro-c"><div className="ro-l">Website live</div><div className="ro-v">{fdate(ev.website_live_date)}</div></div>
            <div className="ro-c"><div className="ro-l">Sales check</div><div className="ro-v">{ev.sales_check}</div></div>
            <div className="ro-c"><div className="ro-l">Nearest related</div><div className="ro-v mono">{ev.nearest_related}</div></div>
            <div className="ro-c"><div className="ro-l">Website</div><div className="ro-v mono">{ev.website || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Web bookings enabled</div><div className="ro-v">{ev.web_bookings_enabled || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">VR1 sent status</div><div className="ro-v">{ev.vr1_status || '—'}</div></div>
          </div>
          <div className="sl">Current edition</div>
          <div className="ms">
            <div><div className="l">Bookings</div><div className="v">{nf(bk.length)}</div></div>
            <div><div className="l">Paid</div><div className="v g">{nf(paid)}</div></div>
            <div><div className="l">Pending</div><div className="v a">{nf(pend)}</div></div>
          </div>
        </>
      )}
      {tab === 'editions' && (
        <>
          <div className="sl">Edition growth</div>
          <div className="bc" style={{ height: 130 }}>
            {eds.map((e) => {
              const H = 105, p = Math.round((e.paid / mx) * H), r = Math.round(((e.seats - e.paid) / mx) * H);
              return (
                <div className="bc-g" key={e.year}>
                  <div className="bc-s" style={{ height: p + r }}>
                    <i style={{ height: r, background: 'var(--n-200)' }} />
                    <i style={{ height: p, background: e.current ? 'var(--t-500)' : 'var(--green)' }} />
                    <span className="bc-t"><b>{e.year}</b> · {nf(e.seats)} seats<br />Paid {nf(e.paid)} · growth {e.growth >= 0 ? '+' : ''}{e.growth}%</span>
                  </div>
                  <div className="bc-l">{e.year}</div>
                </div>
              );
            })}
          </div>
          <div className="bar-lg" style={{ marginBottom: 16 }}><span><em style={{ background: 'var(--green)' }} />Paid</span><span><em style={{ background: 'var(--n-200)' }} />Unpaid / free</span><span><em style={{ background: 'var(--t-500)' }} />Current edition</span></div>
          <table className="gt" style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', overflow: 'hidden' }}>
            <thead><tr><th>Edition</th><th className="num">Seats</th><th className="num">Paid</th><th className="num">Conversion</th><th className="num">Growth</th></tr></thead>
            <tbody>
              {eds.slice().reverse().map((e) => (
                <tr key={e.year}>
                  <td style={{ fontWeight: 650, color: 'var(--text)' }}>{e.year}{e.current ? <span className="tg bg-teal"> current</span> : null}</td>
                  <td className="num">{nf(e.seats)}</td>
                  <td className="num" style={{ color: 'var(--green)', fontWeight: 650 }}>{nf(e.paid)}</td>
                  <td className="num">{pc(e.paid, e.seats)}%</td>
                  <td className="num" style={{ fontWeight: 700, color: e.growth >= 0 ? 'var(--green)' : 'var(--red)' }}>{e.growth >= 0 ? '↑ ' : '↓ '}{Math.abs(e.growth)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {tab === 'teams' && (
        <>
          <div className="sl">Team ownership</div>
          {/* One list, shared with the Events table and both event forms — see
              lib/owners.js. Six of these seven columns are blank on every event in
              the live data, so each row that has no value of its own falls back to
              the lead of the team that owns the role and says so underneath. The
              attribution is not decoration: an inherited name follows the team and
              will change when the team's lead changes, and reading it as the event's
              own answer is the mistake worth preventing.

              ownerOf coalesces. A dropped backend column arrives here as
              `undefined`, and calling .indexOf on it threw during render; with no
              error boundary in the app that white-screened the whole page rather
              than blanking one row, which is what `speaker_sales_team` did after
              events migration 0017 removed it. */}
          {OWNER_FIELDS.map(({ key, label }) => {
            const o = ownerOf(ev, key);
            return (
              <div key={key} style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '10px 0', borderBottom: '1px solid var(--n-50)' }}>
                <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--text-4)', width: 150, flexShrink: 0, paddingTop: 4 }}>{label}</span>
                {!o.names.length ? <span className="dim">—</span>
                  : o.name.indexOf('Team') > -1 ? <span style={{ fontWeight: 650, color: 'var(--text)', fontSize: 12.5 }}>{o.name}</span>
                  : (
                    /* One entry per lead, not a joined string: a team may have any
                       number of leads and each is a person with their own avatar.
                       Sales Team has two, and showing only the first would look
                       like a complete answer. */
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                      {o.names.map((n) => (
                        <Who key={n} name={n} sub={o.inherited ? `lead of ${o.team}` : undefined} />
                      ))}
                    </div>
                  )}
              </div>
            );
          })}
          <div className="sl" style={{ marginTop: 18 }}>Naming &amp; metadata</div>
          <div className="ro">
            <div className="ro-c"><div className="ro-l">Name for email marketing</div><div className="ro-v">{ev.email_marketing_name || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Name for branding</div><div className="ro-v">{ev.branding_name || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Annualisation</div><div className="ro-v">{ev.annualisation || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Date format</div><div className="ro-v mono">{ev.date_format || '—'}</div></div>
          </div>
          <div className="sl" style={{ marginTop: 18 }}>Related &amp; upcoming events</div>
          <div className="ro">
            <div className="ro-c"><div className="ro-l">Related event 1</div><div className="ro-v">{ev.related_event_1 || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Related event 2</div><div className="ro-v">{ev.related_event_2 || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Related event 3</div><div className="ro-v">{ev.related_event_3 || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Upcoming event 1</div><div className="ro-v">{ev.upcoming_event_1 || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Upcoming event 2</div><div className="ro-v">{ev.upcoming_event_2 || '—'}</div></div>
            <div className="ro-c"><div className="ro-l">Upcoming event 3</div><div className="ro-v">{ev.upcoming_event_3 || '—'}</div></div>
          </div>
        </>
      )}
    </Drawer>
  );
}
