import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { Donut, Sparkline } from '../components/UI';
import { Who, RoleBadge, EvBadge } from '../components/Badge';
import { nf, pc, plur, rel, MON } from '../lib/helpers';
import { ROLE_FULL, ALL_MODULES } from '../lib/constants';
import * as eventsApi from '../api/events';
import * as bookingsApi from '../api/bookings';
import * as ticketsApi from '../api/tickets';
import * as webhooksApi from '../api/webhooks';
import * as reportsApi from '../api/reports';
import { useFetch } from '../hooks/useFetch';
import { useSession } from '../context/SessionContext';
import NewBookingModal from './bookings/NewBookingModal';
import NewTicketModal from './tickets/NewTicketModal';
import ImportWizard from '../components/ImportWizard';

const EMPTY_STATS = {
  all: { total: 0, paid: 0, pending: 0, free: 0, credit: 0, unpaid: 0, cancelled: 0 },
  sales: { total: 0, paid: 0, pending: 0, free: 0, credit: 0, unpaid: 0, cancelled: 0 },
  spex: { total: 0, paid: 0, pending: 0, free: 0, credit: 0, unpaid: 0, cancelled: 0 },
  speaker: { total: 0, paid: 0, pending: 0, free: 0, credit: 0, unpaid: 0, cancelled: 0 },
  months: [], channels: [], booking_team_productivity: [], team_productivity: [], tickets: {}, whFailed: 0, year: 0, delta: 0,
};

// Declared at module scope, not inline: useFetch memoises on its deps array, so
// an inline arrow would still be captured once, but a named module-level function
// makes the stability explicit and keeps the row budget visible in one place.
const dashRecentBookings = () => bookingsApi.recent(3);
const dashRecentTickets = () => ticketsApi.recentCompleted(2);
const dashRecentWhLogs = () => webhooksApi.recentLogs(2);

export default function DashboardPage() {
  const { user, perms, canView, can } = useSession();
  const nav = useNavigate();
  const [openTeams, setOpenTeams] = useState(new Set());
  const [newBookingOpen, setNewBookingOpen] = useState(false);
  const [newTicketOpen, setNewTicketOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  // Every fetch on this page is either a bounded page or an aggregate. It used to
  // hold three fetchAllPages walks (delegates 13,269 / tickets 35,690 / webhook
  // logs 130,287) plus a `dashboard()` that internally walked delegates and
  // webhook logs AGAIN — measured at 161 API requests in the first 10 seconds and
  // still climbing, roughly 700 in total. That is what the "backend running in a
  // loop" report was: a finite page-walk, not a render loop.
  const { data: events } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: recentBookings } = useFetch(dashRecentBookings, [], { initialData: [] });
  const { data: oldestPending } = useFetch(bookingsApi.oldestPending, [], { initialData: null });
  const { data: recentTickets } = useFetch(dashRecentTickets, [], { initialData: [] });
  const { data: recentWhLogs } = useFetch(dashRecentWhLogs, [], { initialData: [] });
  const { data: syncLogs } = useFetch(reportsApi.syncLogs, [], { initialData: [] });
  const { data: sheets } = useFetch(reportsApi.sheets, [], { initialData: [] });
  const { data: stats } = useFetch(reportsApi.dashboard, [], { initialData: EMPTY_STATS });

  const EVENTS = events || [];
  const RECENT_BOOKINGS = recentBookings || [];
  const RECENT_TICKETS = recentTickets || [];
  const WH_LOGS = recentWhLogs || [];
  const SYNC_LOGS = syncLogs || [];
  const SHEETS = sheets || [];
  const S = stats || EMPTY_STATS;

  const hr = new Date().getHours();
  const greet = hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
  const first = user.name.split(' ')[0];
  const today = new Date();
  const withOffset = EVENTS.map((e) => ({ ...e, offset: e.event_date ? Math.round((new Date(e.event_date) - today) / 864e5) : -1 }));
  const live = withOffset.filter((e) => e.status === 'Live');
  const soon = withOffset.filter((e) => e.offset > 0).sort((a, b) => a.offset - b.offset);
  const next7 = soon.filter((e) => e.offset <= 30);

  const acts = [];
  if (canView('bookings') && S.all.pending) {
    // oldestPending is a single server-sorted row and may not have arrived yet —
    // the count comes from a different request. Reading `.request_date` off an
    // undefined row here used to throw during render and blank the page.
    acts.push({
      ic: 'clock', tone: 'var(--amber)', t: 'Bookings awaiting payment',
      s: oldestPending ? 'Oldest is ' + rel(oldestPending.request_date) : 'Awaiting confirmation',
      n: S.all.pending, go: () => nav('/bookings/Pending'),
    });
  }
  if (canView('bookings') && S.all.unpaid) acts.push({ ic: 'warn', tone: 'var(--red)', t: 'Unpaid invoices past due', s: 'Needs a chase call or escalation', n: S.all.unpaid, go: () => nav('/bookings/Unpaid') });
  if (canView('ticket_central') && S.tickets.mr_submitted) acts.push({ ic: 'inbox', tone: 'var(--blue)', t: 'Tickets in the mining queue', s: 'Submitted by Market Research', n: S.tickets.mr_submitted, go: () => nav('/tickets/mr_submitted') });
  if (canView('ticket_central') && S.tickets.returned) acts.push({ ic: 'refresh', tone: 'var(--red)', t: 'Tickets returned to MR', s: 'Rejected by Data Mining — needs detail', n: S.tickets.returned, go: () => nav('/tickets/returned') });
  // whFailed is a server-side count (webhooks/logs/?status=failed, read for
  // `count`), not a filter over whatever logs happen to be loaded.
  if (canView('webhooks') && S.whFailed) acts.push({ ic: 'webhook', tone: 'var(--red)', t: 'Failed webhook deliveries', s: 'Website bookings may be missing', n: S.whFailed, go: () => nav('/webhooks') });
  if (canView('reports')) { const e = SHEETS.filter((s) => s.status === 'error').length; if (e) acts.push({ ic: 'sheet', tone: 'var(--amber)', t: 'Sheet sources erroring', s: 'Sync has not completed', n: e, go: () => nav('/reports/registry') }); }
  if (canView('events') && live.length) acts.push({ ic: 'zap', tone: 'var(--green)', t: 'Events live right now', s: live.slice(0, 2).map((e) => e.event_code).join(', ') + (live.length > 2 ? ' +' + (live.length - 2) : ''), n: live.length, go: () => nav('/events') });

  const lines = [
    { k: 'sales', n: 'Delegate Sales', tg: 'Sales', bgc: '--t-50', tc: '--t-700', d: S.sales },
    { k: 'spex', n: 'Sponsorship (SpEx)', tg: 'SpEx', bgc: '--cyan-bg', tc: '--cyan-tx', d: S.spex },
    { k: 'speaker', n: 'Speaker Sales', tg: 'Speaker', bgc: '--green-bg', tc: '--green-tx', d: S.speaker },
  ];

  const monthsMax = Math.max(1, ...S.months.map((x) => x.total));
  const mrTeams = (S.team_productivity || []).filter((t) => t.team_type === 'market_research' || t.team_type === 'data_mining');

  const feed = [];
  RECENT_BOOKINGS.slice(0, 3).forEach((b) => feed.push({ ic: 'receipt', t: <><b>{b.name}</b> booked onto <b>{b.event_code}</b></>, m: rel(b.request_date) + ' · ' + b.payment_status }));
  RECENT_TICKETS.slice(0, 2).forEach((t) => feed.push({ ic: 'ticket', t: <><b>{t.ticket_number}</b> completed by <b>{t.assign_name}</b></>, m: rel(t.complete_date) + ' · ' + nf(t.mined_count) + ' contacts mined' }));
  WH_LOGS.slice(0, 2).forEach((w) => feed.push({ ic: 'webhook', t: <>Webhook <b>{w.api_key_name}</b> — {w.status}</>, m: rel(w.received_at) + ' · ' + plur(w.records, 'record') }));
  SYNC_LOGS.slice(0, 2).forEach((s) => feed.push({ ic: 'sheet', t: <><b>{s.source}</b> sync {s.status}</>, m: rel(s.started_at) + ' · ' + nf(s.rows_written) + ' rows' }));

  function toggleTeam(id) { setOpenTeams((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }

  return (
    <>
      <div className="hero">
        <div className="hero-r">
          <div>
            <h2>{greet}, {first}</h2>
            <p>{ROLE_FULL[user.role]} · {perms.is_all_access ? 'full access' : plur(ALL_MODULES.filter(canView).length, 'module') + ' available'} · Signed in as <b style={{ color: '#fff' }}>{user.username}</b></p>
          </div>
          <div className="hero-st">
            {canView('bookings') ? <div><div className="l">Pending</div><div className="v">{nf(S.all.pending)}<small>bookings</small></div></div> : null}
            {canView('ticket_central') ? <div><div className="l">Mining queue</div><div className="v">{nf(S.tickets.mr_submitted)}<small>tickets</small></div></div> : null}
            {canView('events') ? <div><div className="l">Live now</div><div className="v">{nf(live.length)}<small>events</small></div></div> : null}
            <div><div className="l">Next 30 days</div><div className="v">{nf(next7.length)}<small>events</small></div></div>
          </div>
        </div>
        <div className="qa">
          {can('create', 'bookings') ? <button onClick={() => setNewBookingOpen(true)}><Icon name="plus" size={14} />New booking</button> : null}
          {can('create', 'ticket_central') ? <button onClick={() => setNewTicketOpen(true)}><Icon name="plus" size={14} />New ticket</button> : null}
          {canView('bookings') ? <button onClick={() => nav('/bookings/Pending')}><Icon name="clock" size={14} />Review pending</button> : null}
          {can('create', 'bookings') ? <button onClick={() => setImportOpen(true)}><Icon name="download" size={14} />Import data</button> : null}
          <button onClick={() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}><Icon name="target" size={14} />Search anything</button>
        </div>
      </div>

      <div className="dg w21">
        <div className="card">
          <div className="card-h">
            <div><div className="card-t">Needs your attention</div><div className="card-s">Ranked by urgency across every module you can reach</div></div>
            <span className={'bg bg-' + (acts.length ? 'amber' : 'green')}><i />{acts.length ? acts.length + ' open' : 'all clear'}</span>
          </div>
          {acts.length ? (
            <div className="aq">
              {acts.map((a, i) => (
                <div className="aq-i" key={i} onClick={a.go}>
                  <span className="aq-d" style={{ background: a.tone + '14', color: a.tone }}><Icon name={a.ic} size={15} /></span>
                  <span className="aq-b"><span className="t">{a.t}</span><span className="s">{a.s}</span></span>
                  <span className="aq-n" style={{ color: a.tone }}>{nf(a.n)}</span>
                  <span className="aq-go"><Icon name="chevR" size={14} /></span>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt" style={{ padding: '30px 16px' }}>
              <div className="mt-i" style={{ background: 'var(--green-bg)', color: 'var(--green)' }}><Icon name="check" size={21} /></div>
              <h3>Nothing needs you</h3><p>No pending payments, queued tickets or failed syncs in your modules.</p>
            </div>
          )}
        </div>
        {canView('bookings') ? (
          <div className="card">
            <div className="card-h"><div><div className="card-t">Payment mix</div><div className="card-s">All {nf(S.all.total)} booking records</div></div></div>
            <div className="dn">
              <div className="dn-c">
                <Donut segs={[{ v: S.all.paid, c: 'var(--green)' }, { v: S.all.pending, c: 'var(--amber)' }, { v: S.all.free, c: 'var(--blue)' }, { v: S.all.credit, c: 'var(--violet)' }, { v: S.all.unpaid + S.all.cancelled, c: 'var(--red)' }]} />
                <div className="dn-m"><div className="v">{pc(S.all.paid, S.all.total)}%</div><div className="l">Paid</div></div>
              </div>
              <div className="dn-l">
                <div className="r"><em style={{ background: 'var(--green)' }} /><span className="n">Paid</span><span className="v">{nf(S.all.paid)}</span></div>
                <div className="r"><em style={{ background: 'var(--amber)' }} /><span className="n">Pending</span><span className="v">{nf(S.all.pending)}</span></div>
                <div className="r"><em style={{ background: 'var(--blue)' }} /><span className="n">Free</span><span className="v">{nf(S.all.free)}</span></div>
                <div className="r"><em style={{ background: 'var(--violet)' }} /><span className="n">Credit</span><span className="v">{nf(S.all.credit)}</span></div>
                <div className="r"><em style={{ background: 'var(--red)' }} /><span className="n">Unpaid / cancelled</span><span className="v">{nf(S.all.unpaid + S.all.cancelled)}</span></div>
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {canView('bookings') ? (
        <div className="card" style={{ marginBottom: 11 }}>
          <div className="card-h">
            <div><div className="card-t">Three pipelines</div><div className="card-s">Delegates, sponsorship and speakers tracked separately</div></div>
            <button className="btn btn-s btn-sm" onClick={() => nav('/bookings/Pending')}><Icon name="arrowR" size={13} /> Open bookings</button>
          </div>
          <div className="pl">
            {lines.map((L) => {
              const d = L.d, t = d.total || 1;
              return (
                <div className="pl-i" key={L.k}>
                  <div className="h"><span className="n">{L.n}<span className="tg" style={{ background: `var(${L.bgc})`, color: `var(${L.tc})` }}>{L.tg}</span></span><span className="v"><b>{nf(d.total)}</b> records · {pc(d.paid, t)}% paid</span></div>
                  <div className="bar">
                    <i style={{ width: (d.paid / t) * 100 + '%', background: 'var(--green)' }} />
                    <i style={{ width: (d.pending / t) * 100 + '%', background: 'var(--amber)' }} />
                    <i style={{ width: (d.free / t) * 100 + '%', background: 'var(--blue)' }} />
                    <i style={{ width: (d.credit / t) * 100 + '%', background: 'var(--violet)' }} />
                    <i style={{ width: ((d.unpaid + d.cancelled) / t) * 100 + '%', background: 'var(--red)' }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="bar-lg"><span><em style={{ background: 'var(--green)' }} />Paid</span><span><em style={{ background: 'var(--amber)' }} />Pending</span><span><em style={{ background: 'var(--blue)' }} />Free</span><span><em style={{ background: 'var(--violet)' }} />Credit</span><span><em style={{ background: 'var(--red)' }} />Unpaid / cancelled</span></div>
        </div>
      ) : null}

      <div className="dg w21">
        {canView('reports') ? (
          <div className="card">
            <div className="card-h">
              <div><div className="card-t">Bookings by month</div><div className="card-s">Paid, pending and complimentary · 2026 · H2 {S.delta >= 0 ? '+' : ''}{S.delta}% vs H1</div></div>
              <span className="bg bg-teal"><i />{nf(S.year)} total</span>
            </div>
            <div className="bc">
              {S.months.map((mo) => {
                const H = 132, a = Math.round((mo.paid / monthsMax) * H), b = Math.round((mo.pending / monthsMax) * H), c = Math.round((mo.free / monthsMax) * H);
                return (
                  <div className="bc-g" key={mo.label}>
                    <div className="bc-s" style={{ height: a + b + c }}>
                      <i style={{ height: c, background: 'var(--blue)' }} />
                      <i style={{ height: b, background: 'var(--amber)' }} />
                      <i style={{ height: a, background: 'var(--green)' }} />
                      <span className="bc-t"><b>{mo.label}</b> · {nf(mo.total)} total<br />Paid {nf(mo.paid)} · Pending {nf(mo.pending)} · Free {nf(mo.free)}</span>
                    </div>
                    <div className="bc-l">{mo.label}</div>
                  </div>
                );
              })}
            </div>
            <div className="bar-lg" style={{ marginTop: 11 }}><span><em style={{ background: 'var(--green)' }} />Paid</span><span><em style={{ background: 'var(--amber)' }} />Pending</span><span><em style={{ background: 'var(--blue)' }} />Free</span></div>
          </div>
        ) : null}
        {canView('events') ? (
          <div className="card">
            <div className="card-h">
              <div><div className="card-t">Coming up</div><div className="card-s">Next events by start date</div></div>
              <button className="btn btn-g btn-sm" onClick={() => nav('/events')}>All <Icon name="chevR" size={13} /></button>
            </div>
            <div className="ev-s">
              {soon.slice(0, 7).map((e) => {
                const d = new Date(e.event_date);
                return (
                  <div className="ev-r" key={e.id} onClick={() => nav('/events')}>
                    <span className="ev-dt"><span className="d">{d.getDate()}</span><span className="m">{MON[d.getMonth()]}</span></span>
                    <span className="ev-b"><span className="n">{e.name}</span><span className="s"><span className="mono" style={{ color: 'var(--t-600)' }}>{e.event_code}</span>· {e.location}</span></span>
                    <EvBadge value={e.status} />
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>

      {canView('reports') ? (
        <>
          <div className="sl">Team productivity — booking pipelines</div>
          {S.booking_team_productivity.map((t) => {
            const lab = t.team_type === 'spex' ? ['Sponsors booked', 'Sponsors paid'] : t.team_type === 'speaker_sales' ? ['Speakers', 'Speakers paid'] : ['Bookings', 'Paid'];
            const tag = t.team_type === 'spex' ? 'SpEx' : t.team_type === 'speaker_sales' ? 'Speaker Sales' : ROLE_FULL[t.team_type] || 'Sales';
            const open = openTeams.has(t.team_id);
            return (
              <div className="ac" key={t.team_id}>
                <div className="ac-h" onClick={() => toggleTeam(t.team_id)}>
                  <span className="ac-i" style={{ background: t.color + '14', color: t.color }}><Icon name="team" size={15} /></span>
                  <span className="ac-t"><span className="n">{t.team_name}<span className="tg bg-neutral">{tag}</span></span><span className="s">{plur(t.members.length, 'member')}{t.members.length ? ' · ' + t.conv + '% conversion' : ' · no one assigned'}</span></span>
                  {t.members.length ? <Sparkline v={t.trend} w={56} h={20} /> : null}
                  <span className="ac-st" style={{ marginLeft: 13 }}><span className="l">{lab[0]}</span><span className="v">{nf(t.bookings)}</span></span>
                  <span className={'ac-a' + (open ? ' op' : '')}><Icon name="chevD" size={16} /></span>
                </div>
                <div className={'ac-b' + (open ? ' op' : '')}>
                  {!t.members.length ? <div className="mt" style={{ padding: 24 }}><p style={{ margin: 0 }}>No members assigned to this team yet.</p></div> : (
                    <table className="gt">
                      <thead><tr><th>Member</th><th>Role</th><th className="num">{lab[0]}</th><th className="num">{lab[1]}</th><th className="num">Conversion</th></tr></thead>
                      <tbody>
                        {t.members.slice().sort((a, b) => b.bookings - a.bookings).map((mb) => {
                          const tn = mb.conv >= 70 ? 'var(--green)' : mb.conv >= 50 ? 'var(--t-500)' : 'var(--amber)';
                          return (
                            <tr key={mb.user_id}>
                              <td><Who name={mb.name} sub={mb.is_lead ? 'Team lead' : ''} /></td>
                              <td><RoleBadge value={mb.role} /></td>
                              <td className="num" style={{ fontWeight: 650, color: 'var(--text)' }}>{nf(mb.bookings)}</td>
                              <td className="num" style={{ color: 'var(--green)', fontWeight: 650 }}>{nf(mb.paid)}</td>
                              <td className="num"><span className="cv"><span>{mb.conv}%</span><span className="cv-b"><i style={{ width: mb.conv + '%', background: tn }} /></span></span></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            );
          })}
          {canView('ticket_central') ? (
            <>
              <div className="sl" style={{ marginTop: 16 }}>Team productivity — ticket pipeline</div>
              <div className="dg c2">
                {mrTeams.map((t) => {
                  // From /api/stats/dashboard_aggregate/ — previously computed by
                  // walking all 35,690 tickets in the browser and matching on
                  // display name.
                  const mined = t.team_type === 'data_mining' ? (t.mined || 0) : (t.raised || 0);
                  const lbl = t.team_type === 'data_mining' ? 'Contacts mined' : 'Tickets raised';
                  return (
                    <div className="card" key={t.team_id}>
                      <div className="card-h">
                        <div><div className="card-t">{t.team_name}</div><div className="card-s">{plur(t.members.length, 'member')}</div></div>
                        <span className="kpi-v" style={{ fontSize: 20 }}>{nf(mined)}<small style={{ fontSize: 10.5, marginLeft: 5 }}>{lbl}</small></span>
                      </div>
                      <div className="av-stk">{t.members.map((m) => <Who key={m.user_id} name={m.name} size="sm" />)}</div>
                    </div>
                  );
                })}
              </div>
            </>
          ) : null}
        </>
      ) : null}

      <div className="dg c2" style={{ marginTop: 18 }}>
        {canView('reports') ? (
          <div className="card">
            <div className="card-h"><div><div className="card-t">Where bookings come from</div><div className="card-s">Share of total intake</div></div></div>
            {S.channels.map((c) => (
              <div className="shr" key={c.n}><span className="l">{c.n}</span><span className="t"><i style={{ width: c.p + '%' }} /></span><span className="p">{c.p}%</span></div>
            ))}
          </div>
        ) : null}
        <div className="card">
          <div className="card-h"><div><div className="card-t">Recent activity</div><div className="card-s">Across bookings, tickets and integrations</div></div></div>
          <div className="af">
            {feed.slice(0, 8).map((f, i) => (
              <div className="af-i" key={i}><span className="af-d"><Icon name={f.ic} size={10} /></span><span className="af-b"><span className="t">{f.t}</span><span className="m">{f.m}</span></span></div>
            ))}
          </div>
        </div>
      </div>

      {newBookingOpen ? <NewBookingModal onClose={() => setNewBookingOpen(false)} /> : null}
      {newTicketOpen ? <NewTicketModal onClose={() => setNewTicketOpen(false)} /> : null}
      {importOpen ? <ImportWizard kind="bookings" onClose={() => setImportOpen(false)} /> : null}
    </>
  );
}
