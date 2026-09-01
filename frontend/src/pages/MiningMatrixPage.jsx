import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import DataTable from '../components/DataTable';
import { Tabs } from '../components/UI';
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
 * Col B   how many days until it opens, and the dates it runs
 * Col C   unmined links — tickets whose `actual_number` has not been filled in
 * Col D   unmined data — the estimate those tickets carry
 * Col E+  Col D split twice over, by Priority and by Ticket type, one column per
 *         value actually in use
 *
 * Col A and Col B are FROZEN. The two split blocks push the table well past the
 * width of any screen, and a reader scrolled into the ticket-type columns needs
 * to still know which event they are reading.
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
 * The class that colours one split column.
 *
 * The COLOURS are not here — they are theme tokens in styles/base.css
 * [band_palette], selected by these classes in components.css. That split is the
 * point: the palette has a dark-theme half, and a hex chosen in JavaScript and
 * set inline would be the one colour on the page that cannot follow the theme.
 *
 * The value is lower-cased and stripped to alphanumerics because it comes from a
 * free CharField (see services.SPLITS) and reaches the DOM as a class name. A
 * value the palette has no entry for simply matches nothing and the column
 * renders unbanded, which is the intended fallback rather than a defect —
 * `.bnd` carries neutral defaults for exactly that case.
 */
const bandClass = (value) => {
  const slug = String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  return slug ? `bnd bnd-${slug}` : 'bnd';
};

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

function buildCols(splits, splitColumns) {
  const cols = [
    {
      key: 'event_code',
      label: 'Event code',
      group: 'ev',
      // FROZEN. This column and the two after it are the row's identity, and this
      // table is wide enough that without them pinned a reader scrolled into the
      // ticket-type block is looking at numbers with nothing naming them. See the
      // `pins` block in DataTable for how the offsets are derived.
      pin: true,
      w: 150,
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
      /**
       * ALSO CARRIES THE JOIN WARNING, since the Ticket code column that used to
       * show it is gone. A row whose event code resolved onto a purpose Ticket
       * Central has never raised work under reads zero straight across, which is
       * indistinguishable from an event that is genuinely fully mined — so the
       * code is tinted and the tooltip says which of the two it is. Without this
       * the removal would have made a wrong-looking row unexplainable.
       */
      cell: (v, r) => (
        <Link
          className="mono lnk"
          style={r.matched ? undefined : { color: 'var(--amber-tx)' }}
          to={matrixApi.ticketsHref(r)}
          onClick={(e) => e.stopPropagation()}
          title={r.matched
            ? `Open ${r.canonical_code} tickets in Ticket Central, filtered to unmined`
            : `No tickets are filed under "${r.canonical_code}" — this row is empty `
              + 'because the code join found nothing, not because the work is done'}
        >
          {v}
        </Link>
      ),
    },
    {
      key: 'days_to_go',
      label: 'Days to go',
      group: 'dt',
      num: true,
      pin: true,
      w: 100,
      cell: (v) => <DaysBadge value={v} />,
    },
    {
      // ONE column, not a Starts and an Ends. It is read as a single fact — the
      // window the event occupies — and splitting it spent two frozen columns on
      // it, which is width taken from the numbers this page exists to show.
      //
      // Keyed on `start_date` and typed `date` so DataTable's date comparator
      // still orders it correctly (undated rows last in both directions), which a
      // synthesised "12 Feb - 14 Feb" string could not.
      key: 'start_date',
      label: 'Dates',
      type: 'date',
      group: 'dt',
      pin: true,
      w: 190,
      cell: (v, r) => {
        if (!v) return dim();
        if (!r.end_date || r.end_date === v) return fdate(v);
        return `${fdate(v)} – ${fdate(r.end_date)}`;
      },
    },
    { key: 'location', label: 'Location', group: 'ev', cell: (v) => v || dim() },
    {
      key: 'unmined_links', label: 'Unmined links', group: 'un', num: true,
      cell: (v) => (v ? <b style={{ color: 'var(--text)' }}>{nf(v)}</b> : zero()),
    },
    {
      key: 'unmined_data', label: 'Unmined data', group: 'un', num: true,
      cell: (v) => (v ? <b style={{ color: 'var(--text)' }}>{nf(v)}</b> : zero()),
    },
  ];

  // Col E onwards: one BLOCK of columns per split dimension, in the order the
  // server lists them. Keyed 'sp_<dim>_<value>' so a value can never collide with
  // a fixed column key, nor a priority with an identically named ticket type, and
  // flattened onto the row (see toRows) so DataTable's numeric sort compares
  // numbers rather than the strings a nested lookup would hand it.
  for (const dim of splits) {
    const block = splitColumns[dim.key] || [];
    block.forEach((c, i) => {
      cols.push({
        key: `sp_${dim.key}_${c.key}`,
        label: c.label,
        group: 'sp_' + dim.key,
        num: true,
        // `sec` on the FIRST column of each block draws the rule that separates
        // Ticket type from Priority. The band colours alone were not enough: two
        // adjacent runs of tinted columns still read as one continuous field, and
        // the reader has to know where one question ends and the next begins.
        cls: bandClass(c.key) + (i === 0 ? ' sec' : ''),
        // The hover carries the LINK count for the same cell. Both figures matter
        // — 4,000 estimated across two links is a different afternoon from 4,000
        // across ninety — and giving each its own column would double the width of
        // the widest part of this table.
        cell: (v, r) => {
          const links = (r.split_links[dim.key] || {})[c.key] || 0;
          if (!v && !links) return zero();
          return (
            <span title={`${nf(links)} link${links === 1 ? '' : 's'} — ${dim.label} ${c.label}`}>
              {nf(v)}
            </span>
          );
        },
      });
    });
  }
  return cols;
}

/** Flattens each split's map onto the row, and gives DataTable a stable id. */
function toRows(payload) {
  const splits = payload.splits || [];
  const splitColumns = payload.split_columns || {};
  return (payload.rows || []).map((r, i) => {
    const flat = {
      ...r,
      // event_code repeats across editions in the All view, so it is not a key on
      // its own; the index makes the row identity unique without inventing one
      // the server would then have to keep stable.
      id: `${r.event_code}#${i}`,
      split_links: r.split_links || {},
    };
    for (const dim of splits) {
      const data = (r.split_data || {})[dim.key] || {};
      for (const c of splitColumns[dim.key] || []) {
        flat[`sp_${dim.key}_${c.key}`] = data[c.key] || 0;
      }
    }
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
  const splits = useMemo(() => payload.splits || [], [payload]);
  const splitColumns = useMemo(() => payload.split_columns || {}, [payload]);
  // Memoised on the payload, not rebuilt each render: DataTable memoises its Row
  // on the `cols` identity, and this table is wide enough that losing that memo
  // is felt. See the TK_COLS note in TicketCentralPage.
  const cols = useMemo(() => buildCols(splits, splitColumns), [splits, splitColumns]);
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

      {/* THE TOTALS BAR.

          NOT A FOOTER ROW. In the All view one family appears once per edition
          and every one of those rows carries identical figures, because Ticket
          Central has one purpose code and not three — so a total summed down the
          visible column double-counts. The server totals over DISTINCT codes
          instead (mining_matrix/services._totals), which is a different number
          from the one under the column; at the foot of that column it would read
          as an arithmetic error rather than as the correction it is.

          THE LAYOUT. Hierarchy carries this, not boxes: the three figures that
          answer "how much is outstanding" are set large and lead, and everything
          after them is a breakdown at roughly half the size. No cards, no borders
          between items, one hairline per section — so the bar is scanned in the
          order the numbers matter rather than as fifteen equal tiles.

          COLOUR IS AN ACCENT, NOT A FILL. Each breakdown figure carries a small
          dot in its column's own hue, so a value here points at the block below
          it. Tinting the figures themselves would make a summary line read as a
          colour chart and cost the numbers their contrast — the same restraint
          the column bands use.

          Each split run sums to Data on its own: ticket type and priority are two
          cuts of the same money, not two halves of it. */}
      <div className="kbar">
        <div className="kbar-lead">
          <span className="kbar-fig">
            <b>{nf(totals.unmined_links || 0)}</b><em>Unmined links</em>
          </span>
          <span className="kbar-fig">
            <b>{nf(totals.unmined_data || 0)}</b><em>Unmined data</em>
          </span>
          <span className="kbar-fig">
            <b>{nf(totals.rows || 0)}</b><em>{isUnlinked ? 'Codes' : 'Events'}</em>
          </span>
        </div>
        {splits.map((dim) => (
          <div className="kbar-sec" key={dim.key}>
            <span className="kbar-sec-l">{dim.label}</span>
            <span className="kbar-chips">
              {(splitColumns[dim.key] || []).map((c) => (
                <span className={'kbar-chip ' + bandClass(c.key)} key={c.key}>
                  <i aria-hidden="true" />
                  <b>{nf(((totals.split_data || {})[dim.key] || {})[c.key] || 0)}</b>
                  <em>{c.label}</em>
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>

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
          ...splits.map((d) => ({ key: 'sp_' + d.key, label: 'By ' + d.label.toLowerCase() })),
        ]}
        hiddenDefault={['location']}
        // Not "or name": the Event and Status columns were removed, and DataTable's
        // in-memory search reads the COLUMNS, so a name is no longer searchable
        // here. Saying otherwise would promise a match the table cannot make.
        searchPlaceholder="Search event code…"
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
