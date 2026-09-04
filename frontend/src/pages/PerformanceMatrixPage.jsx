import { useCallback, useMemo, useState } from 'react';
import DataTable from '../components/DataTable';
import { Tabs } from '../components/UI';
import { MON, nf } from '../lib/helpers';
import { apiErrorMessage } from '../api/client';
import * as pmApi from '../api/performanceMatrix';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';

/**
 * PERFORMANCE MATRIX
 * ──────────────────
 * One row per event EDITION, answering "how is this conference selling, against
 * its benchmark and against the same point before its last edition", with the
 * research pipeline (unmined tickets, paper submissions) beside the money.
 *
 * Every figure is computed live on the server from Events, Bookings, Delegates,
 * Ticket Central and Paper Review (backend/performance_matrix/services.py);
 * nothing here derives a number. The one thing this page WRITES is the Verdict,
 * which is stored on the Event and paints the whole row in its colour.
 *
 * The table IS the page: no summary bar above it, so the rows start at the top
 * and the horizontal scrollbar sits at the foot of the visible box. The page
 * root (.gs-page, shared with Events) sets the module's own face, Google Sans Flex, and the type
 * ladder: block title, column header, data, each one step smaller. The first
 * four columns are frozen; the event NAME is not a column, it is the hover on
 * the code, along with every team owner, so the frozen block stays narrow.
 */

const TABS = [
  { id: pmApi.VIEWS.UPCOMING, label: 'Upcoming' },
  { id: pmApi.VIEWS.ALL, label: 'All editions' },
];

// The five age windows, in the order the momentum blocks show them. Keys match
// the server's bk_* / pay_* / pr_* fields.
const WINDOWS = [['today', 'Today'], ['d7', '0-7d'], ['d14', '8-14d'], ['d21', '15-21d'], ['d30', '30d']];

const PREV_TONE = { Fresh: 'teal', Repeat: 'green', Rescheduled: 'amber', Relaunch: 'slate' };
const PREV_HINT = {
  Fresh: 'No earlier edition of this family in the catalogue',
  Repeat: 'The previous edition ran',
  Rescheduled: 'The previous edition was Postponed or TBP; this is its new date',
  Relaunch: 'The previous edition was Cancelled and the family is back',
};

const GROUPS = [
  { key: 'ev', label: 'Event' },
  { key: 'ly', label: 'Previous edition' },
  { key: 'lv', label: 'Live position' },
  { key: 'bk', label: 'Bookings · by request date' },
  { key: 'py', label: 'Payments · by payment date' },
  { key: 'tk', label: 'Tickets · unmined' },
  { key: 'pr', label: 'Proposals · by submission date' },
  { key: 'vd', label: 'Verdict' },
  { key: 'meta', label: 'Details' },
];

const dim = () => <span className="dim">—</span>;
const zero = () => <span className="dim">0</span>;
const num = (v) => (v ? <b className="pm-n">{nf(v)}</b> : zero());

/** '13-14 Sep, 2027' from a start and end ISO date; the plain dates are calendar
 *  days, so they are read as written rather than shifted through a timezone. */
export function dateRange(start, end) {
  if (!start) return '—';
  const [y1, m1, d1] = start.split('-').map(Number);
  const [y2, m2, d2] = (end || start).split('-').map(Number);
  if (y1 === y2 && m1 === m2) {
    return d1 === d2 ? `${d1} ${MON[m1 - 1]}, ${y1}` : `${d1}-${d2} ${MON[m1 - 1]}, ${y1}`;
  }
  if (y1 === y2) return `${d1} ${MON[m1 - 1]} - ${d2} ${MON[m2 - 1]}, ${y1}`;
  return `${d1} ${MON[m1 - 1]}, ${y1} - ${d2} ${MON[m2 - 1]}, ${y2}`;
}

/** The hover on the event code: the name, then every team owner. */
const tipFor = (r) => [r.name, ...Object.entries(r.owners || {}).map(([k, v]) => `${k}: ${v}`)].join('\n');

/** Days to go, banded: a week out is red, a month amber, a quarter blue.
 *  A completed edition's row is sepia throughout and recolours this cell too. */
function Countdown({ days, label }) {
  if (days == null) return dim();
  if (days < 0) return <span className="tg bg-neutral">{label}</span>;
  const tone = days <= 7 ? 'red' : days <= 30 ? 'amber' : days <= 90 ? 'blue' : 'neutral';
  return <span className={'tg bg-' + tone}>{label}</span>;
}

function VerdictPill({ value }) {
  const v = value || 'Standby';
  return <span className={'pm-vd ' + pmApi.verdictClass(v)}>{v}</span>;
}

function buildCols(onVerdict, benchmark, ticketTypes) {
  const cols = [
    {
      key: 'event_code', label: 'Event', group: 'ev', pin: true, w: 126,
      cell: (v, r) => <span className="mono pm-code" title={tipFor(r)}>{v}</span>,
    },
    {
      key: 'start_date', label: 'Dates', type: 'date', group: 'ev', pin: true, w: 146,
      cell: (v, r) => dateRange(v, r.end_date),
    },
    {
      key: 'location', label: 'Location', group: 'ev', pin: true, w: 118,
      cell: (v) => (v ? <span className="pm-loc" title={v}>{v}</span> : dim()),
    },
    {
      key: 'days_left', label: 'Countdown', group: 'ev', num: true, pin: true, w: 92,
      cell: (v, r) => <Countdown days={v} label={r.countdown} />,
    },
    {
      key: 'prev_status', label: 'Last edition', group: 'ly', cls: 'sec', w: 112,
      opts: () => Object.keys(PREV_TONE),
      cell: (v, r) => (
        <span className={'bg bg-' + (PREV_TONE[v] || 'neutral')}
          title={(PREV_HINT[v] || '') + (r.prior_event_code ? ` (${r.prior_event_code})` : '')}>
          <i />{v}
        </span>
      ),
    },
    {
      key: 'live_prev_year', label: 'Live then', group: 'ly', num: true, w: 90,
      cell: (v, r) => (v == null ? dim() : (
        <span title="Live count the previous edition had with this many days to go">
          {nf(v)}
          {r.live_delta != null
            ? <span className={'pm-d ' + (r.live_delta >= 0 ? 'up' : 'down')}>{r.live_delta >= 0 ? '+' : ''}{nf(r.live_delta)}</span>
            : null}
        </span>
      )),
    },
    { key: 'live_count', label: 'Live', group: 'lv', num: true, cls: 'sec', w: 70, cell: num },
    { key: 'paid_heads', label: 'Paid', group: 'lv', num: true, w: 70, cell: num },
    {
      key: 'pending', label: 'Pending', group: 'lv', num: true, w: 80,
      cell: (v) => (v ? <b style={{ color: 'var(--red-tx)' }}>{nf(v)}</b> : zero()),
    },
    {
      key: 'expected', label: 'Expected', group: 'lv', num: true, w: 84,
      cell: (v) => (v ? <b style={{ color: 'var(--amber-tx)' }}>{nf(v)}</b> : zero()),
    },
    {
      key: 'shortfall', label: `Short of ${benchmark}`, group: 'lv', num: true, w: 92,
      cell: (v) => (v ? <span className="tg bg-red">{nf(v)}</span> : <span className="tg bg-green">Met</span>),
    },
  ];
  WINDOWS.forEach(([k, label], i) => cols.push({
    key: 'bk_' + k, label, group: 'bk', num: true, w: 72, cls: i === 0 ? 'sec' : '', cell: num,
  }));
  WINDOWS.forEach(([k, label], i) => cols.push({
    key: 'pay_' + k, label, group: 'py', num: true, w: 72, cls: i === 0 ? 'sec' : '', cell: num,
  }));
  // Tickets are a family's pile, shown once on its nearest upcoming edition; the
  // other editions of the family read a dash rather than a misleading zero.
  const tk = (v, r) => (r.tk_here ? num(v) : dim());
  cols.push(
    { key: 'tk_unmined', label: 'Unmined', group: 'tk', num: true, cls: 'sec', w: 80, cell: tk },
    { key: 'tk_data', label: 'Est. data', group: 'tk', num: true, w: 84, cell: tk },
  );
  ticketTypes.forEach((t) => cols.push({
    key: 'tk_t_' + (t.key || 'none'), label: t.label, group: 'tk', num: true, w: 66, cell: tk,
  }));
  cols.push({ key: 'pr_total', label: 'Total', group: 'pr', num: true, cls: 'sec', w: 72, cell: num });
  WINDOWS.forEach(([k, label]) => cols.push({
    key: 'pr_' + k, label, group: 'pr', num: true, w: 72, cell: num,
  }));
  cols.push({
    key: 'verdict', label: 'Verdict', group: 'vd', cls: 'sec', w: 150,
    opts: () => pmApi.VERDICT_NAMES,
    // DataTable's own in-place editor: click opens the list, arrows navigate,
    // and onEdit fires only when the value actually changed.
    editOpts: pmApi.VERDICT_NAMES,
    onEdit: onVerdict,
    optionCell: (o) => <span className="pm-opt"><i className={pmApi.verdictClass(o)} />{o}</span>,
    cell: (v) => <VerdictPill value={v} />,
  });
  cols.push({ key: 'name', label: 'Event name', group: 'meta', cell: (v) => v || dim() });
  return cols;
}

/** Flattens the per-type ticket counts onto the row so DataTable can sort them. */
function toRows(payload) {
  const types = payload.ticket_types || [];
  return (payload.rows || []).map((r) => {
    const flat = { ...r };
    types.forEach((t) => { flat['tk_t_' + (t.key || 'none')] = (r.tk_types || {})[t.key] || 0; });
    return flat;
  });
}

/**
 * Admin-only, and gated BEFORE anything fetches: the figures are live paid and
 * pending heads across the whole catalogue, and the server answers with
 * IsAdminRole. No grant under Roles opens this page, which `reason` says.
 */
export default function PerformanceMatrixPage() {
  const { isAdmin } = useSession();
  const toast = useToast();
  const [view, setView] = useState(pmApi.VIEWS.UPCOMING);
  const [years, setYears] = useState(() => new Set());

  const fetchMatrix = useCallback(
    () => (isAdmin ? pmApi.list(view) : Promise.resolve(null)),
    [isAdmin, view],
  );
  const { data, loading, refetchQuiet } = useFetch(fetchMatrix, [isAdmin, view], { initialData: null });
  // Every figure is an aggregate over other modules, so any write to them moves
  // the matrix, a webhook booking from the website included.
  useLiveData(refetchQuiet, { resources: ['delegates', 'invoices', 'events', 'tickets', 'paper-reviews'] });

  const payload = useMemo(() => data || {}, [data]);
  const rows = useMemo(() => toRows(payload), [payload]);
  const ticketTypes = useMemo(() => payload.ticket_types || [], [payload]);
  const benchmark = payload.benchmark || 40;

  // The year chips. None selected means every edition in the view; two ticked
  // means those two editions side by side, which is the comparison the matrix
  // is for. Read off the rows rather than the catalogue so a chip always has
  // something behind it in the current tab.
  const yearOptions = useMemo(() => [...new Set(rows.map((r) => r.year).filter(Boolean))].sort(), [rows]);
  const shown = useMemo(() => (years.size ? rows.filter((r) => years.has(r.year)) : rows), [rows, years]);
  const toggleYear = (y) => setYears((s) => { const n = new Set(s); if (n.has(y)) n.delete(y); else n.add(y); return n; });

  const setVerdict = useCallback(async (row, value) => {
    try {
      await pmApi.setVerdict(row.id, value === 'Standby' ? '' : value);
      toast(`${row.event_code}: ${value}`, 'ok');
      refetchQuiet();
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not save the verdict'), 'er');
    }
  }, [toast, refetchQuiet]);

  const cols = useMemo(() => buildCols(setVerdict, benchmark, ticketTypes), [setVerdict, benchmark, ticketTypes]);
  // Completed editions carry their own wash, whatever the verdict; the verdict
  // still shows in the edge and the cell.
  const rowClass = useCallback((r) => (r.done ? 'pm-done ' : '') + pmApi.verdictClass(r.verdict), []);

  if (!isAdmin) {
    return (
      <NoAccessPage
        module="Performance Matrix"
        reason="Performance figures are restricted to administrators. This page is not part of the module permissions, so it cannot be granted under Roles."
      />
    );
  }

  const isUpcoming = view === pmApi.VIEWS.UPCOMING;

  return (
    <div className="gs-page">
      <Tabs list={TABS} active={view} onPick={setView}
        actions={(
          <div className="pm-years">
            {yearOptions.map((y) => (
              <button key={y} type="button" className={'chip' + (years.has(y) ? ' on' : '')} onClick={() => toggleYear(y)}
                title={years.has(y) ? `Hide the ${y} editions` : `Show only the ${y} editions (tick several to compare)`}>
                {y}
              </button>
            ))}
            <span className="tabs-upd">{loading ? 'Refreshing…' : payload.today ? `Live · ${dateRange(payload.today)}` : ''}</span>
          </div>
        )} />

      {/* .pm-dense tightens the row and header rhythm for this table only; the
          page root above carries the flex sizing #main gives a bare table, so
          the scroller stays inside the viewport with its horizontal bar in reach. */}
      <div className="pm-dense">
        <DataTable
          key={view}
          tableId={`performance_matrix.v2.${view}`}
          rows={shown}
          cols={cols}
          groups={GROUPS}
          groupHeader
          noun="editions"
          pageSize={1000}
          // Nearest first: the matrix is read top-down as a queue. Past editions
          // have no countdown to queue on, so the All view leads with the latest.
          defaultSort={isUpcoming ? { key: 'days_left', dir: 'asc' } : { key: 'start_date', dir: 'desc' }}
          hiddenDefault={['name']}
          searchPlaceholder="Search event code…"
          canEdit
          rowClass={rowClass}
        />
      </div>
    </div>
  );
}
