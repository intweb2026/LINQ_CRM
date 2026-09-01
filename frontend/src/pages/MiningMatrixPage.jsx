import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import DataTable from '../components/DataTable';
import { Kpi, Tabs } from '../components/UI';
import { EvBadge } from '../components/Badge';
import { Icon } from '../lib/icons';
import { fdate, nf } from '../lib/helpers';
import * as matrixApi from '../api/miningMatrix';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import NoAccessPage from './NoAccessPage';

/**
 * MINING RESOURCE MATRIX
 * ──────────────────────
 * One row per event, answering "how much Market Research output is still waiting
 * on Data Mining here, and how does it split by priority".
 *
 * Col A   the event code, as the Events module holds it, linking through to
 *         Ticket Central already filtered to exactly these tickets
 * Col B   the event's start and end dates, plus how many days until it opens
 * Col C   unmined links — tickets whose `actual_number` has not been filled in
 * Col D   unmined data — the estimate those tickets carry
 * Col E+  Col D split by priority, one column per value in use
 *
 * THE JOIN, AND WHY IT IS NOT event_code = event_code. Ticket Central files work
 * under a short stable code (`purpose`: AFS, DDU, BAPE) that does not change from
 * year to year, because the ticket number is built from it. The Events catalogue
 * uses a per-edition code that does: AFS, AFS - JS and Feb2027_AFS-JS are three
 * editions of one family and every ticket for any of them reads "AFS". The server
 * reduces the event code to the purpose it belongs to — see
 * backend/mining_matrix/codes.py — and the resolved code is shown in its own
 * column so a row that joined oddly can be spotted rather than quietly reading
 * zero.
 *
 * WHY THE COLUMNS ARE BUILT AT RUNTIME. `Ticket.priority` is a free CharField,
 * not a constrained choice set (the D4 note in ticket_central/models.py), so the
 * server returns the priorities actually in use and this builds a column for
 * each. A hardcoded list would silently drop unmined work under any value nobody
 * had thought of — on the one screen that exists to surface it.
 */

const TAB_LABELS = {
  [matrixApi.VIEWS.UPCOMING]: 'Upcoming',
  [matrixApi.VIEWS.ALL]: 'All events',
  [matrixApi.VIEWS.UNLINKED]: 'Unlinked codes',
};

const dim = () => <span className="dim">—</span>;
const zero = () => <span className="dim">0</span>;

/**
 * How long until the event opens — the column the whole default view is ordered
 * by, so it is a badge rather than a bare integer.
 *
 * Banded, not coloured by a threshold on the number itself: "14" means something
 * different from "140" at a glance only if the two look different. Under a week
 * is red because there is no room left to schedule miners against it; under a
 * month is amber. A past date only appears in the All and Unlinked views, where
 * it reads as history rather than as a deadline.
 */
function DaysBadge({ value }) {
  if (value == null) return dim();
  if (value < 0) return <span className="dim">{nf(-value)}d ago</span>;
  if (value === 0) return <span className="tg bg-red">Today</span>;
  const tone = value <= 7 ? 'red' : value <= 30 ? 'amber' : 'neutral';
  return <span className={'tg bg-' + tone}>{nf(value)}d</span>;
}

function buildCols(priorityColumns) {
  const cols = [
    {
      key: 'event_code',
      label: 'Event code',
      group: 'ev',
      /**
       * A real <Link>, not a row click. Ctrl/cmd-click and "open in new tab" have
       * to work: the normal way to use this page is to open three or four codes
       * side by side and work through them, and a JavaScript navigation would
       * reopen the matrix in each new tab instead.
       *
       * stopPropagation because these rows sit in a table whose own onRow could
       * later be given a drawer; a plain click must follow the link and do
       * nothing else.
       */
      cell: (v, r) => (
        <Link
          className="mono lnk"
          to={matrixApi.ticketsHref(r)}
          onClick={(e) => e.stopPropagation()}
          title={`Open ${r.canonical_code} tickets in Ticket Central, filtered to unmined`}
        >
          {v}
        </Link>
      ),
    },
    {
      key: 'canonical_code',
      label: 'Ticket code',
      group: 'ev',
      // Shown, not hidden behind the Columns menu. It is the answer to "why does
      // this row read zero" whenever the code join is the reason, and a column
      // nobody can see cannot answer anything.
      cell: (v, r) => (
        <span className="mono" style={{ color: r.matched ? 'var(--text-3)' : 'var(--amber-tx)' }}
          title={r.matched ? '' : 'No tickets are filed under this code'}>
          {v || '—'}
        </span>
      ),
    },
    { key: 'event_name', label: 'Event', group: 'ev', cls: 'st', cell: (v) => v || dim() },
    { key: 'status', label: 'Status', group: 'ev', cell: (v) => (v ? <EvBadge value={v} /> : dim()) },
    { key: 'location', label: 'Location', group: 'ev', cell: (v) => v || dim() },
    { key: 'days_to_go', label: 'Days to go', group: 'dt', num: true, cell: (v) => <DaysBadge value={v} /> },
    { key: 'start_date', label: 'Starts', type: 'date', group: 'dt', cell: (v) => (v ? fdate(v) : dim()) },
    { key: 'end_date', label: 'Ends', type: 'date', group: 'dt', cell: (v) => (v ? fdate(v) : dim()) },
    {
      key: 'unmined_links', label: 'Unmined links', group: 'un', num: true,
      cell: (v) => (v ? <b style={{ color: 'var(--text)' }}>{nf(v)}</b> : zero()),
    },
    {
      key: 'unmined_data', label: 'Unmined data', group: 'un', num: true,
      cell: (v) => (v ? <b style={{ color: 'var(--text)' }}>{nf(v)}</b> : zero()),
    },
  ];

  // Col E onwards. Keyed 'pri_<value>' so a priority can never collide with one
  // of the fixed column keys above, and flattened onto the row (see toRows) so
  // DataTable's numeric sort compares numbers rather than the strings a nested
  // lookup would hand it.
  for (const p of priorityColumns) {
    cols.push({
      key: 'pri_' + p.key,
      label: p.label,
      group: 'pri',
      num: true,
      // The hover carries the LINK count for the same cell. Both figures matter —
      // 4,000 estimated across two links is a different afternoon from 4,000
      // across ninety — and giving each its own column would double the width of
      // the widest part of the table.
      cell: (v, r) => {
        const links = r.priority_links[p.key] || 0;
        if (!v && !links) return zero();
        return (
          <span title={`${nf(links)} link${links === 1 ? '' : 's'} at ${p.label}`}>
            {nf(v)}
          </span>
        );
      },
    });
  }
  return cols;
}

/** Flattens the priority maps onto each row, and gives DataTable a stable id. */
function toRows(payload) {
  const keys = (payload.priority_columns || []).map((c) => c.key);
  return (payload.rows || []).map((r, i) => {
    const flat = {
      ...r,
      // event_code repeats across editions in the All view, so it is not a key on
      // its own; the index makes the row identity unique without inventing one
      // the server would then have to keep stable.
      id: `${r.event_code}#${i}`,
      priority_links: r.priority_links || {},
    };
    for (const k of keys) flat['pri_' + k] = (r.priority_data || {})[k] || 0;
    return flat;
  });
}

export default function MiningMatrixPage() {
  const { canView } = useSession();
  const [view, setView] = useState(matrixApi.VIEWS.UPCOMING);
  const [includeZero, setIncludeZero] = useState(false);

  const fetchMatrix = useCallback(
    () => matrixApi.list(view, includeZero), [view, includeZero],
  );
  const { data, loading, refetchQuiet } = useFetch(fetchMatrix, [view, includeZero], {
    initialData: null,
  });
  // The figures are aggregates over Ticket Central, so anything that writes a
  // ticket moves them — a DMD submit from a colleague's browser included. Same
  // subscription the ticket tab counts use.
  useLiveData(refetchQuiet, { resources: ['tickets'] });

  // `data || {}` written inline would be a NEW object on every render for as long
  // as the first fetch is in flight, which would make `rows` below recompute
  // every render and hand DataTable a fresh array each time — defeating the memo
  // on Row across a table this wide. One useMemo fixes the identity.
  const payload = useMemo(() => data || {}, [data]);
  const priorityColumns = useMemo(() => payload.priority_columns || [], [payload]);
  // Memoised on the payload, not rebuilt each render: DataTable memoises its Row
  // on the `cols` identity, and this table is wide enough that losing that memo
  // is felt. See the TK_COLS note in TicketCentralPage.
  const cols = useMemo(() => buildCols(priorityColumns), [priorityColumns]);
  const rows = useMemo(() => toRows(payload), [payload]);

  if (!canView('mining_matrix')) return <NoAccessPage module="Mining Resource Matrix" />;

  const counts = payload.view_counts || {};
  const totals = payload.totals || {};
  const isUnlinked = view === matrixApi.VIEWS.UNLINKED;
  const TABS = Object.entries(TAB_LABELS).map(([id, label]) => ({
    id, label, count: counts[id],
  }));

  return (
    <>
      <Tabs list={TABS} active={view} onPick={setView} />

      {/* WHY THE TOTALS SIT HERE AND NOT IN A FOOTER ROW. In the All view one
          family appears once per edition and every one of those rows carries the
          same figures, because Ticket Central has one purpose code and not three
          — so adding the visible column up double-counts. The server totals over
          DISTINCT codes instead (mining_matrix/services._totals), and `codes`
          below is what makes the difference legible rather than mysterious. */}
      <div className="kpis">
        <Kpi label="Unmined links" value={totals.unmined_links || 0} icon="link"
          sub={`across ${nf(totals.codes || 0)} event ${(totals.codes === 1) ? 'code' : 'codes'}`} />
        <Kpi label="Unmined data" value={totals.unmined_data || 0} icon="chart"
          sub="sum of estimates still to mine" />
        <Kpi label={isUnlinked ? 'Unlinked codes' : 'Events listed'} value={totals.rows || 0}
          icon={isUnlinked ? 'warn' : 'calendar'}
          tone={isUnlinked ? 'var(--amber)' : undefined}
          sub={isUnlinked
            ? 'no upcoming event covers this work'
            : `${nf(counts[matrixApi.VIEWS.UNLINKED] || 0)} more in Unlinked codes`} />
      </div>

      {/* The per-priority totals, in the same order and with the same labels as
          the columns below, so the strip reads as the table's footer without
          having to live inside it. These sum to "Unmined data" above. */}
      {priorityColumns.length ? (
        <div className="pri-tot">
          <span className="pri-tot-l">By priority</span>
          {priorityColumns.map((p) => (
            <span className="pri-tot-i" key={p.key}>
              <em>{p.label}</em>
              <b>{nf((totals.priority_data || {})[p.key] || 0)}</b>
            </span>
          ))}
        </div>
      ) : null}

      {payload.no_purpose && payload.no_purpose.links ? (
        /* Unmined tickets carrying no purpose at all. They can never be a row —
           there is nothing to group them under — so the count is stated rather
           than dropped without trace. */
        <div className="lnk-filter">
          <Icon name="warn" size={14} />
          <span>
            <b>{nf(payload.no_purpose.links)}</b> unmined{' '}
            {payload.no_purpose.links === 1 ? 'ticket has' : 'tickets have'} no purpose code,
            so {payload.no_purpose.links === 1 ? 'it is' : 'they are'} not on any row below.
          </span>
        </div>
      ) : null}

      <DataTable
        /**
         * ONE TABLE ID AND ONE MOUNT PER VIEW, both deliberate.
         *
         * DataTable reads its stored sort and column visibility ONCE, into
         * useState initialisers seeded from localStorage (see `storedRef`), and a
         * stored sort deliberately BEATS `defaultSort` — cycling sort off is a
         * choice worth keeping across a reload. Two consequences here, since the
         * three views share this component:
         *
         *   Without `key`, switching view would not re-read anything, so a view's
         *   own defaultSort would apply only to whichever view happened to be
         *   mounted first. Remounting is right anyway: the row set is entirely
         *   different, so carrying scroll position and search across is wrong.
         *
         *   Without the view in `tableId`, all three would share one stored sort.
         *   "Soonest first" leaking into Unlinked — where most rows have no date
         *   at all — would silently reorder that view by a column it cannot use.
         *
         * `.v1` follows tickets.v2: the key is versioned so a later change to the
         * default column set is not invisible to everyone who has already opened
         * the page.
         */
        key={view}
        tableId={`mining_matrix.v1.${view}`}
        rows={rows}
        cols={cols}
        noun={isUnlinked ? 'codes' : 'events'}
        pageSize={1000}
        // Soonest first in the event views: the matrix is read top-down as a
        // queue. Unlinked codes have no commencement to queue on — that is what
        // makes them unlinked — so that view leads with the biggest pile instead.
        defaultSort={isUnlinked
          ? { key: 'unmined_data', dir: 'desc' }
          : { key: 'start_date', dir: 'asc' }}
        groups={[
          { key: 'ev', label: 'Event' },
          { key: 'dt', label: 'Dates' },
          { key: 'un', label: 'Unmined' },
          { key: 'pri', label: 'By priority' },
        ]}
        hiddenDefault={['location']}
        searchPlaceholder="Search event code or name…"
        // No tab strip to fold these into — the strip above switches VIEWS, which
        // is a different question — so they ride on the table's own toolbar row,
        // the same placement Paper Review uses for its filter toggle.
        extraToolbar={(
          <>
            {/* Offered only where it means anything. The Unlinked view is defined
                as "codes that still hold unmined work", so it has no zero row to
                reveal. */}
            {isUnlinked ? null : (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-3)', whiteSpace: 'nowrap' }}
                title="Also list events whose links have all been mined. Off by default: the matrix is a worklist, and a fully mined event has nothing left to schedule against.">
                <input type="checkbox" className="ck" checked={includeZero}
                  onChange={(e) => setIncludeZero(e.target.checked)} />
                Include fully mined
              </label>
            )}
            {loading ? <span className="dim" style={{ fontSize: 11.5 }}>Loading…</span> : null}
          </>
        )}
      />
    </>
  );
}
