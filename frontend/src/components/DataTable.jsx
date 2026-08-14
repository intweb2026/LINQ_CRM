import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../lib/icons';
import { nf } from '../lib/helpers';
import Popover from './Popover';
import { EmptyState, Seg } from './UI';
import {
  MAX_SPEC_BYTES, condIsActive, orderingParam, partitionConds, specByteLength, specToJson,
} from '../lib/filterSpec';
import useServerRows from '../hooks/useServerRows';
import useLiveData from '../hooks/useLiveData';
import { apiErrorMessage, fetchAllIds, fetchPage } from '../api/client';

const PAGE_SIZE_DEFAULT = 50;

/**
 * Ceiling on the one-request reload a background refresh uses in infinite mode
 * (see liveReload). config/pagination.py caps page_size at 500, so a user who has
 * scrolled past that many rows cannot have the whole span refreshed in a single
 * request — and refreshing only PART of it would silently drop the rest off the
 * screen. Past this point background refresh stands down and the explicit
 * Refresh button remains the way to reload.
 */
const MAX_LIVE_SPAN = 500;

const FILTER_OPS = ['Contains', 'Not Contains', 'Is', 'Is Not', 'Starts With', 'Ends With', 'Like', 'Is Empty', 'Is Not Empty'];
const NO_VALUE_OPS = ['Is Empty', 'Is Not Empty'];

function condActive(cond) {
  return NO_VALUE_OPS.includes(cond.op) || cond.values.length > 0 || !!cond._live;
}

function opLabel(op) {
  return {
    Is: 'is', 'Is Not': 'is not', Contains: 'contains', 'Not Contains': 'does not contain',
    'Starts With': 'starts with', 'Ends With': 'ends with', Like: 'is like',
    'Is Empty': 'is empty', 'Is Not Empty': 'is not empty',
  }[op] || op.toLowerCase();
}

function fmtValues(values) {
  if (!values.length) return '""';
  if (values.length === 1) return `"${values[0]}"`;
  return values.slice(0, -1).map((v) => `"${v}"`).join(',') + 'or' + `"${values[values.length - 1]}"`;
}

function likeTest(val, pattern) {
  const esc = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/%/g, '.*').replace(/_/g, '.');
  return new RegExp('^' + esc + '$').test(val);
}

function condPasses(row, cond) {
  const val = String(row[cond.key] == null ? '' : row[cond.key]).trim();
  if (cond.op === 'Is Empty') return val === '' || val === '—';
  if (cond.op === 'Is Not Empty') return val !== '' && val !== '—';
  const allValues = cond._live ? [...cond.values, cond._live] : cond.values;
  const vs = allValues.map((v) => String(v).toLowerCase()).filter(Boolean);
  if (!vs.length) return true;
  const lv = val.toLowerCase();
  switch (cond.op) {
    case 'Is': return vs.includes(lv);
    case 'Is Not': return !vs.includes(lv);
    case 'Not Contains': return !vs.some((v) => lv.includes(v));
    case 'Starts With': return vs.some((v) => lv.startsWith(v));
    case 'Ends With': return vs.some((v) => lv.endsWith(v));
    case 'Like': return vs.some((v) => likeTest(lv, v));
    case 'Contains':
    default: return vs.some((v) => lv.includes(v));
  }
}

// ── Persistence ─────────────────────────────────────────────────────────────
// Filter, sort and hidden-column state survive a reload and are cleared only by
// explicit user action ("Clear all", or unchecking a filter). `_live` is a
// transient keystroke draft and is deliberately NOT persisted — restoring a
// half-typed value would silently filter the table on next load.
const STORE_PREFIX = 'iqhub.table.';
const STORE_VERSION = 1;

function readStored(tableId) {
  if (!tableId) return null;
  try {
    const raw = window.localStorage.getItem(STORE_PREFIX + tableId);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (!p || p.version !== STORE_VERSION) return null;
    return {
      conds: Array.isArray(p.conds) ? p.conds.map((c) => ({ ...c, _live: '' })) : [],
      sort: p.sort || null,
      hidden: Array.isArray(p.hidden) ? p.hidden : null,
    };
  } catch {
    return null;                       // private mode, quota, corrupt JSON
  }
}

function writeStored(tableId, { conds, sort, hidden }) {
  if (!tableId) return;
  try {
    window.localStorage.setItem(STORE_PREFIX + tableId, JSON.stringify({
      version: STORE_VERSION,
      conds: (conds || []).map(({ _live, ...c }) => c),
      sort: sort || null,
      hidden: [...(hidden || [])],
    }));
  } catch {
    /* storage unavailable — in-memory state still works */
  }
}

// `onLive` (optional) reports every keystroke immediately, so the table filters as
// you type instead of only once Enter/blur commits the text as a removable chip —
// otherwise a typed-but-uncommitted value silently filters nothing, which reads as
// "the filter doesn't work" even though the logic is correct.
function ValueTagInput({ values, onChange, onLive, pill }) {
  const [draft, setDraft] = useState('');
  function add() {
    const v = draft.trim();
    // Always call onChange exactly once (never a second onLive('') alongside it) —
    // the caller clears the pending live draft as part of this same update. Two
    // separate calls here would race against each other from the same stale
    // `values` snapshot and silently drop the just-added value.
    onChange(v && !values.includes(v) ? [...values, v] : values);
    setDraft('');
  }
  return (
    <div className="vti">
      {values.length ? (
        <div className="vti-tags">
          {values.map((v, i) => (
            <span className="vti-tag" key={i}>{v}<button type="button" onClick={() => onChange(values.filter((_, idx) => idx !== i))} aria-label="Remove value"><Icon name="x" size={10} /></button></span>
          ))}
        </div>
      ) : null}
      <input className={pill ? 'flt-pill' : 'in in-xs'} placeholder="Type a value, press Enter…" value={draft}
        onChange={(e) => { setDraft(e.target.value); if (onLive) onLive(e.target.value); }}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
        onBlur={add}
      />
    </div>
  );
}

// A button + inline dropdown list styled identically to ValueTagInput's `.flt-pill`
// input — a native <select>'s open-state option list can't be restyled with CSS, so
// it always looks like a foreign control next to the value field; this is fully
// custom markup instead, guaranteeing pixel-identical box model, border and type.
function OperatorSelect({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="flt-op" ref={ref}>
      <button type="button" className="flt-pill flt-op-btn" onClick={() => setOpen((o) => !o)}>
        <span>{value}</span>
        <Icon name="chevD" size={13} />
      </button>
      {open ? (
        <div className="flt-op-menu">
          {FILTER_OPS.map((o) => (
            <button type="button" className="pop-i" key={o} onClick={() => { onChange(o); setOpen(false); }}>
              {o === value ? <Icon name="check" size={14} /> : <span style={{ width: 14 }} />}
              {o}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// Unified checklist item for the toolbar Search/Filter panel: checking a field
// expands its operator + value editor directly beneath it (spreadsheet-search style).
function FilterListItem({ col, cond, onToggle, onChangeCond }) {
  const checked = !!cond;
  const value = cond || { key: col.key, op: 'Contains', values: [] };
  const needsValue = checked && !NO_VALUE_OPS.includes(value.op);
  const opts = col.opts ? col.opts() : null;
  return (
    <div className="flt-item">
      <label className="pop-i"><input type="checkbox" checked={checked} onChange={onToggle} />{col.label}</label>
      {checked ? (
        <div className="flt-editor">
          <OperatorSelect value={value.op} onChange={(op) => onChangeCond({ ...value, op })} />
          {needsValue ? (
            opts ? (
              <div className="vti-opts">
                {opts.map((o) => (
                  <label className="pop-i" key={o} style={{ padding: '3px 6px' }}>
                    <input type="checkbox" checked={value.values.includes(o)} onChange={() => {
                      const has = value.values.includes(o);
                      onChangeCond({ ...value, values: has ? value.values.filter((v) => v !== o) : [...value.values, o] });
                    }} />
                    {o}
                  </label>
                ))}
              </div>
            ) : (
              <ValueTagInput pill values={value.values} onChange={(values) => onChangeCond({ ...value, values, _live: '' })}
                onLive={(text) => onChangeCond({ ...value, _live: text })} />
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FilterRow({ col, cond, onChange, onRemove }) {
  const opts = col.opts ? col.opts() : null;
  const needsValue = !NO_VALUE_OPS.includes(cond.op);
  return (
    <div className="flt-row">
      <div className="flt-row-h"><span className="flt-row-f">{col.label}</span><button type="button" onClick={onRemove} aria-label="Remove filter"><Icon name="x" size={12} /></button></div>
      <label className="flt-row-l">Operator</label>
      <select className="in in-xs" value={cond.op} onChange={(e) => onChange({ ...cond, op: e.target.value })}>
        {FILTER_OPS.map((o) => <option key={o}>{o}</option>)}
      </select>
      {needsValue ? (
        <>
          <label className="flt-row-l">Value</label>
          {opts ? (
            <div className="vti-opts">
              {opts.map((o) => (
                <label className="pop-i" key={o} style={{ padding: '3px 6px' }}>
                  <input type="checkbox" checked={cond.values.includes(o)} onChange={() => {
                    const has = cond.values.includes(o);
                    onChange({ ...cond, values: has ? cond.values.filter((v) => v !== o) : [...cond.values, o] });
                  }} />
                  {o}
                </label>
              ))}
            </div>
          ) : (
            <ValueTagInput values={cond.values} onChange={(values) => onChange({ ...cond, values })} />
          )}
        </>
      ) : null}
    </div>
  );
}

/**
 * Header cell: the column LABEL is the sort control (cycles ascending →
 * descending → unsorted) and the funnel is a separate button that opens the
 * filter editor. Two separate controls because one button cannot both sort and
 * open a popover, which is why sorting was previously unreachable.
 */
function HeaderCell({ col, cond, sort, canSort = true, onSort, onChange, onRemove }) {
  const active = cond ? condActive(cond) : false;
  const value = cond || { key: col.key, op: 'Contains', values: [] };
  const dir = sort && sort.key === col.key ? sort.dir : null;
  return (
    <th className={(col.num ? 'num ' : '') + (active ? 'act' : '')}>
      <div className="th-w">
        {canSort ? (
          <button
            type="button"
            className={'th-sort' + (dir ? ' on' : '')}
            onClick={onSort}
            aria-label={`Sort by ${col.label}`}
            title={dir === 'asc' ? 'Sorted ascending — click for descending'
              : dir === 'desc' ? 'Sorted descending — click to clear' : `Sort by ${col.label}`}
          >
            <span>{col.label}</span>
            {dir ? <Icon name={dir === 'asc' ? 'chevU' : 'chevD'} size={11} /> : null}
          </button>
        ) : (
          <span className="th-sort th-nosort" title="This column cannot be sorted by the server">{col.label}</span>
        )}
        <Popover trigger={({ toggle }) => (
          <button type="button" className={'th-flt-btn' + (active ? ' on' : '')} onClick={toggle} aria-label={`Filter ${col.label}`}>
            <Icon name="filter" size={10} />
          </button>
        )}>
          {() => <FilterRow col={col} cond={value} onChange={onChange} onRemove={onRemove} />}
        </Popover>
      </div>
    </th>
  );
}

function EditableCell({ row, col, value }) {
  return (
    <Popover
      trigger={({ toggle }) => (
        <span className="ec" onClick={(e) => { e.stopPropagation(); toggle(); }}>
          <span className="ec-v">{col.cell ? col.cell(value, row) : value}</span>
        </span>
      )}
    >
      {({ close }) => {
        const opts = typeof col.editOpts === 'function' ? col.editOpts(row) : col.editOpts;
        return (
          <>
            <div className="pop-t">{col.label}</div>
            <div className="pop-mx">
              {opts.map((o) => (
                <button key={o} className="pop-i" onClick={() => { close(); if (String(row[col.key]) !== String(o)) col.onEdit(row, o); }}>
                  {String(row[col.key]) === String(o) ? <Icon name="check" size={15} /> : <span style={{ width: 15 }} />}
                  {o}
                </button>
              ))}
            </div>
          </>
        );
      }}
    </Popover>
  );
}

/**
 * DataTable operates in one of two modes.
 *
 * IN-MEMORY (default): the caller passes a fully-loaded `rows` array and every
 * filter, sort and page is computed locally. Correct for the small tables
 * (roles, teams, webhook keys, …) where the whole set is a few hundred rows.
 *
 * SERVER (`server={{ resource: 'delegates' }}`): Django does the filtering,
 * ordering and pagination. Required wherever the table is large enough that
 * loading it all is either slow or wrong — see lib/filterSpec.js for why
 * `payment_status` in particular cannot be filtered correctly in the browser.
 * In this mode `rows` is ignored; conditions the backend cannot express fall
 * back to filtering the fetched page, and the toolbar says so rather than
 * pretending the result is the full filtered set.
 *
 * A server-mode table also keeps ITSELF current — see the liveReload block. It
 * refreshes when anything writes to its resource, and polls while visible. Where
 * a write lands on a different path than the one being read, name the extra paths
 * in `server.live`: bookings are READ from `delegates/` but an import and an
 * invoice edit both write `invoices/`, so without `live: ['invoices']` neither
 * would reach the table.
 */
export default function DataTable({
  rows, cols, noun = 'records', groups, hiddenDefault = [], select = false, infinite = false,
  pageSize = PAGE_SIZE_DEFAULT, defaultSort = null, scope = null, searchPlaceholder = 'Search…',
  card, onRow, bulkActions, extraToolbar, tableId, server = null,
  // Whether this table may edit a cell in place. Defaults to FALSE, so a column
  // carrying editOpts is inert until its page explicitly opts in with the
  // caller's own permission check — previously EditableCell rendered off the
  // mere presence of editOpts, which handed a read-only role a working status
  // editor whose PATCH the server then rejected with a 403 nobody surfaced.
  canEdit = false,
  // Receives the table's `refetch` so a parent can reload after a write. Only
  // the function is handed out, never the whole fetch state: that object has a
  // new identity every render, so a parent storing it in state would re-render
  // this component forever. `refetch` is stable.
  onServerReady,
  // Extra filter_spec criteria ANDed into every server request — the tab strip
  // uses this. A tab is a real query, not a client-side narrowing of one page:
  // narrowing locally would make "Paid (1,204)" mean "1,204 of the 50 rows I
  // happen to have", and for payment_status the resolved-field semantics mean
  // the browser cannot reproduce the server's answer anyway.
  serverCriteria = null,
  /**
   * Extra query params sent with every server request — `{ period: 'last_7_days' }`
   * is the one caller today (see accounts/period_filter.py PeriodFilterMixin).
   *
   * Separate from serverCriteria because it is NOT a filter_spec criterion and
   * cannot be one: the booking window is COALESCE(request_date, invoice_date),
   * which no single-column criterion expresses, and Ticket Central's window is
   * over created_at, which filter_spec excludes from every resource on purpose.
   * Sent as its own param so the server owns both definitions.
   *
   * Values are folded into the fetch key by VALUE, so a caller may rebuild the
   * object every render without retriggering the request.
   */
  serverParams = null,
  // Background refresh, server mode only — see the liveReload block below.
  // `false` opts a table out; a number overrides the poll interval.
  live = true,
}) {
  const storeId = tableId || noun;
  const storedRef = useRef(undefined);
  if (storedRef.current === undefined) storedRef.current = readStored(storeId);
  const stored = storedRef.current;

  const [q, setQ] = useState('');
  // `stored` present means the user has interacted with this table before, so
  // their sort wins — INCLUDING an explicit null, which is "I cycled sort off".
  // Falling back to defaultSort on a null would make turning sort off impossible
  // to keep across a reload, even though it is exactly as deliberate an action as
  // choosing a column.
  const [sort, setSort] = useState(() => (stored ? stored.sort : defaultSort));
  const [conds, setConds] = useState(() => (stored ? stored.conds : []));
  const [page, setPage] = useState(1);
  const [shown, setShown] = useState(pageSize);
  const [sel, setSel] = useState(new Set());
  /**
   * Non-null when `sel` holds every row the current query matches, rather than
   * rows the user ticked off the page — see selectEverything.
   *
   * The distinction is load-bearing twice over: the prune effect below must not
   * run against a selection whose rows were never loaded, and the caption has to
   * be able to say "all 35,690" without inferring it from `sel.size === total`,
   * which would also be true of a 12-row table where every row happens to be
   * ticked by hand.
   */
  const [selAll, setSelAll] = useState(false);
  const [selBusy, setSelBusy] = useState(false);
  const [selError, setSelError] = useState('');
  const [hidden, setHidden] = useState(() => new Set(stored && stored.hidden ? stored.hidden : hiddenDefault));
  const [view, setView] = useState('table');

  // Persist whenever any persisted slice changes.
  useEffect(() => {
    writeStored(storeId, { conds, sort, hidden });
  }, [storeId, conds, sort, hidden]);

  const activeCols = cols.filter((c) => !hidden.has(c.key));
  const serverMode = !!(server && server.resource);

  // ── Server-side spec + ordering ───────────────────────────────────────────
  // partitionConds needs the schema, which arrives asynchronously; before it
  // does, nothing is sent as a spec and nothing is claimed to be server-filtered.
  const hasStoredSpec = !!(stored && stored.conds && stored.conds.length > 0);
  const [schemaForSplit, setSchemaForSplit] = useState(null);

  const split = useMemo(
    () => (serverMode ? partitionConds(conds, cols, schemaForSplit) : { criteria: [], clientConds: conds, unsupported: [] }),
    // cols is rebuilt every render by most callers, so depend on the keys rather
    // than the array identity or this recomputes (and refetches) continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [serverMode, conds, schemaForSplit, cols.map((c) => c.key).join('|')],
  );

  // serverCriteria is stringified into the dep chain via specJson, so a caller
  // rebuilding the array each render does not retrigger the fetch.
  const specJson = serverMode
    ? specToJson([...(serverCriteria || []), ...split.criteria])
    : null;
  const specBytes = specByteLength(specJson);
  const specTooLarge = specBytes > MAX_SPEC_BYTES;
  const ordering = serverMode ? orderingParam(sort, cols) : null;

  // Serialised for the same reason specJson is: a params object rebuilt each
  // render would be a new identity every time and refetch forever. Sorted so
  // `{a,b}` and `{b,a}` are one key rather than two.
  const paramsJson = serverMode && serverParams
    ? JSON.stringify(Object.fromEntries(
      Object.entries(serverParams).filter(([, v]) => v !== undefined && v !== null && v !== '').sort(),
    ))
    : null;

  /**
   * Identity of the MATCHING SET — deliberately not useServerRows' request key.
   *
   * That key includes page, page size and ordering, because they change which
   * rows come back. None of them changes which rows MATCH, and a select-all has
   * to survive the user re-sorting the column or paging around to spot-check
   * what they are about to edit. Only the spec, the search text and the extra
   * server params do.
   */
  const matchKey = serverMode ? `${specJson || ''}|${q || ''}|${paramsJson || ''}` : null;

  /**
   * A whole-set selection describes ONE query, so a new query retires it.
   *
   * Deliberately narrower than "clear the selection whenever the filter moves":
   * a hand-assembled selection survives filtering, because narrowing a column to
   * find the next row to tick is how one gets built (see the prune effect). But
   * "all 35,690 matching" cannot survive the filter that defined it changing —
   * silently keeping those ids would leave a bulk action pointed at rows the
   * table is no longer showing, under a caption still reading "all matching".
   */
  const matchKeyRef = useRef(matchKey);
  const selReqRef = useRef(0);
  useEffect(() => {
    if (matchKeyRef.current === matchKey) return;
    matchKeyRef.current = matchKey;
    // Abandons any select-all still in flight: its answer describes the previous
    // query, and applying it here is exactly the bug this effect exists to stop.
    // The busy flag has to be released HERE as well as in selectEverything's
    // finally, because that finally deliberately leaves the state of an
    // abandoned request alone — without this the spinner would run forever and
    // the checkbox would stay disabled.
    selReqRef.current += 1;
    setSelBusy(false);
    setSelError('');
    // Read straight from state rather than from inside a setSelAll updater: an
    // updater must stay pure, and StrictMode invokes it twice.
    if (selAll) { setSel(new Set()); setSelAll(false); }
  }, [matchKey, selAll]);

  const serverState = useServerRows({
    resource: serverMode ? server.resource : null,
    page,
    pageSize,
    ordering,
    // Refuse to send an oversized spec: gunicorn answers 414 before Django sees
    // the request, so there is no error body to show the user.
    filterSpec: specTooLarge ? null : specJson,
    search: q || null,
    paramsJson,
    enabled: serverMode,
    hasStoredSpec,
  });

  useEffect(() => { if (serverState.schema) setSchemaForSplit(serverState.schema); }, [serverState.schema]);

  /**
   * Raw API rows mapped into the shape the columns read.
   *
   * REQUIRED for any resource whose api module has a toFrontend(): in-memory mode
   * receives rows that have already been through it, but server mode gets them
   * straight off the wire. Without this, a column keyed on a mapped name renders
   * blank — and worse, a key that exists under BOTH names silently renders the
   * wrong one. Verified on Bookings: `name` and `company_name` came out empty,
   * and `payment_status` displayed the INVOICE value because the serializer also
   * exposes that name, instead of the resolved person-level
   * `effective_payment_status`. That is precisely the correctness bug the
   * server-side work exists to prevent, reintroduced one layer down.
   */
  const mapServerRows = useCallback(
    (list) => (server && server.mapRow ? list.map(server.mapRow) : list),
    // mapRow is a module-level function in every caller, so its identity is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [server && server.mapRow],
  );

  // In server mode, infinite scroll accumulates fetched pages instead of
  // discarding the previous one. Reset whenever the QUERY changes — a new filter
  // or sort makes previously accumulated rows wrong, not merely stale.
  // paramsJson is part of the key: changing the date range makes every
  // accumulated page wrong, not stale, exactly as a new filter does. Leaving it
  // out would keep the previous window's rows on screen and append the new
  // window's page 2 underneath them.
  const fetchKey = `${specJson || ''}|${ordering || ''}|${q}|${pageSize}|${paramsJson || ''}`;
  const [acc, setAcc] = useState([]);
  const lastAppliedRef = useRef('');
  /**
   * The count a background reload found, while it is the most recent answer to
   * this query — see liveReload below, which fetches a whole span at once and so
   * gets a count useServerRows never sees. Null means "no background answer
   * newer than the paged one", which is the state after every ordinary fetch.
   */
  const [liveTotal, setLiveTotal] = useState(null);
  useEffect(() => {
    if (!serverMode || !infinite) return;
    setAcc([]);
    setPage(1);
    setLiveTotal(null);
    lastAppliedRef.current = '';
  }, [serverMode, infinite, fetchKey]);
  // Keyed on the page the ROWS came from, never on the `page` we asked for.
  // useServerRows sets `loading` from inside an effect, so in the render that
  // advances `page` this component still sees loading=false next to the PREVIOUS
  // page's rows — appending on that combination appended page 1 a second time and
  // then ran one page behind for the rest of the scroll. rowsPage is null until
  // rows for the current query and page have actually landed.
  const dataPage = serverState.rowsPage;
  useEffect(() => {
    if (!serverMode || !infinite || !dataPage) return;
    const stamp = `${fetchKey}#${dataPage}`;
    if (lastAppliedRef.current === stamp) return;
    lastAppliedRef.current = stamp;
    const incoming = mapServerRows(serverState.rows);
    setAcc((prev) => (dataPage <= 1 ? incoming : [...prev, ...incoming]));
    // A page that has genuinely just landed carries a fresher count than any
    // earlier background reload, so that one stops overriding it. Without this,
    // one background reload would pin the footer's total for as long as the query
    // stayed put, and scrolling on would report the count from minutes ago.
    setLiveTotal(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverMode, infinite, dataPage, serverState.rows, fetchKey, mapServerRows]);

  // ── Staying current ───────────────────────────────────────────────────────
  /**
   * REFRESH AFTER A WRITE — what a parent gets through onServerReady.
   *
   * `serverState.refetch` alone was handed out here, and in infinite mode it did
   * NOTHING VISIBLE. The accumulator drops any response whose
   * `${fetchKey}#${page}` stamp it has already applied, which is precisely what a
   * re-fetch of the same page is, so the new rows were fetched and then discarded.
   * Bookings and Ticket Central are both infinite: creating a booking, marking a
   * row paid, importing a spreadsheet, or pressing the Refresh button left the
   * table showing pre-write rows until the user pressed F5 — which is exactly the
   * "it only shows up after a refresh" complaint, and it was not the request
   * failing, it was the answer being thrown away.
   *
   * Clearing the stamp and returning to page 1 is the right reset for a write:
   * every table here sorts newest-first by default, so the record just created is
   * at the top of the page the user is put back on.
   */
  const resetAccumulation = useCallback(() => {
    lastAppliedRef.current = '';
    setAcc([]);
    setPage(1);
  }, []);

  const liveResources = serverMode
    ? [server.resource, ...(server.live || [])]
    : null;

  /**
   * BACKGROUND REFRESH — a poll, or someone else's write arriving.
   *
   * Not the same operation as the one above, and the difference is the whole
   * reason there are two. Resetting to page 1 under a user who has scrolled to
   * row 300 would throw away their position for a change they did not make; that
   * is worse behaviour than being briefly stale.
   *
   * So in infinite mode this re-fetches EVERYTHING already on screen as one
   * request — page 1 at a page_size covering the whole accumulated span — and
   * swaps it in. Nothing moves: same rows in the same order, with new values and
   * any new arrivals at the top. Above MAX_LIVE_SPAN a single request can no
   * longer hold the span, and shrinking the list to fit would delete rows from
   * under the reader, so it stands down instead.
   */
  // Destructured, not held as the returned object: that object is rebuilt every
  // render, so a `refreshRows` depending on it would have a new identity every
  // render, and the onServerReady effect below would re-register on each one —
  // which is a parent setState per render, in other words a render loop. The two
  // functions inside are stable.
  const { markRefreshed } = useLiveData(
    useCallback(() => {
      if (!serverMode) return;
      if (!infinite) { serverState.refetch({ quiet: true }); return; }
      const span = page * pageSize;
      if (span > MAX_LIVE_SPAN) return;
      fetchPage(server.resource, {
        page: 1,
        pageSize: span,
        ordering,
        filterSpec: specTooLarge ? null : specJson,
        search: q || null,
        // Load-bearing: this path re-fetches the whole scrolled span itself
        // rather than going through useServerRows, so omitting the params here
        // would make a background refresh silently replace a windowed table with
        // unfiltered rows — the filter would appear to switch itself off after a
        // poll, which is worse than not refreshing at all.
        params: paramsJson ? JSON.parse(paramsJson) : undefined,
      })
        .then((res) => {
          setAcc(mapServerRows(res.results));
          setLiveTotal(res.count);
        })
        // Silent by design: a failed background refresh must leave the rows on
        // screen alone and say nothing. The user did not ask for this fetch.
        .catch(() => {});
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [serverMode, infinite, page, pageSize, ordering, specJson, specTooLarge, q, paramsJson, mapServerRows, serverState.refetch, server && server.resource]),
    {
      resources: liveResources,
      enabled: serverMode && live !== false,
      poll: typeof live === 'number' ? live : undefined,
    },
  );

  const refreshRows = useCallback(() => {
    resetAccumulation();
    serverState.refetch();
    // The write that prompted this is about to arrive over the bus as well;
    // stamping the clock stops that echo fetching the same page a second time.
    markRefreshed();
    // serverState.refetch, NOT serverState — which is what the exhaustive-deps
    // rule asks for and cannot be given. That object is rebuilt every render, so
    // depending on it would give this callback a new identity every render, the
    // effect below would re-register on each one, and each registration is a
    // setState in the parent: a render loop. `refetch` itself is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetAccumulation, serverState.refetch, markRefreshed]);

  useEffect(() => { if (onServerReady) onServerReady(refreshRows); }, [onServerReady, refreshRows]);

  // ── The rows actually rendered ────────────────────────────────────────────
  // acc is already mapped by the accumulate effect; the non-infinite path maps here.
  const sourceRows = serverMode ? (infinite ? acc : mapServerRows(serverState.rows)) : rows;

  /**
   * Selected ids that no longer exist stop being selected.
   *
   * Nothing pruned `sel`, so a selection outlived the rows it named. Clear all
   * data was the clearest way to see it: tick some rows, wipe the module, and the
   * table empties while the bulk bar stays put above it announcing "12 selected"
   * over nothing. Every button on that bar was live, so Delete or Mark paid could
   * still be pressed, sending ids the database no longer had. Only F5 cleared it,
   * because only a remount discarded the state — the same "it needs a refresh"
   * shape as the stale rows, one component up.
   *
   * Pruned against the LOADED rows, not the filtered ones, so typing in the search
   * box or narrowing a column does not silently drop a selection the user is still
   * assembling; a row hidden by a filter is still loaded. A row that has left the
   * loaded set is either deleted or filtered out server-side, and in both cases a
   * bulk action on it would be acting blind.
   *
   * Never mid-fetch: rows are empty for a moment during a page or filter change,
   * and pruning there would clear the selection on every one of them.
   *
   * Never against a whole-set selection either. `selAll` holds ids resolved by
   * the server across every page, so all but the ~50 on screen are legitimately
   * absent from the loaded rows; pruning would cut a 35,690-row selection to one
   * page and report it as if the user had asked for that. What retires such a
   * selection instead is the query changing, handled by the matchKey effect
   * above, which is the only event that actually invalidates it.
   */
  useEffect(() => {
    if (!sel.size || selAll) return;
    if (serverMode && serverState.loading) return;
    const present = new Set((sourceRows || []).map((r) => r.id));
    setSel((prev) => {
      const next = new Set();
      prev.forEach((id) => { if (present.has(id)) next.add(id); });
      // Same Set back when nothing was dropped: a new one every render would be a
      // fresh state value each time and this effect would never settle.
      return next.size === prev.size ? prev : next;
    });
  }, [sel, selAll, sourceRows, serverMode, serverState.loading]);

  const data = useMemo(() => {
    let d = sourceRows || [];
    if (scope) d = d.filter(scope);
    if (serverMode) {
      // Server already applied `search` and every mapped criterion. Only the
      // conditions it could not express are re-applied here, against the page
      // that came back.
      split.clientConds.forEach((cond) => { if (condActive(cond)) d = d.filter((r) => condPasses(r, cond)); });
      // Ordering the server understood is already applied; a column that sorts
      // only locally is sorted here.
      if (sort && !ordering) d = sortLocally(d, sort);
      return d;
    }
    if (q) { const v = q.toLowerCase(); d = d.filter((r) => cols.some((c) => String(r[c.key] == null ? '' : r[c.key]).toLowerCase().includes(v))); }
    conds.forEach((cond) => { if (condActive(cond)) d = d.filter((r) => condPasses(r, cond)); });
    if (sort) d = sortLocally(d, sort);
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceRows, scope, q, conds, sort, serverMode, split.clientConds, ordering]);

  // Total the footer reports. In server mode that is the server's `count` —
  // except when a client-only condition is also narrowing the page, where the
  // true total is unknowable without fetching everything, so the count shown is
  // explicitly labelled as counting loaded rows.
  const clientNarrowed = serverMode && split.clientConds.some(condActive);
  // liveTotal first when a background reload has one: it is the more recent
  // answer to the same query, and leaving the older count in place would show
  // "Showing 60 of 50" the moment ten new rows arrived.
  const total = serverMode && !clientNarrowed
    ? (liveTotal === null ? serverState.count : liveTotal)
    : data.length;

  const totalPages = serverMode && !clientNarrowed
    ? Math.max(1, serverState.totalPages)
    : Math.max(1, Math.ceil(data.length / pageSize));
  const curPage = serverMode ? page : Math.min(page, totalPages);
  const pageRows = serverMode
    ? data
    : infinite ? data.slice(0, shown) : data.slice((curPage - 1) * pageSize, curPage * pageSize);

  const loadedCount = serverMode && infinite ? data.length : Math.min(shown, data.length);

  // ── Load on scroll (infinite mode) ────────────────────────────────────────
  // A sentinel sits directly below the last row; reaching it fetches the next
  // page. The footer's "Load N more" button stays alongside it: it is the
  // keyboard path, and the only way forward if an intersection is never
  // delivered — a fetch that errored, or a sentinel the user never reaches.
  //
  // The sentinel NODE is kept in state rather than a ref, because it mounts and
  // unmounts as the table moves between its rows / empty / cards branches. An
  // effect keyed on a ref would go on observing whichever node was current when
  // it last ran, which shows up as scrolling loading one more page and then
  // going quiet; a callback ref re-runs the effect on every node swap.
  const [sentinelEl, setSentinelEl] = useState(null);
  const scrollBoxRef = useRef(null);

  const loadMore = useCallback(() => {
    if (!serverMode) { setShown((s) => s + pageSize); return; }
    // Advance only when the rows on screen ARE the last page requested.
    // Intersection callbacks can arrive twice before `loading` flips true, and
    // setPage(p => p + 1) landing twice would step 1 → 3, skipping a whole page
    // of records rather than merely double-fetching. lastAppliedRef holds the
    // stamp of the page the accumulator has actually appended, so a second call
    // in the same window finds `#2` pending against `#1` applied and is dropped.
    if (lastAppliedRef.current !== `${fetchKey}#${page}`) return;
    setPage((p) => p + 1);
  }, [serverMode, pageSize, fetchKey, page]);

  // Not while a fetch is in flight, and not after an error — otherwise the
  // observer re-fires against the same failed page for as long as the sentinel
  // stays on screen.
  const canLoadMore = infinite && loadedCount < total
    && !(serverMode && (serverState.loading || serverState.error));

  useEffect(() => {
    if (!sentinelEl || !canLoadMore || typeof IntersectionObserver === 'undefined') return undefined;
    // Root at whatever actually scrolls. `.tsc` is its own scroll box at desktop
    // widths (max-height: calc(100vh - 300px)) but max-height:none under 880px,
    // where the PAGE scrolls instead. This matters because rootMargin is measured
    // against the ROOT's rect: with a viewport root the margin buys nothing while
    // the sentinel is clipped inside an inner scroller, so the next page would
    // only start loading once the user had already hit the bottom.
    const box = scrollBoxRef.current;
    const root = box && box.scrollHeight > box.clientHeight + 1 ? box : null;
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) loadMore(); },
      { root, rootMargin: '260px 0px' },
    );
    io.observe(sentinelEl);
    return () => io.disconnect();
    // loadedCount: each appended page moves the sentinel and can change whether
    // the box scrolls at all, so the root is re-derived rather than assumed.
  }, [sentinelEl, canLoadMore, loadMore, loadedCount]);

  function resetPaging() { setPage(1); setShown(pageSize); }
  function toggleRow(id) {
    // Un-ticking one row out of "all 35,690" leaves a selection that is no longer
    // the whole match, so the flag goes with it and the caption stops claiming it.
    if (selAll) setSelAll(false);
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  function clearSelection() { setSel(new Set()); setSelAll(false); setSelError(''); }

  /**
   * Select EVERY row the current query matches, not the page.
   *
   * In-memory tables already hold the whole match in `data`, so this is a local
   * operation. Server-mode tables hold one page of it and have to ask: the
   * server resolves the same filter_queryset() the list endpoint uses and hands
   * back bare ids (accounts/filter_spec.py, the `ids` action).
   *
   * Two cases keep it local even in server mode, and both are the same reason —
   * the server cannot resolve what it was never sent:
   *
   *   • clientNarrowed — a column filter the backend has no term for is applied
   *     here, against the loaded page. Asking the server for "all matching"
   *     would return rows this table is deliberately hiding.
   *   • specTooLarge — the spec is past what a URL can carry, so it was not sent
   *     at all and the server is answering an unfiltered question.
   *
   * In both, `data` is the honest answer to "everything you can see", and the
   * caption stays a subset caption rather than claiming the whole set.
   */
  async function selectEverything() {
    setSelError('');
    const localOnly = !serverMode || clientNarrowed || specTooLarge;
    if (localOnly) {
      setSel(new Set(data.map((r) => r.id)));
      setSelAll(false);
      return;
    }

    // Tick the page up front so the click always does something visible, then
    // widen when the ids land. A select-all that sits inert for a second reads
    // as a dead checkbox and gets clicked again.
    setSel(new Set(pageRows.map((r) => r.id)));
    const token = ++selReqRef.current;
    setSelBusy(true);
    try {
      const { ids } = await fetchAllIds(server.resource, {
        filterSpec: specJson,
        search: q || null,
        params: paramsJson ? JSON.parse(paramsJson) : undefined,
      });
      // The query moved while this was in flight — these ids answer a filter the
      // user has already left, so they are dropped rather than applied.
      if (selReqRef.current !== token) return;
      setSel(new Set(ids));
      setSelAll(true);
    } catch (err) {
      if (selReqRef.current !== token) return;
      // The backend's own 400 when the match is past select_all_max names the
      // count and the ceiling, which is more use than anything phrased here.
      setSelError(apiErrorMessage(err, `Could not select all ${noun}.`));
    } finally {
      if (selReqRef.current === token) setSelBusy(false);
    }
  }

  function toggleAll() {
    if (sel.size) clearSelection();
    else selectEverything();
  }
  function clearAll() { setConds([]); setQ(''); resetPaging(); }
  function addCond(col) { setConds((cs) => [...cs, { key: col.key, op: 'Contains', values: [] }]); resetPaging(); }
  function updateCond(key, next) { setConds((cs) => cs.map((c) => (c.key === key ? next : c))); resetPaging(); }
  function removeCond(key) { setConds((cs) => cs.filter((c) => c.key !== key)); resetPaging(); }
  function setColFilter(key, next) {
    setConds((cs) => (cs.some((c) => c.key === key) ? cs.map((c) => (c.key === key ? next : c)) : [...cs, next]));
    resetPaging();
  }
  // asc → desc → off. Cycling back to off restores the table's default order
  // rather than leaving the last direction stuck on.
  const cycleSort = useCallback((key) => {
    setSort((s) => {
      if (!s || s.key !== key) return { key, dir: 'asc' };
      if (s.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
    resetPaging();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageSize]);

  const pageList = () => {
    const tp = totalPages, p = curPage, n = [];
    if (tp <= 7) { for (let i = 1; i <= tp; i++) n.push(i); return n; }
    n.push(1); if (p > 3) n.push('…');
    for (let i = Math.max(2, p - 1); i <= Math.min(tp - 1, p + 1); i++) n.push(i);
    if (p < tp - 2) n.push('…'); n.push(tp);
    return n;
  };

  const activeCondCount = conds.filter(condActive).length;
  const isFiltered = activeCondCount > 0 || !!q;
  const nounCap = noun.charAt(0).toUpperCase() + noun.slice(1);
  const emptyState = isFiltered ? (
    <EmptyState icon="filter" title="No matching records found" body={`No ${noun} match your current search or filters.`}
      action={<button className="btn btn-s btn-sm" onClick={clearAll}><Icon name="refresh" size={13} />Clear filters</button>} />
  ) : (
    <EmptyState icon="inbox" title={`No ${nounCap} Found`} body={`There are no ${noun} to display yet.`} />
  );

  /**
   * The scroll sentinel, and the progress line the user reads while it works.
   *
   * An ELEMENT rather than a `<MoreBar />` component (the way Footer is written):
   * a function component declared in this body has a new identity every render,
   * so React would tear the div down and rebuild it on each one, re-running the
   * observer setup continuously for no reason.
   *
   * Rendered only while rows remain: a sentinel left in place on a fully-loaded
   * table sits inside the observer's rootMargin and fires on every scroll.
   * aria-live because rows appearing on scroll is otherwise a silent DOM append.
   */
  const moreBar = infinite && loadedCount < total ? (
    <div className="more" ref={setSentinelEl} aria-live="polite">
      {serverMode && serverState.loading
        ? <><span className="spin" />Loading more {noun}…</>
        : <>Loaded <b>{nf(loadedCount)}</b> of {nf(total)} — keep scrolling for more</>}
    </div>
  ) : null;

  return (
    <>
      <div className="tb">
        <div className="tb-s"><input className="in in-s" placeholder={searchPlaceholder} value={q} onChange={(e) => { setQ(e.target.value); resetPaging(); }} /></div>
        <Popover trigger={({ toggle }) => <button className="btn btn-s btn-sm" onClick={toggle}><Icon name="cols" size={13} />Columns</button>}>
          {() => (
            <>
              <div className="pop-t">Visible columns</div>
              <div className="pop-mx">
                {(groups || [{ key: null, label: null }]).map((g) => (
                  <div key={g.key || 'x'}>
                    {g.label ? <div className="pop-sec">{g.label}</div> : null}
                    {cols.filter((c) => (groups ? c.group === g.key : true)).map((c) => (
                      <label className="pop-i" key={c.key}>
                        <input type="checkbox" checked={!hidden.has(c.key)} onChange={() => {
                          setHidden((h) => {
                            const n = new Set(h);
                            if (n.has(c.key)) n.delete(c.key);
                            else { if (cols.length - n.size <= 2) return h; n.add(c.key); }
                            return n;
                          });
                        }} />
                        {c.label}
                      </label>
                    ))}
                  </div>
                ))}
              </div>
              <div className="pop-f"><button className="btn btn-g btn-sm" onClick={() => setHidden(new Set())}>Show all</button></div>
            </>
          )}
        </Popover>
        <div className="tb-sp" />
        {extraToolbar}
        {card ? (
          <Seg options={[{ value: 'table', icon: 'list', label: 'Table' }, { value: 'cards', icon: 'grid', label: 'Cards' }]} value={view} onChange={setView} />
        ) : null}
        <Popover width={400} align="right" panelClassName="pop-lg" trigger={({ toggle }) => <button className={'btn btn-s btn-sm' + (activeCondCount ? ' on' : '')} onClick={toggle}><Icon name="filter" size={13} />Filter{activeCondCount ? ` (${activeCondCount})` : ''}</button>}>
          {({ close }) => (
            <>
              <div className="pop-hd"><h3>Search</h3><button type="button" className="pop-x" onClick={close} aria-label="Close"><Icon name="x" size={16} /></button></div>
              <div className="pop-mx flt-list">
                {cols.map((c) => {
                  const cond = conds.find((cd) => cd.key === c.key);
                  return (
                    <FilterListItem key={c.key} col={c} cond={cond}
                      onToggle={() => (cond ? removeCond(c.key) : addCond(c))}
                      onChangeCond={(next) => updateCond(c.key, next)}
                    />
                  );
                })}
              </div>
              <div className="pop-search-f"><button type="button" className="btn btn-p btn-pill" onClick={close}>Search</button></div>
            </>
          )}
        </Popover>
      </div>

      {serverMode && serverState.error ? (
        <div className="vr er" style={{ marginBottom: 10 }}>
          <Icon name="warn" size={15} /><span>{serverState.error}</span>
        </div>
      ) : null}
      {serverMode && specTooLarge ? (
        <div className="vr er" style={{ marginBottom: 10 }}>
          <Icon name="warn" size={15} />
          <span>Too many filter values to send to the server ({nf(specBytes)} of {nf(MAX_SPEC_BYTES)} characters). Remove some values — the list below is unfiltered.</span>
        </div>
      ) : null}
      {serverMode && serverState.schemaFailed ? (
        <div className="vr wn" style={{ marginBottom: 10 }}>
          <Icon name="warn" size={15} />
          <span>Server-side filtering is unavailable, so this list is not filtered by the server. Filters below narrow only the rows already loaded.</span>
        </div>
      ) : null}
      {serverMode && clientNarrowed ? (
        <div className="vr wn" style={{ marginBottom: 10 }}>
          <Icon name="info" size={15} />
          <span>
            {split.unsupported.map((u) => u.key).join(', ')} {split.unsupported.length === 1 ? 'is' : 'are'} filtered
            in the browser, so the count below counts loaded rows only — not every matching record.
          </span>
        </div>
      ) : null}

      {(activeCondCount > 0 || q) && (
        <div className="fch">
          {q ? <span className="fc"><span className="k">search</span><span className="fc-txt">{q}</span><button onClick={() => setQ('')} aria-label="Clear search"><Icon name="x" size={11} /></button></span> : null}
          {conds.filter(condActive).map((cond) => {
            const c = cols.find((x) => x.key === cond.key);
            const text = NO_VALUE_OPS.includes(cond.op) ? opLabel(cond.op) : `${opLabel(cond.op)} ${fmtValues(cond.values)}`;
            const local = serverMode && split.unsupported.some((u) => u.key === cond.key);
            return (
              <span className={'fc' + (local ? ' fc-local' : '')} key={cond.key} title={`${c ? c.label : cond.key} ${text}${local ? ' — filtered in the browser' : ''}`}>
                <span className="k">{c ? c.label : cond.key}</span><span className="fc-txt">{text}</span>
                <button onClick={() => removeCond(cond.key)} aria-label="Remove filter"><Icon name="x" size={11} /></button>
              </span>
            );
          })}
          <button className="fcl" onClick={clearAll}>Clear all</button>
        </div>
      )}

      {view === 'cards' && card ? (
        <>
          {pageRows.length ? <div className="cg">{pageRows.map((r) => <div key={r.id} onClick={() => onRow && onRow(r)}>{card(r)}</div>)}</div>
            : emptyState}
          {/* Cards scroll with the page, not inside .tsc — the sentinel still
              belongs directly under the last card so scrolling loads there too. */}
          {moreBar}
          <div className="tw" style={{ marginTop: 11 }}><Footer /></div>
        </>
      ) : pageRows.length ? (
        <div className="tw">
          <div className="tsc" ref={scrollBoxRef}>
            <table className="dt">
              <thead>
                <tr>
                  {select ? (
                    /* The header checkbox selects every MATCHING row, not the
                       page. It used to iterate pageRows, so on a filter matching
                       35,690 tickets it selected 50 and a mass update reached
                       0.1% of what was asked for. Checked whenever anything is
                       selected — clicking it again is the clear — because with
                       whole-set selection the useful second click is "none",
                       not "this page as well". */
                    <th className="ck"><input type="checkbox" className="ck"
                      checked={sel.size > 0}
                      ref={(el) => { if (el) el.indeterminate = sel.size > 0 && !selAll && total > sel.size; }}
                      onChange={toggleAll}
                      disabled={selBusy}
                      aria-label={sel.size > 0 ? 'Clear the selection' : `Select all ${total} matching ${noun}`}
                      title={sel.size > 0
                        ? 'Clears the selection'
                        : `Selects all ${nf(total)} matching ${noun}, not just this page`} /></th>
                  ) : null}
                  {activeCols.map((c) => {
                    if (c.sortable === false) return <th key={c.key} className={c.num ? 'num' : ''}>{c.label}</th>;
                    const cond = conds.find((cd) => cd.key === c.key);
                    // In server mode a column is sortable only if the backend has
                    // an ordering term for it. Sorting locally would reorder the
                    // fetched page while implying the whole table was sorted —
                    // worse than not offering it.
                    const canSort = !serverMode || !!c.serverOrdering;
                    return (
                      <HeaderCell key={c.key} col={c} cond={cond} sort={sort} canSort={canSort}
                        onSort={() => cycleSort(c.key)}
                        onChange={(next) => setColFilter(c.key, next)}
                        onRemove={() => removeCond(c.key)}
                      />
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r) => (
                  <tr key={r.id} className={sel.has(r.id) ? 'sel' : ''} onClick={() => onRow && onRow(r)} style={onRow ? { cursor: 'pointer' } : undefined}>
                    {select ? (
                      <td className="ck" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" className="ck" checked={sel.has(r.id)} onChange={() => toggleRow(r.id)} aria-label="Select row" />
                      </td>
                    ) : null}
                    {activeCols.map((c) => {
                      const v = r[c.key];
                      return (
                        <td key={c.key} className={(c.num ? 'num ' : '') + (c.cls || '')}>
                          {c.editOpts && canEdit ? <EditableCell row={r} col={c} value={v} /> : c.cell ? c.cell(v, r) : v == null || v === '' ? <span className="dim">—</span> : v}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {moreBar}
          </div>
          <Footer />
        </div>
      ) : serverMode && serverState.loading ? (
        <div className="tw"><div className="more"><span className="spin" />Loading {noun}…</div></div>
      ) : (
        <>
          {emptyState}
          <div className="tw" style={{ marginTop: 11 }}><Footer /></div>
        </>
      )}

      {/* C5 — the selection states what it actually covers, and the three states
          are genuinely different things to say. Rendered above bulkActions so the
          caveat is read before the button is pressed. */}
      {select && selBusy ? (
        <div className="hint" style={{ marginTop: 9 }}>
          <span className="spin" /> Selecting all {nf(total)} matching {noun}…
        </div>
      ) : null}

      {select && selError ? (
        <div className="vr er" style={{ marginTop: 9 }}>
          <Icon name="warn" size={15} />
          <span>{selError} The {nf(sel.size)} {noun} on this page are still selected.</span>
        </div>
      ) : null}

      {select && !selBusy && sel.size > 0 && total > sel.size ? (
        <div className="hint" style={{ marginTop: 9 }}>
          <b>{nf(sel.size)}</b> of <b>{nf(total)}</b> {noun} selected — actions below
          apply to those {nf(sel.size)} only.{' '}
          <button type="button" className="btn btn-s btn-sm" onClick={selectEverything}>
            Select all {nf(total)}
          </button>
        </div>
      ) : null}

      {/* selAll rather than sel.size === total: on a 12-row table those are the
          same number the moment every row is ticked by hand, and only one of the
          two was actually resolved server-side across every page. */}
      {select && !selBusy && selAll && sel.size > 0 ? (
        <div className="hint" style={{ marginTop: 9 }}>
          All <b>{nf(sel.size)}</b> matching {noun} selected — including{' '}
          {nf(Math.max(0, sel.size - pageRows.length))} not shown on this page.
        </div>
      ) : null}

      {select && sel.size > 0 && bulkActions
        ? bulkActions([...sel], { clear: clearSelection, total, loadedCount, allMatching: selAll })
        : null}
    </>
  );

  function Footer() {
    if (infinite) {
      const more = total - loadedCount;
      return (
        <div className="tf">
          <span>Showing <b>{nf(loadedCount)}</b> of <b>{nf(total)}</b> {noun}</span>
          {more > 0
            ? <button className="btn btn-s btn-sm" disabled={serverMode && serverState.loading} onClick={loadMore}>
                Load {nf(Math.min(pageSize, more))} more
              </button>
            : <span className="dim">All loaded</span>}
        </div>
      );
    }
    const from = total ? (curPage - 1) * pageSize + 1 : 0;
    const to = serverMode ? Math.min((curPage - 1) * pageSize + pageRows.length, total) : Math.min(curPage * pageSize, total);
    return (
      <div className="tf">
        <span>Showing <b>{nf(from)}–{nf(to)}</b> of <b>{nf(total)}</b> {noun}</span>
        <div className="pgr">
          <button className="pgb" disabled={curPage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))} aria-label="Previous"><Icon name="chevL" size={13} /></button>
          {pageList().map((p, i) => p === '…' ? <span className="pge" key={'e' + i}>…</span> : <button key={p} className={'pgb' + (p === curPage ? ' on' : '')} onClick={() => setPage(p)}>{p}</button>)}
          <button className="pgb" disabled={curPage >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))} aria-label="Next"><Icon name="chevR" size={13} /></button>
        </div>
      </div>
    );
  }
}

function sortLocally(rows, sort) {
  const { key, dir } = sort, m = dir === 'asc' ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = a[key], y = b[key];
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * m;
    return String(x == null ? '' : x).localeCompare(String(y == null ? '' : y)) * m;
  });
}
