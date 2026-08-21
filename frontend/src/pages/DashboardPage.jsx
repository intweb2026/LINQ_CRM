import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { Donut, Sparkline } from '../components/UI';
import { Who, RoleBadge, EvBadge } from '../components/Badge';
import { nf, pc, plur, rel, MON } from '../lib/helpers';
import { ROLE_FULL, ALL_MODULES } from '../lib/constants';
import { DASH_MODULES } from '../lib/nav';
import * as eventsApi from '../api/events';
import * as bookingsApi from '../api/bookings';
import * as ticketsApi from '../api/tickets';
import * as webhooksApi from '../api/webhooks';
import * as statsApi from '../api/stats';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import NewBookingModal from './bookings/NewBookingModal';
import TicketFormModal from './tickets/TicketFormModal';
import ImportWizard from '../components/ImportWizard';
import NoAccessPage from './NoAccessPage';

const EMPTY_LINE = { total: 0, paid: 0, pending: 0, free: 0, credit: 0, unpaid: 0, cancelled: 0, invoices: 0, companies: 0 };
const EMPTY_STATS = {
  all: EMPTY_LINE, sales: EMPTY_LINE, spex: EMPTY_LINE, speaker: EMPTY_LINE,
  months: [], channels: [], booking_team_productivity: [], team_productivity: [], tickets: {}, whFailed: 0, year: 0, delta: 0,
  period: {}, attribution: {},
};

// Per-team headline. The pipeline figure is PIPELINE-wide, not team-wide (two
// teams can work one pipeline — Sales and Telemarketing both sell delegate
// seats), so it is labelled as such rather than presented as this team's total.
// SpEx is measured in COMPANIES: sponsorship is sold to an organisation, and the
// delegate rows on a SpEx invoice are the passes bundled with the package.
function pipelineStat(t) {
  if (t.team_type === 'spex') return { label: 'Sponsors', value: t.pipeline_companies };
  if (t.team_type === 'speaker_sales') return { label: 'Speakers', value: t.pipeline_total };
  return { label: 'In pipeline', value: t.pipeline_total };
}

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
  // Fixed at 'all' now — the Date Range control was removed from this page
  // (kept on Bookings only), so there is no UI left to change it.
  const period = 'all';

  // Every fetch on this page is either a bounded page or an aggregate. It used to
  // hold three fetchAllPages walks (delegates 13,269 / tickets 35,690 / webhook
  // logs 130,287) plus a `dashboard()` that internally walked delegates and
  // webhook logs AGAIN — measured at 161 API requests in the first 10 seconds and
  // still climbing, roughly 700 in total. That is what the "backend running in a
  // loop" report was: a finite page-walk, not a render loop.
  const { data: events, refetchQuiet: reloadEvents } = useFetch(eventsApi.list, [], { initialData: [] });
  const { data: recentBookings, refetchQuiet: reloadBookings } = useFetch(dashRecentBookings, [], { initialData: [] });
  const { data: oldestPending, refetchQuiet: reloadOldest } = useFetch(bookingsApi.oldestPending, [], { initialData: null });
  const { data: recentTickets, refetchQuiet: reloadTickets } = useFetch(dashRecentTickets, [], { initialData: [] });
  const { data: recentWhLogs, refetchQuiet: reloadWhLogs } = useFetch(dashRecentWhLogs, [], { initialData: [] });
  // The only period-sensitive fetch on the page. useFetch memoises `run` on the
  // deps array, so [period] is what makes changing the range refetch — and the
  // ONLY thing that does. An inline arrow with an empty deps array would capture
  // the first period forever and the buttons would visibly do nothing.
  const fetchStats = useCallback(() => statsApi.dashboard(period), [period]);
  const { data: stats, refetchQuiet: reloadStats } = useFetch(fetchStats, [period], { initialData: EMPTY_STATS });

  /**
   * The whole page, on any write.
   *
   * Nothing here is a list this user edits in place — every number is an
   * aggregate over tables owned by other pages, plus the webhook delivery feed,
   * which is written by machines and not by anyone's browser. So the subscription
   * is unfiltered (`resources: null`): a booking, a ticket, an event or a
   * delivery all move something on this screen.
   *
   * Polled at a minute rather than the default thirty seconds. This is seven
   * requests, one of them a full aggregate over the delegate table, and a
   * dashboard left open on a wall display would otherwise run that all day.
   */
  const { refreshNow: refresh } = useLiveData(
    useCallback(() => {
      reloadStats();
      reloadEvents();
      reloadBookings();
      reloadOldest();
      reloadTickets();
      reloadWhLogs();
    }, [reloadStats, reloadEvents, reloadBookings, reloadOldest, reloadTickets, reloadWhLogs]),
    {
      // Was `resources: null` — every write anywhere in the CRM triggered this
      // whole callback. A paper review save, a proposal edit, a company merge, a
      // user or team change: none of them move a number on this screen, but each
      // one cost a full aggregate over the delegate table for every open
      // dashboard, plus eight other requests.
      //
      // Scoped to the resources this page actually READS. Not the three that
      // feed the aggregate alone: the callback above also reloads the recent
      // tickets and webhook delivery panels, so narrowing to
      // delegates/invoices/events would have left those two stale until the
      // poll. The excluded set is still most of the app — paper-review,
      // proposals, companies, users, teams, google_sync, import, search.
      //
      // Paths are what api/client.js announceWrite() publishes: normalisePath()
      // strips the /api/ prefix and the query string, and pathTouches() matches
      // by prefix in both directions, so 'delegates' catches 'delegates/1234'
      // and 'webhooks' catches both 'webhooks/logs/9/retry' and 'webhooks/keys'.
      resources: ['delegates', 'invoices', 'events', 'tickets', 'webhooks'],
      // Matched to the backend cache TTL. stats.dashboard() is cached for 120s
      // (config/views.py), so polling faster than that only ever hits the cache —
      // cheap, but seven requests' worth of cheap, per open tab, forever.
      poll: 120_000,
    },
  );

  // Every panel below is an aggregate over another module's tables, and each one
  // is already hidden by its own canView() check. Holding none of DASH_MODULES
  // therefore rendered a page of nothing but the greeting — so this says so
  // instead. The rail and the command palette hide the entry on the same list
  // (see lib/nav.js), which leaves this guard for a typed URL.
  //
  // Below the hooks on purpose: an early return above them would change the hook
  // count between renders as the permission matrix resolves.
  if (!DASH_MODULES.some((m) => canView(m))) return <NoAccessPage module="Dashboard" />;

  const EVENTS = events || [];
  const RECENT_BOOKINGS = recentBookings || [];
  const RECENT_TICKETS = recentTickets || [];
  const WH_LOGS = recentWhLogs || [];
  const S = stats || EMPTY_STATS;
  // `period` is fixed at 'all' now (see above), so this is always 'all time' —
  // kept as a named value rather than inlined below because the several card
  // captions that read it are clearer for saying what it is than for saying
  // where it comes from.
  const RANGE = 'all time';

  const hr = new Date().getHours();
  const greet = hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
  const first = user.name.split(' ')[0];
  const today = new Date();
  const withOffset = EVENTS.map((e) => ({ ...e, offset: e.event_date ? Math.round((new Date(e.event_date) - today) / 864e5) : -1 }));
  const live = withOffset.filter((e) => e.status === 'Live');
  const soon = withOffset.filter((e) => e.offset > 0).sort((a, b) => a.offset - b.offset);
  const next7 = soon.filter((e) => e.offset <= 30);

  // OUT is the all-time payment mix, not the windowed one. This queue is a
  // worklist: under "Last 7 days" the windowed figure would report 0 unpaid
  // invoices while a backlog of them sits in the table, and a filter that hides
  // a backlog is worse than no filter. The analytic cards below use the windowed
  // `S.all`; these two rows deliberately do not.
  const OUT = S.outstanding || S.all;
  const acts = [];
  if (canView('bookings') && OUT.pending) {
    // oldestPending is a single server-sorted row and may not have arrived yet —
    // the count comes from a different request. Reading `.request_date` off an
    // undefined row here used to throw during render and blank the page.
    acts.push({
      ic: 'clock', tone: 'var(--amber)', t: 'Bookings awaiting payment',
      s: oldestPending ? 'Oldest is ' + rel(oldestPending.request_date) : 'Awaiting confirmation',
      n: OUT.pending, go: () => nav('/bookings/Pending'),
    });
  }
  if (canView('bookings') && OUT.unpaid) acts.push({ ic: 'warn', tone: 'var(--red)', t: 'Unpaid invoices past due', s: 'Needs a chase call or escalation', n: OUT.unpaid, go: () => nav('/bookings/Unpaid') });
  if (canView('ticket_central') && S.tickets.mr_submitted) acts.push({ ic: 'inbox', tone: 'var(--blue)', t: 'Tickets in the mining queue', s: 'Submitted by Market Research', n: S.tickets.mr_submitted, go: () => nav('/tickets/mr_submitted') });
  if (canView('ticket_central') && S.tickets.returned) acts.push({ ic: 'refresh', tone: 'var(--red)', t: 'Tickets returned to MR', s: 'Rejected by Data Mining — needs detail', n: S.tickets.returned, go: () => nav('/tickets/returned') });
  // whFailed is a server-side count (webhooks/logs/?status=failed, read for
  // `count`), not a filter over whatever logs happen to be loaded.
  if (canView('webhooks') && S.whFailed) acts.push({ ic: 'webhook', tone: 'var(--red)', t: 'Failed webhook deliveries', s: 'Website bookings may be missing', n: S.whFailed, go: () => nav('/webhooks') });
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
            {canView('bookings') ? <div><div className="l">Pending</div><div className="v">{nf(OUT.pending)}<small>bookings</small></div></div> : null}
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
            <div><div className="card-t">Needs your attention</div><div className="card-s">Ranked by urgency across every module you can reach · not limited by the date range</div></div>
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
            <div className="card-h"><div><div className="card-t">Payment mix</div><div className="card-s">{nf(S.all.total)} booking records · {RANGE}</div></div></div>
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
            <div><div className="card-t">Three pipelines</div><div className="card-s">Split on the booking code — sponsorship, speakers and delegate sales counted separately · {RANGE}</div></div>
            <button className="btn btn-s btn-sm" onClick={() => nav('/bookings/Pending')}><Icon name="arrowR" size={13} /> Open bookings</button>
          </div>
          <div className="pl">
            {lines.map((L) => {
              const d = L.d, t = d.total || 1;
              return (
                <div className="pl-i" key={L.k}>
                  <div className="h"><span className="n">{L.n}<span className="tg" style={{ background: `var(${L.bgc})`, color: `var(${L.tc})` }}>{L.tg}</span></span><span className="v"><b>{nf(d.total)}</b> records · {nf(d.invoices)} invoices · {nf(d.companies)} companies · {pc(d.paid, t)}% paid</span></div>
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
        {canView('bookings') ? (
          <div className="card">
            <div className="card-h">
              {/* The H1→H2 swing is only meaningful across a full year; inside a
                  7-day window there is no second half to compare, so it is
                  dropped rather than rendered as a confident -100%. */}
              <div><div className="card-t">Bookings by month</div><div className="card-s">Paid, pending and complimentary · {RANGE}{period === 'all' ? ` · H2 ${S.delta >= 0 ? '+' : ''}${S.delta}% vs H1` : ''}</div></div>
              <span className="bg bg-teal"><i />{nf(period === 'all' ? S.year : S.all.total)} {period === 'all' ? 'this year' : 'total'}</span>
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

      {canView('bookings') ? (
        <>
          <div className="sl">Team productivity — booking pipelines<span style={{ fontWeight: 500, textTransform: 'none', letterSpacing: 0 }}>{RANGE}</span></div>
          {S.booking_team_productivity.map((t) => {
            const tag = t.team_type === 'spex' ? 'SpEx' : t.team_type === 'speaker_sales' ? 'Speaker Sales' : ROLE_FULL[t.team_type] || 'Sales';
            const open = openTeams.has(t.team_id);
            const ps = pipelineStat(t);
            // Three distinct states, because "0 bookings" and "nobody can be
            // told apart from the data" look identical on a card and mean very
            // different things. The third is what an empty Event ▸ SpEx Team
            // column produces, and reporting it as a flat 0 is what made the
            // SpEx and Speaker numbers look absent rather than unattributable.
            const sub = !t.members.length ? 'no one assigned'
              : t.bookings ? t.conv + '% conversion · ' + nf(t.paid) + ' paid'
              : t.attribution_available ? 'no bookings attributed in this range'
              : 'not attributed — ' + t.attribution_source + ' is empty on every event';
            return (
              <div className="ac" key={t.team_id}>
                <div className="ac-h" onClick={() => toggleTeam(t.team_id)}>
                  <span className="ac-i" style={{ background: t.color + '14', color: t.color }}><Icon name="team" size={15} /></span>
                  <span className="ac-t"><span className="n">{t.team_name}<span className="tg bg-neutral">{tag}</span></span><span className="s">{plur(t.members.length, 'member')} · {sub}</span></span>
                  {t.members.length ? <Sparkline v={t.trend} w={56} h={20} /> : null}
                  <span className="ac-st" style={{ marginLeft: 13 }}><span className="l">{ps.label}</span><span className="v">{nf(ps.value)}</span></span>
                  {t.attribution_available
                    ? <span className="ac-st" style={{ marginLeft: 13 }}><span className="l">Attributed</span><span className="v">{nf(t.bookings)}</span></span>
                    : <span className="ac-st" style={{ marginLeft: 13 }}><span className="l">Attributed</span><span className="v" style={{ color: 'var(--amber)' }}>—</span></span>}
                  <span className={'ac-a' + (open ? ' op' : '')}><Icon name="chevD" size={16} /></span>
                </div>
                <div className={'ac-b' + (open ? ' op' : '')}>
                  {!t.members.length ? <div className="mt" style={{ padding: 24 }}><p style={{ margin: 0 }}>No members assigned to this team yet.</p></div> : (
                    <>
                    {!t.attribution_available ? (
                      <div className="hint" style={{ margin: 12, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                        <span style={{ color: 'var(--amber)', flexShrink: 0, marginTop: 1 }}><Icon name="warn" size={14} /></span>
                        <span>
                          <b>{nf(t.pipeline_total)} records</b> sit in this pipeline for the selected range
                          {t.team_type === 'spex' ? <> across <b>{nf(t.pipeline_invoices)} invoices</b> and <b>{nf(t.pipeline_companies)} companies</b></> : null}
                          , but none of them name a member of this team. Bookings are attributed
                          through <b>{t.attribution_source}</b>, which is empty on every event in
                          the catalogue — so the split by person cannot be computed. Fill that
                          column in on the Events tab and these numbers populate themselves.
                        </span>
                      </div>
                    ) : null}
                    <table className="gt">
                      <thead><tr><th>Member</th><th>Role</th><th className="num">Bookings</th><th className="num">Paid</th><th className="num">Conversion</th></tr></thead>
                      <tbody>
                        {t.members.slice().sort((a, b) => b.bookings - a.bookings).map((mb) => {
                          const tn = mb.conv >= 70 ? 'var(--green)' : mb.conv >= 50 ? 'var(--t-500)' : 'var(--amber)';
                          return (
                            <tr key={mb.user_id}>
                              <td><Who name={mb.name} sub={mb.is_lead ? 'Team lead' : ''} avatar={false} /></td>
                              <td><RoleBadge value={mb.role} /></td>
                              <td className="num" style={{ fontWeight: 650, color: 'var(--text)' }}>{nf(mb.bookings)}</td>
                              <td className="num" style={{ color: 'var(--green)', fontWeight: 650 }}>{nf(mb.paid)}</td>
                              <td className="num"><span className="cv"><span>{mb.conv}%</span><span className="cv-b"><i style={{ width: mb.conv + '%', background: tn }} /></span></span></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    </>
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
        {canView('bookings') ? (
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

      {/* All three used to be mounted with onClose alone. A booking raised from
          the dashboard, a ticket raised from the dashboard, or a spreadsheet
          imported from the dashboard therefore changed nothing the user could
          see: same KPIs, same activity feed, same "awaiting payment" count, on a
          page whose entire job is to report those numbers. */}
      {newBookingOpen ? <NewBookingModal onClose={() => setNewBookingOpen(false)} onCreated={refresh} /> : null}
      {newTicketOpen ? <TicketFormModal onClose={() => setNewTicketOpen(false)} onSaved={refresh} /> : null}
      {importOpen ? <ImportWizard kind="bookings" onClose={() => setImportOpen(false)} onImported={refresh} /> : null}
    </>
  );
}
