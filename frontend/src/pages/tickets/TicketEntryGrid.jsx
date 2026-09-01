import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../lib/icons';
import { TK_PRIORITY, TK_RELATIONSHIPS, TK_TYPES } from '../../lib/constants';
import { fmy } from '../../lib/helpers';
import * as ticketsApi from '../../api/tickets';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';

/**
 * Ticket Central's inline entry grid — a spreadsheet, not a form.
 *
 * Raising twenty tickets for one event through the single-ticket modal meant
 * twenty round trips through a dialog, re-typing the same organizer, month,
 * location and purpose each time. Here every field is a cell, a new row inherits
 * the row above, and the spreadsheet keys people already own do the work:
 * Ctrl+D / Ctrl+R to fill, Tab and Enter to advance, Ctrl+V to drop in a block
 * pasted straight out of Excel or Sheets.
 *
 * Three things about the shape of this file:
 *
 *  · The grid is ONE <table> re-rendered from `rows`. Cells are plain <td>s; the
 *    selection rectangle and the fill handle are absolutely-positioned overlays
 *    measured off the DOM in a layout effect, because drawing a range border
 *    with cell borders shifts the layout by a pixel per edge and the columns
 *    visibly jitter as you move the selection.
 *  · While a cell is being edited the <input> is UNCONTROLLED and its draft
 *    lives in the CellEditor child. Keystrokes therefore re-render one cell, not
 *    the grid; `rows` only changes when an edit is committed.
 *  · Ticket numbers are never typed. The number column is read-only and shows
 *    the prefix the backend will build (type code + purpose); the number itself
 *    comes from the per-purpose sequence in ticket_central/utils.py at save.
 */

// ── Columns ─────────────────────────────────────────────────────────────────
//
// Exactly the MR half of a ticket — ticket_central/constants.MR_FIELDS — in the
// order somebody actually types it: what the ticket is, then which event, then
// the notes. `carry` marks the fields a new row inherits: the classification and
// the event repeat down a batch, while the link, its keywords and the comment
// are what makes each row a different ticket and must start empty.
const NUM_KEYS = new Set(['estimate']);

const COLS = [
  { k: 'link_url', t: 'Link URL', w: 250, kind: 'text', req: true, ph: 'https://', cell: (v) => v },
  { k: 'linkedin_keywords', t: 'LinkedIn Keywords', w: 170, kind: 'text' },
  { k: 'type_of_ticket', t: 'Type of Ticket', w: 138, kind: 'pick', opts: TK_TYPES, carry: true, req: true },
  { k: 'purpose', t: 'Purpose', w: 100, kind: 'pick', free: true, mono: true, carry: true, req: true },
  { k: 'ticket_number', t: 'Ticket Number', w: 134, kind: 'auto' },
  { k: 'priority', t: 'Priority', w: 100, kind: 'pick', opts: Object.keys(TK_PRIORITY), carry: true, tone: (v) => TK_PRIORITY[v] || 'neutral' },
  { k: 'estimate', t: 'Estimate', w: 92, kind: 'num' },
  { k: 'organizer', t: 'Organizer', w: 166, kind: 'text', carry: true },
  { k: 'competitor_event_name', t: 'Competitor Event', w: 172, kind: 'text', carry: true },
  { k: 'event_month_year', t: 'Event Month/Year', w: 140, kind: 'month', carry: true, cell: (v) => (v ? fmy(v) : '') },
  { k: 'event_location', t: 'Event Location', w: 150, kind: 'text', carry: true },
  { k: 'relationship', t: 'Relationship', w: 116, kind: 'pick', opts: TK_RELATIONSHIPS, carry: true, tone: () => 'neutral' },
  { k: 'mr_comments', t: 'MR Comments', w: 200, kind: 'text' },
  { k: 'assigned_mr', t: 'Assigned MR', w: 200, kind: 'pick', free: true, carry: true },
];
const NC = COLS.length;

// Mirrors ticket_central/utils.extract_type_code: the segment after the last
// dash, or the whole string when there is none. 'White - WH' → 'WH'.
const typeCode = (v) => {
  const s = (v || '').trim();
  if (!s) return '';
  return s.includes('-') ? s.split('-').pop().trim() : s;
};

// Mirrors utils.normalize_link. Kept in step so the grid's own within-batch
// check calls a repeat the same way the server does; the server is still the
// authority, this only avoids a round trip to say what is already obvious.
function normalizeLink(url) {
  let s = (url || '').trim().toLowerCase();
  if (!s) return '';
  for (const scheme of ['https://', 'http://', '//']) {
    if (s.startsWith(scheme)) { s = s.slice(scheme.length); break; }
  }
  if (s.startsWith('www.')) s = s.slice(4);
  return s.replace(/\/+$/, '');
}

let uid = 0;
const blank = () => COLS.reduce((v, c) => ({ ...v, [c.k]: '' }), {});

/** A new row, inheriting the carry-forward fields of the one above it. */
function mkRow(prev, carryOn) {
  const row = { key: `r${++uid}`, v: blank(), carry: {} };
  if (prev && carryOn) {
    COLS.forEach((c) => {
      if (c.carry && prev.v[c.k]) { row.v[c.k] = prev.v[c.k]; row.carry[c.k] = true; }
    });
  }
  return row;
}

/**
 * A row holding nothing but inherited values has not been started.
 *
 * This matters more than it looks. Carry-forward pre-fills the next row, so
 * without this every batch would end with a half-populated row that counts
 * itself as a ticket, fails validation for the link it does not have, and blocks
 * the save. A row becomes real when somebody types something of its own.
 */
const notStarted = (row) => COLS.every((c) => !(row.v[c.k] || '').trim() || row.carry[c.k]);

/** Two source values a constant step apart continue as a series; else repeat. */
function series(src, n) {
  const out = [];
  const nums = src.map((v) => (/^-?\d+$/.test((v || '').trim()) ? parseInt(v, 10) : null));
  if (src.length > 1 && nums.every((x) => x !== null)) {
    const step = nums[1] - nums[0];
    const even = nums.every((x, i) => i === 0 || x - nums[i - 1] === step);
    if (even && step !== 0) {
      let last = nums[nums.length - 1];
      for (let i = 0; i < n; i += 1) { last += step; out.push(String(last)); }
      return out;
    }
  }
  for (let i = 0; i < n; i += 1) out.push(src[i % src.length]);
  return out;
}

// ── In-cell editor ──────────────────────────────────────────────────────────

/**
 * The editor for one cell: an uncontrolled input, plus a filtered option list
 * for the picklist columns.
 *
 * `free` columns (purpose, assigned_mr) accept a value that is not on the list —
 * those are plain CharFields holding Zoho text, and refusing an unlisted code
 * would make the grid unable to enter a purpose the team has just started using.
 */
function CellEditor({ col, initial, options, seeded, onCommit, onCancel }) {
  const ref = useRef(null);
  const [q, setQ] = useState(initial || '');
  const [ix, setIx] = useState(0);
  // Every exit path funnels through here. Picking an option commits and unmounts
  // the editor, and the input's own blur then fires on the way out — without
  // this latch that second commit would write the half-typed text back over the
  // option just chosen.
  const settled = useRef(false);
  const finish = (val, dir) => {
    if (settled.current) return;
    settled.current = true;
    onCommit(val, dir);
  };

  const list = useMemo(() => {
    if (col.kind !== 'pick') return [];
    const needle = (q || '').trim().toLowerCase();
    return (options || [])
      .filter((o) => !needle || o.toLowerCase().includes(needle))
      .slice(0, 60);
  }, [col.kind, options, q]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    if (!el.value) return;
    // A seeded keystroke leaves the caret after it, so typing continues; opening
    // a cell to edit selects the value, so typing replaces it. Both are what a
    // spreadsheet does.
    if (seeded) el.setSelectionRange(el.value.length, el.value.length);
    else el.select();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = () => (ref.current ? ref.current.value : '');

  /**
   * What Enter or Tab actually stores.
   *
   * Order matters. An emptied cell stays empty — without that first rule,
   * clearing a picklist and pressing Enter would silently store the first option
   * in the list, since an empty query filters nothing out. An exact typed match
   * beats the highlight so typing a full code and hitting Enter is never
   * surprising. Otherwise the highlighted option wins, and failing that the
   * typed text stands: purpose and Assigned MR are free CharFields, and refusing
   * an unlisted value would make a newly-agreed purpose code unenterable.
   */
  const take = (dir) => {
    const typed = value().trim();
    if (col.kind !== 'pick' || !typed) { finish(value(), dir); return; }
    const exact = (options || []).find((o) => o.toLowerCase() === typed.toLowerCase());
    if (exact) { finish(exact, dir); return; }
    if (list.length && ix >= 0 && list[ix] != null) { finish(list[ix], dir); return; }
    finish(value(), dir);
  };

  const key = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); settled.current = true; onCancel(); return; }
    if (col.kind === 'pick' && list.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      setIx((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length);
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); take(e.shiftKey ? 'up' : 'down'); return; }
    if (e.key === 'Tab') { e.preventDefault(); take(e.shiftKey ? 'left' : 'right'); return; }
    if (col.kind !== 'pick' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault(); take(e.key === 'ArrowDown' ? 'down' : 'up');
    }
  };

  return (
    <div className={'eg-ed' + (col.kind === 'num' ? ' n' : '') + (col.mono ? ' m' : '')}>
      <input
        ref={ref}
        type={col.kind === 'month' ? 'month' : 'text'}
        defaultValue={initial}
        placeholder={col.ph || ''}
        onChange={(e) => { setQ(e.target.value); setIx(0); }}
        onKeyDown={key}
        onBlur={() => finish(value(), null)}
      />
      {col.kind === 'pick' ? (
        <div className="eg-pop" role="listbox">
          {list.length ? list.map((o, i) => (
            <button
              type="button"
              key={o}
              className={'eg-opt' + (i === ix ? ' on' : '')}
              // mousedown with preventDefault, not click: a click would land
              // after the input's blur had already closed the editor.
              onMouseDown={(e) => { e.preventDefault(); finish(o, 'stay'); }}
            >
              {o}
              {col.k === 'type_of_ticket' ? <small>{typeCode(o)}</small> : null}
            </button>
          )) : (
            <div className="eg-none">{col.free ? 'Not on the list, Enter keeps it' : 'No match'}</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

// ── The grid ────────────────────────────────────────────────────────────────

export default function TicketEntryGrid({ onClose, onSaved }) {
  const { user } = useSession();
  const toast = useToast();

  const [rows, setRows] = useState(() => [mkRow(null, false)]);
  const [A, setA] = useState({ r: 0, c: 0 });
  const [B, setB] = useState({ r: 0, c: 0 });
  const [editing, setEditing] = useState(null);        // {r, c, seed}
  const [carryOn, setCarryOn] = useState(true);
  const [rowSel, setRowSel] = useState({});
  const [dups, setDups] = useState({});                // row index → server verdict
  const [serverErrs, setServerErrs] = useState({});    // row index → {field: msg}
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(null);              // created ticket numbers

  const hist = useRef({ past: [], future: [] });
  const wrapRef = useRef(null);
  const cvRef = useRef(null);
  const rboxRef = useRef(null);
  const aboxRef = useRef(null);
  const fboxRef = useRef(null);
  const dragRef = useRef(null);
  const fillRef = useRef(null);

  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: purposeRows } = useFetch(ticketsApi.purposes, [], { initialData: [] });

  const mrEmails = useMemo(
    () => (users || []).filter((u) => u.status === 'active' && u.email)
      .map((u) => u.email).sort((a, b) => a.localeCompare(b)),
    [users],
  );
  const purposeOpts = useMemo(
    () => (purposeRows || []).map((p) => p.purpose),
    [purposeRows],
  );
  const optionsFor = useCallback((col) => {
    if (col.k === 'purpose') return purposeOpts;
    if (col.k === 'assigned_mr') return mrEmails;
    return col.opts || [];
  }, [purposeOpts, mrEmails]);

  // ── Range helpers ─────────────────────────────────────────────────────
  const rng = useCallback(() => ({
    r1: Math.min(A.r, B.r), r2: Math.max(A.r, B.r),
    c1: Math.min(A.c, B.c), c2: Math.max(A.c, B.c),
  }), [A, B]);

  const focusGrid = () => wrapRef.current && wrapRef.current.focus();

  const go = useCallback((r, c, keep) => {
    const rr = Math.max(0, Math.min(rows.length - 1, r));
    const cc = Math.max(0, Math.min(NC - 1, c));
    setB({ r: rr, c: cc });
    if (!keep) { setA({ r: rr, c: cc }); setRowSel({}); }
  }, [rows.length]);

  // ── Mutation, with undo ───────────────────────────────────────────────
  const push = useCallback((next) => {
    setRows((cur) => {
      hist.current.past.push(cur);
      if (hist.current.past.length > 60) hist.current.past.shift();
      hist.current.future = [];
      return typeof next === 'function' ? next(cur) : next;
    });
    // A verdict is keyed by row index, so any structural change makes the
    // stored ones ambiguous. Clearing is honest; the effect below re-checks.
    setServerErrs({});
  }, []);

  const undo = useCallback(() => {
    const h = hist.current;
    if (!h.past.length) return;
    setRows((cur) => { h.future.push(cur); return h.past.pop(); });
    setServerErrs({});
  }, []);
  const redo = useCallback(() => {
    const h = hist.current;
    if (!h.future.length) return;
    setRows((cur) => { h.past.push(cur); return h.future.pop(); });
  }, []);

  const setCell = (draft, r, c, val) => {
    const col = COLS[c];
    if (!col || col.kind === 'auto' || !draft[r]) return;
    draft[r] = { ...draft[r], v: { ...draft[r].v, [col.k]: val == null ? '' : String(val) } };
    if (draft[r].carry[col.k]) {
      const carry = { ...draft[r].carry };
      delete carry[col.k];
      draft[r].carry = carry;
    }
  };

  const commitCell = (r, c, val) => {
    push((cur) => {
      const next = [...cur];
      setCell(next, r, c, val);
      return next;
    });
  };

  /**
   * Insert `n` rows and put the cursor on the first of them.
   *
   * `col` is explicit because the cursor cannot be moved with go() afterwards:
   * go() clamps against rows.length, which is still the pre-insert value inside
   * the same event handler, so it would clamp straight back to the old last row.
   */
  const addRows = useCallback((n, at, col = 0) => {
    push((cur) => {
      const next = [...cur];
      const start = at == null ? next.length : at;
      for (let i = 0; i < n; i += 1) {
        next.splice(start + i, 0, mkRow(next[start + i - 1] || null, carryOn));
      }
      return next;
    });
    const first = at == null ? rows.length : at;
    setA({ r: first, c: col });
    setB({ r: first, c: col });
  }, [push, carryOn, rows.length]);

  const delRows = useCallback(() => {
    const picked = Object.keys(rowSel).map(Number);
    const g = rng();
    const targets = picked.length ? picked : Array.from(
      { length: g.r2 - g.r1 + 1 }, (_, i) => g.r1 + i,
    );
    push((cur) => {
      const next = cur.filter((_, i) => !targets.includes(i));
      return next.length ? next : [mkRow(null, false)];
    });
    setRowSel({});
    const r = Math.max(0, Math.min(g.r1, rows.length - targets.length - 1));
    setA({ r, c: g.c1 }); setB({ r, c: g.c1 });
  }, [rowSel, rng, push, rows.length]);

  const clearRange = useCallback(() => {
    const g = rng();
    push((cur) => {
      const next = [...cur];
      for (let r = g.r1; r <= g.r2; r += 1) {
        for (let c = g.c1; c <= g.c2; c += 1) setCell(next, r, c, '');
      }
      return next;
    });
  }, [rng, push]);

  // Ctrl+D copies the TOP row of the selection down; a single cell pulls from
  // the row above. Ctrl+R is the same across columns. A series is only extended
  // by the drag handle, where the whole range is genuinely the pattern.
  const fillDown = useCallback(() => {
    const g = rng();
    const from = g.r1 === g.r2 ? g.r1 - 1 : g.r1;
    if (from < 0) return;
    push((cur) => {
      const next = [...cur];
      for (let c = g.c1; c <= g.c2; c += 1) {
        if (COLS[c].kind === 'auto') continue;
        const src = next[from].v[COLS[c].k];
        for (let r = from + 1; r <= g.r2; r += 1) setCell(next, r, c, src);
      }
      return next;
    });
    setA({ r: from, c: g.c1 }); setB({ r: g.r2, c: g.c2 });
  }, [rng, push]);

  const fillRight = useCallback(() => {
    const g = rng();
    const from = g.c1 === g.c2 ? g.c1 - 1 : g.c1;
    if (from < 0) return;
    push((cur) => {
      const next = [...cur];
      for (let r = g.r1; r <= g.r2; r += 1) {
        const src = next[r].v[COLS[from].k];
        for (let c = from + 1; c <= g.c2; c += 1) setCell(next, r, c, src);
      }
      return next;
    });
    setA({ r: g.r1, c: from }); setB({ r: g.r2, c: g.c2 });
  }, [rng, push]);

  const applyDragFill = useCallback((target) => {
    const g = rng();
    push((cur) => {
      const next = [...cur];
      if (target.axis === 'v') {
        const n = target.to - g.r2;
        for (let c = g.c1; c <= g.c2; c += 1) {
          if (COLS[c].kind === 'auto') continue;
          const src = [];
          for (let r = g.r1; r <= g.r2; r += 1) src.push(next[r].v[COLS[c].k]);
          const vals = series(src, n);
          for (let i = 0; i < n; i += 1) setCell(next, g.r2 + 1 + i, c, vals[i]);
        }
      } else {
        const n = target.to - g.c2;
        for (let r = g.r1; r <= g.r2; r += 1) {
          const src = [];
          for (let c = g.c1; c <= g.c2; c += 1) src.push(next[r].v[COLS[c].k]);
          const vals = series(src, n);
          for (let i = 0; i < n; i += 1) setCell(next, r, g.c2 + 1 + i, vals[i]);
        }
      }
      return next;
    });
    if (target.axis === 'v') setB({ r: target.to, c: g.c2 });
    else setB({ r: g.r2, c: target.to });
  }, [rng, push]);

  const pasteBlock = useCallback((text) => {
    const lines = text.replace(/\r\n|\r/g, '\n').split('\n');
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    if (!lines.length) return;
    const g = rng();
    push((cur) => {
      const next = [...cur];
      while (next.length < g.r1 + lines.length) {
        next.push(mkRow(next[next.length - 1] || null, false));
      }
      lines.forEach((line, i) => {
        line.split('\t').forEach((cellText, j) => {
          if (g.c1 + j < NC) setCell(next, g.r1 + i, g.c1 + j, cellText.trim());
        });
      });
      return next;
    });
    setB({
      r: g.r1 + lines.length - 1,
      c: Math.min(NC - 1, g.c1 + lines[0].split('\t').length - 1),
    });
    toast(`Pasted ${lines.length} ${lines.length === 1 ? 'row' : 'rows'}`, 'ok');
  }, [rng, push, toast]);

  const rangeTSV = useCallback(() => {
    const g = rng();
    const out = [];
    for (let r = g.r1; r <= g.r2; r += 1) {
      const line = [];
      for (let c = g.c1; c <= g.c2; c += 1) {
        line.push(COLS[c].kind === 'auto' ? numberPrefix(rows[r]) : rows[r].v[COLS[c].k]);
      }
      out.push(line.join('\t'));
    }
    return out.join('\n');
  }, [rng, rows]);

  // ── Repeated links ────────────────────────────────────────────────────
  //
  // Within the batch, computed here and instantly: the same link twice under the
  // same purpose is the likeliest duplicate of all, and it needs no round trip.
  const localDups = useMemo(() => {
    const seen = new Map();
    const out = {};
    rows.forEach((row, i) => {
      const key = normalizeLink(row.v.link_url);
      if (!key || notStarted(row)) return;
      const full = `${key}||${(row.v.purpose || '').trim().toUpperCase()}`;
      if (seen.has(full)) out[i] = { severity: 'block', firstRow: seen.get(full) + 1 };
      else seen.set(full, i);
    });
    return out;
  }, [rows]);

  // Against what is already stored: one request for the whole grid, debounced,
  // and only for rows that carry a link. `signature` keeps the effect from
  // firing on edits to any other column.
  const signature = useMemo(
    () => rows.map((r) => `${normalizeLink(r.v.link_url)}|${(r.v.purpose || '').trim().toUpperCase()}`).join('~'),
    [rows],
  );
  useEffect(() => {
    const pairs = rows.map((r) => ({ link_url: r.v.link_url || '', purpose: r.v.purpose || '' }));
    if (!pairs.some((p) => p.link_url.trim())) { setDups({}); return undefined; }
    let alive = true;
    const t = setTimeout(() => {
      ticketsApi.checkLinks(pairs).then((results) => {
        if (!alive) return;
        const out = {};
        results.forEach((res, i) => { if (res && res.severity) out[i] = res; });
        setDups(out);
      }).catch(() => { /* the save re-checks server-side; no need to shout here */ });
    }, 450);
    return () => { alive = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // ── Validation ────────────────────────────────────────────────────────
  const cellIssue = useCallback((r, c) => {
    const row = rows[r];
    const col = COLS[c];
    if (!row || notStarted(row)) return null;
    const val = (row.v[col.k] || '').trim();
    const srv = serverErrs[r] && serverErrs[r][col.k];
    if (srv) return { kind: 'bad', msg: Array.isArray(srv) ? srv.join(' ') : String(srv) };
    if (col.req && !val) return { kind: 'warn', msg: `${col.t} is needed before this ticket can be numbered` };
    if (col.k === 'estimate' && val && !/^\d+$/.test(val)) return { kind: 'bad', msg: 'Estimate takes a whole number' };
    if (col.k === 'estimate' && val && Number(val) === 0) return { kind: 'bad', msg: 'Estimate has to be above zero' };
    if (col.k === 'link_url' && val && !/^https?:\/\//i.test(val)) return { kind: 'bad', msg: 'A link starts with http or https' };
    if (col.k === 'link_url' && localDups[r]) {
      return { kind: 'bad', msg: `Same link and purpose as row ${localDups[r].firstRow} of this batch` };
    }
    if (col.k === 'link_url' && dups[r]) return dupIssue(dups[r]);
    // The purpose is half of what makes a repeat a repeat, so it carries the
    // same marker: this link was raised before, under THAT purpose.
    if (col.k === 'purpose' && dups[r]) return dupIssue(dups[r], true);
    return null;
  }, [rows, serverErrs, localDups, dups]);

  const rowIssues = useCallback((r) => {
    const out = [];
    for (let c = 0; c < NC; c += 1) {
      const issue = cellIssue(r, c);
      if (issue) out.push({ c, ...issue });
    }
    return out;
  }, [cellIssue]);

  const stats = useMemo(() => {
    let staged = 0; let bad = 0; let warn = 0; let firstMsg = '';
    rows.forEach((row, r) => {
      if (notStarted(row)) return;
      staged += 1;
      const issues = rowIssues(r);
      if (issues.some((i) => i.kind === 'bad')) {
        bad += 1;
        if (!firstMsg) firstMsg = `Row ${r + 1}. ${issues.find((i) => i.kind === 'bad').msg}`;
      } else if (issues.length) {
        warn += 1;
        if (!firstMsg) firstMsg = `Row ${r + 1}. ${issues[0].msg}`;
      }
    });
    return { staged, bad, warn, firstMsg };
  }, [rows, rowIssues]);

  const rowState = useCallback((r) => {
    if (notStarted(rows[r])) return '';
    const issues = rowIssues(r);
    if (!issues.length) return 'ok';
    return issues.some((i) => i.kind === 'bad') ? 'bad' : 'warn';
  }, [rows, rowIssues]);

  // ── Selection overlays ────────────────────────────────────────────────
  const cellEl = (r, c) => {
    const cv = cvRef.current;
    return cv ? cv.querySelector(`td[data-r="${r}"][data-c="${c}"]`) : null;
  };
  const place = (box, a, b) => {
    if (!box) return;
    if (!a || !b) { box.classList.add('eg-hide'); return; }
    const cv = cvRef.current.getBoundingClientRect();
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    box.classList.remove('eg-hide');
    box.style.left = `${ar.left - cv.left - 1}px`;
    box.style.top = `${ar.top - cv.top - 1}px`;
    box.style.width = `${br.right - ar.left + 1}px`;
    box.style.height = `${br.bottom - ar.top + 1}px`;
  };
  useLayoutEffect(() => {
    if (editing) {
      [rboxRef, aboxRef].forEach((r) => r.current && r.current.classList.add('eg-hide'));
      return;
    }
    const g = rng();
    place(rboxRef.current, cellEl(g.r1, g.c1), cellEl(g.r2, g.c2));
    const single = g.r1 === g.r2 && g.c1 === g.c2;
    if (single) aboxRef.current && aboxRef.current.classList.add('eg-hide');
    else place(aboxRef.current, cellEl(A.r, A.c), cellEl(A.r, A.c));
  });

  // Keep the active cell on screen without yanking the page around.
  useEffect(() => {
    const el = cellEl(B.r, B.c);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [B.r, B.c]);

  // ── Keyboard ──────────────────────────────────────────────────────────
  const onKeyDown = (e) => {
    if (editing) return;
    const mod = e.ctrlKey || e.metaKey;
    const g = rng();
    const k = e.key;

    if (mod && (k === 'd' || k === 'D')) { e.preventDefault(); fillDown(); return; }
    if (mod && (k === 'r' || k === 'R')) { e.preventDefault(); fillRight(); return; }
    if (mod && (k === 'z' || k === 'Z')) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && (k === 'y' || k === 'Y')) { e.preventDefault(); redo(); return; }
    if (mod && k === 'Enter') { e.preventDefault(); addRows(1, g.r2 + 1); return; }
    if (mod && (k === '-' || k === '_')) { e.preventDefault(); delRows(); return; }
    if (mod && (k === 'a' || k === 'A')) {
      e.preventDefault(); setA({ r: 0, c: 0 }); setB({ r: rows.length - 1, c: NC - 1 }); return;
    }
    if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'ArrowLeft' || k === 'ArrowRight') {
      e.preventDefault();
      const dr = k === 'ArrowDown' ? 1 : k === 'ArrowUp' ? -1 : 0;
      const dc = k === 'ArrowRight' ? 1 : k === 'ArrowLeft' ? -1 : 0;
      if (mod) {
        go(dr > 0 ? rows.length - 1 : dr < 0 ? 0 : B.r,
          dc > 0 ? NC - 1 : dc < 0 ? 0 : B.c, e.shiftKey);
      } else {
        go(B.r + dr, B.c + dc, e.shiftKey);
      }
      return;
    }
    if (k === 'Home') { e.preventDefault(); go(B.r, 0, e.shiftKey); return; }
    if (k === 'End') { e.preventDefault(); go(B.r, NC - 1, e.shiftKey); return; }
    if (k === 'Enter') {
      e.preventDefault();
      if (e.shiftKey) go(A.r - 1, A.c);
      else setEditing({ r: A.r, c: A.c, seed: null });
      return;
    }
    if (k === 'F2') { e.preventDefault(); setEditing({ r: A.r, c: A.c, seed: null }); return; }
    if (k === 'Tab') {
      e.preventDefault();
      if (e.shiftKey) go(A.c > 0 ? A.r : A.r - 1, A.c > 0 ? A.c - 1 : NC - 1);
      else if (A.c < NC - 1) go(A.r, A.c + 1);
      else if (A.r === rows.length - 1) addRows(1, null, 0);
      else go(A.r + 1, 0);
      return;
    }
    if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); clearRange(); return; }
    if (k === 'Escape') { setRowSel({}); return; }
    if (!mod && !e.altKey && k.length === 1) {
      e.preventDefault();
      setEditing({ r: A.r, c: A.c, seed: k });
    }
  };

  // ── Mouse ─────────────────────────────────────────────────────────────
  const onCellMouseDown = (e, r, c) => {
    if (e.button !== 0) return;
    if (e.shiftKey) { setB({ r, c }); return; }
    setA({ r, c }); setB({ r, c }); setRowSel({});
    dragRef.current = true;
    focusGrid();
  };
  const onCellMouseEnter = (r, c) => {
    if (!dragRef.current) return;
    setB({ r, c });
  };
  useEffect(() => {
    const up = () => { dragRef.current = false; };
    document.addEventListener('mouseup', up);
    return () => document.removeEventListener('mouseup', up);
  }, []);

  const onHandleDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const move = (ev) => {
      const el = document.elementFromPoint(ev.clientX, ev.clientY);
      const td = el && el.closest ? el.closest('td[data-r]') : null;
      if (!td) return;
      const g = rng();
      const r = Number(td.dataset.r);
      const c = Number(td.dataset.c);
      const dv = r - g.r2;
      const dh = c - g.c2;
      if (dv <= 0 && dh <= 0) {
        fillRef.current = null;
        fboxRef.current && fboxRef.current.classList.add('eg-hide');
        return;
      }
      fillRef.current = dv >= dh ? { axis: 'v', to: r } : { axis: 'h', to: c };
      const t = fillRef.current;
      place(
        fboxRef.current,
        t.axis === 'v' ? cellEl(g.r2 + 1, g.c1) : cellEl(g.r1, g.c2 + 1),
        t.axis === 'v' ? cellEl(t.to, g.c2) : cellEl(g.r2, t.to),
      );
    };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      fboxRef.current && fboxRef.current.classList.add('eg-hide');
      if (fillRef.current) applyDragFill(fillRef.current);
      fillRef.current = null;
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  };

  const pickRow = (e, r) => {
    const from = e.shiftKey ? Math.min(A.r, r) : r;
    const to = e.shiftKey ? Math.max(A.r, r) : r;
    const next = {};
    for (let i = from; i <= to; i += 1) next[i] = true;
    setRowSel(next);
    if (!e.shiftKey) setA({ r, c: 0 });
    setB({ r: to, c: NC - 1 });
    focusGrid();
  };

  // ── Save ──────────────────────────────────────────────────────────────
  const save = async () => {
    const payload = [];
    const rowIndexes = [];
    rows.forEach((row, i) => {
      if (notStarted(row)) return;
      const body = {};
      COLS.forEach((col) => {
        if (col.kind === 'auto') return;
        const raw = (row.v[col.k] || '').trim();
        if (!raw) return;
        if (NUM_KEYS.has(col.k)) body[col.k] = Number(raw);
        // <input type="month"> gives YYYY-MM; event_month_year is a DateField,
        // so it becomes the first of that month — the column only ever displays
        // month and year (helpers.fmy), which is why the grid asks for a month
        // rather than the full date the single-ticket form asks for.
        else if (col.kind === 'month') body[col.k] = raw.length === 7 ? `${raw}-01` : raw;
        else body[col.k] = raw;
      });
      payload.push(body);
      rowIndexes.push(i);
    });

    if (!payload.length) { toast('Nothing to create yet', 'wn'); return; }
    setSaving(true);
    setServerErrs({});
    try {
      const res = await ticketsApi.bulkCreate(payload);
      const created = res.created || [];
      setDone(created.map((t) => t.ticket_number || `#${t.id}`));
      toast(`${created.length} ${created.length === 1 ? 'ticket' : 'tickets'} created`, 'ok');
      onSaved && onSaved();
    } catch (err) {
      const data = err && err.response && err.response.data;
      const errs = (data && data.errors) || null;
      if (errs) {
        // Errors come back keyed by the index within the payload, which skips
        // the not-started rows — map them back to grid rows before showing.
        const mapped = {};
        Object.keys(errs).forEach((k) => {
          const gridRow = rowIndexes[Number(k)];
          if (gridRow != null) mapped[gridRow] = errs[k];
        });
        setServerErrs(mapped);
        toast((data && data.detail) || 'Some rows need attention', 'er');
      } else {
        toast((data && data.detail) || 'Could not create the batch', 'er');
      }
    } finally {
      setSaving(false);
    }
  };

  const startAnother = () => {
    setDone(null);
    hist.current = { past: [], future: [] };
    setRows([mkRow(null, false)]);
    setA({ r: 0, c: 0 }); setB({ r: 0, c: 0 });
    setDups({}); setServerErrs({});
    focusGrid();
  };

  // ── Render ────────────────────────────────────────────────────────────
  const g = rng();

  if (done) {
    return (
      <div className="eg-wrap">
        <div className="eg-done">
          <div className="eg-done-h">
            <span className="eg-done-ic"><Icon name="check" size={19} /></span>
            <div>
              <h2>{done.length} {done.length === 1 ? 'ticket' : 'tickets'} created</h2>
              <p>
                Numbered from the per-purpose sequence, and added under{' '}
                <b>{user.username}</b>. Added Time follows the order you typed them,
                so they sit at the end of the table.
              </p>
            </div>
          </div>
          <div className="eg-nums">{done.map((n) => <span key={n}>{n}</span>)}</div>
          <div className="eg-done-a">
            <button className="btn btn-s" onClick={onClose}>Back to the table</button>
            <button className="btn btn-p" onClick={startAnother}>Start another batch</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="eg-wrap">
      <div className="eg-bar">
        <div className="eg-title">
          <h2>New tickets</h2>
          <p>
            Type straight into the grid. A new row carries the row above forward, and
            these are filed under <b>{user.username}</b>.
          </p>
        </div>
        <div className="eg-acts">
          <button className="btn btn-s" onClick={undo} title="Undo, Ctrl+Z">Undo</button>
          <button className="btn btn-s" onClick={redo} title="Redo, Ctrl+Y">Redo</button>
          <button className="btn btn-s" onClick={delRows} title="Delete the selected rows, Ctrl+Minus">
            <Icon name="trash" size={14} />Delete rows
          </button>
          <button className="btn btn-s" onClick={() => addRows(1)}>
            <Icon name="plus" size={14} />Add row
          </button>
          <button className="btn btn-p" onClick={save} disabled={saving || !stats.staged || !!stats.bad}>
            {saving ? 'Creating…' : `Create ${stats.staged || ''} ${stats.staged === 1 ? 'ticket' : 'tickets'}`.trim()}
          </button>
          <button className="btn btn-s eg-ib" onClick={onClose} title="Close the grid" aria-label="Close">
            <Icon name="x" size={14} />
          </button>
        </div>
      </div>

      <div className="eg-legend">
        <span className="eg-kb"><kbd>Ctrl</kbd><kbd>D</kbd>Fill down</span>
        <span className="eg-kb"><kbd>Ctrl</kbd><kbd>R</kbd>Fill right</span>
        <span className="eg-kb"><kbd>Enter</kbd>Edit, then next row</span>
        <span className="eg-kb"><kbd>Tab</kbd>Next field</span>
        <span className="eg-kb"><kbd>Ctrl</kbd><kbd>V</kbd>Paste a block</span>
        <span className="eg-kb"><kbd>Ctrl</kbd><kbd>Enter</kbd>Row below</span>
        <span className="eg-kb">Drag the corner to fill or extend a series</span>
        <span className="eg-sp" />
        <label className="eg-tgl">
          <input type="checkbox" checked={carryOn} onChange={(e) => setCarryOn(e.target.checked)} />
          <span className="eg-sw" />
          Carry the row above forward
          <small>dotted values are inherited</small>
        </label>
      </div>

      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        className="eg-scroll"
        ref={wrapRef}
        tabIndex={0}
        role="grid"
        aria-label="New tickets"
        onKeyDown={onKeyDown}
        onCopy={(e) => { if (!editing) { e.preventDefault(); e.clipboardData.setData('text/plain', rangeTSV()); } }}
        onCut={(e) => { if (!editing) { e.preventDefault(); e.clipboardData.setData('text/plain', rangeTSV()); clearRange(); } }}
        onPaste={(e) => {
          if (editing) return;
          const text = e.clipboardData && e.clipboardData.getData('text/plain');
          if (!text) return;
          e.preventDefault();
          pasteBlock(text);
        }}
      >
        <div className="eg-cv" ref={cvRef}>
          <table className="eg-t">
            <colgroup>
              <col style={{ width: 54 }} />
              {COLS.map((c) => <col key={c.k} style={{ width: c.w }} />)}
            </colgroup>
            <thead>
              <tr>
                <th className="eg-gut">#</th>
                {COLS.map((c, i) => (
                  <th
                    key={c.k}
                    className={(i >= g.c1 && i <= g.c2 ? 'hl ' : '') + (c.kind === 'auto' ? 'lock' : '')}
                    onMouseDown={() => { setA({ r: 0, c: i }); setB({ r: rows.length - 1, c: i }); focusGrid(); }}
                  >
                    {c.t}{c.req ? <span className="eg-rq">*</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={row.key} className={rowSel[r] ? 'rsel' : ''}>
                  <td className="eg-gut" onMouseDown={(e) => pickRow(e, r)}>
                    <span className={'eg-dot ' + rowState(r)} />{r + 1}
                  </td>
                  {COLS.map((col, c) => {
                    const issue = cellIssue(r, c);
                    const sel = r >= g.r1 && r <= g.r2 && c >= g.c1 && c <= g.c2;
                    const cls = ['eg-c'];
                    if (col.kind === 'num') cls.push('n');
                    if (col.mono) cls.push('m');
                    if (sel) cls.push('sel');
                    if (col.kind === 'auto') cls.push('lock');
                    if (row.carry[col.k]) cls.push('carry');
                    if (issue) cls.push(issue.kind);
                    const isEditing = editing && editing.r === r && editing.c === c;
                    return (
                      <td
                        key={col.k}
                        className={cls.join(' ')}
                        data-r={r}
                        data-c={c}
                        title={issue ? issue.msg : undefined}
                        onMouseDown={(e) => onCellMouseDown(e, r, c)}
                        onMouseEnter={() => onCellMouseEnter(r, c)}
                        onDoubleClick={() => setEditing({ r, c, seed: null })}
                      >
                        {isEditing ? (
                          <CellEditor
                            col={col}
                            options={optionsFor(col)}
                            initial={editing.seed != null ? editing.seed : row.v[col.k]}
                            seeded={editing.seed != null}
                            onCancel={() => { setEditing(null); focusGrid(); }}
                            onCommit={(val, dir) => {
                              setEditing(null);
                              if (val !== row.v[col.k]) commitCell(r, c, val);
                              // Enter or Tab off the last row grows the grid, so
                              // a batch is one unbroken run of typing.
                              if (dir === 'down') {
                                if (r === rows.length - 1) addRows(1, null, c);
                                else go(r + 1, c);
                              } else if (dir === 'up') go(r - 1, c);
                              else if (dir === 'right') {
                                if (c < NC - 1) go(r, c + 1);
                                else if (r === rows.length - 1) addRows(1, null, 0);
                                else go(r + 1, 0);
                              } else if (dir === 'left') go(c > 0 ? r : r - 1, c > 0 ? c - 1 : NC - 1);
                              focusGrid();
                            }}
                          />
                        ) : (
                          <CellText col={col} row={row} />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="eg-rbox eg-hide" ref={rboxRef}>
            {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
            <span className="eg-hnd" onMouseDown={onHandleDown} />
          </div>
          <div className="eg-abox eg-hide" ref={aboxRef} />
          <div className="eg-fbox eg-hide" ref={fboxRef} />
        </div>
      </div>

      <div className="eg-foot">
        <span><b>{stats.staged}</b> {stats.staged === 1 ? 'row' : 'rows'} staged</span>
        {stats.bad ? <span className="tg bg-red">{stats.bad} to fix</span> : null}
        {stats.warn ? <span className="tg bg-amber">{stats.warn} to check</span> : null}
        {!stats.bad && !stats.warn && stats.staged ? <span className="tg bg-green">Ready</span> : null}
        <span className="eg-hint">{stats.firstMsg}</span>
        <div className="eg-foot-r">
          <button className="btn btn-s" onClick={() => addRows(5)}>Add 5 rows</button>
        </div>
      </div>
    </div>
  );
}

// ── Cell rendering ──────────────────────────────────────────────────────────

/** The prefix the backend will build the number from — never editable. */
function numberPrefix(row) {
  const p = (row.v.purpose || '').trim();
  if (!p) return '';
  const t = typeCode(row.v.type_of_ticket);
  return t ? `${t}-${p}` : p;
}

function CellText({ col, row }) {
  const raw = row.v[col.k] || '';
  if (col.kind === 'auto') {
    const prefix = numberPrefix(row);
    return (
      <div className="eg-v">
        {prefix ? <span className="eg-tkn">{prefix} <i>next</i></span> : null}
      </div>
    );
  }
  let body = raw;
  if (col.cell) body = col.cell(raw);
  if (raw && col.tone) body = <span className={'tg bg-' + col.tone(raw)}>{raw}</span>;
  else if (raw && col.k === 'link_url') body = <span className="eg-lnk">{raw}</span>;
  return (
    <div className="eg-v">
      {row.carry[col.k] && raw ? <span className="eg-inh">{body}</span> : body}
    </div>
  );
}

/**
 * One reading of a server verdict, shown on the link cell and on the purpose
 * cell, because the purpose is half of what makes a repeat a repeat.
 *
 * The wording always names the earlier ticket, the purpose it was raised under
 * and how long ago, since that is what decides whether this row is a mistake or
 * ordinary work.
 */
function dupIssue(verdict, onPurpose) {
  const first = (verdict.matches && verdict.matches[0]) || {};
  const where = first.ticket_number || 'an earlier ticket';
  const when = age(first.created_at);
  const purpose = first.purpose || 'no purpose';
  const total = verdict.total || (verdict.matches || []).length;
  const more = total > 1 ? `, and on ${total - 1} other ${total === 2 ? 'ticket' : 'tickets'}` : '';

  if (verdict.severity === 'block') {
    return {
      kind: 'bad',
      msg: `Already raised under the same purpose ${purpose}, as ${where} ${when}. Change the purpose, or work that ticket instead.`,
    };
  }
  if (first.same_purpose) {
    return {
      kind: 'warn',
      msg: `Raised under this same purpose before, as ${where}${more}, but that was ${when}, so it will save.`,
    };
  }
  return {
    kind: 'warn',
    msg: onPurpose
      ? `This link was raised ${when} under a different purpose, ${purpose}, as ${where}${more}. A new purpose is allowed, so it will save.`
      : `Seen before under a different purpose, ${purpose}, as ${where}${more}. That is allowed, so it will save.`,
  };
}

function age(iso) {
  if (!iso) return 'earlier';
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 60) return `${days} days ago`;
  return `${Math.round(days / 30)} months ago`;
}
