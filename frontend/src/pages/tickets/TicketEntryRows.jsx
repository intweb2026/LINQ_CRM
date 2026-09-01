import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../lib/icons';
import { TK_PRIORITY, TK_RELATIONSHIPS, TK_TYPES } from '../../lib/constants';
import * as ticketsApi from '../../api/tickets';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';

/**
 * New tickets, typed straight into the Ticket Central table.
 *
 * This renders as DataTable's `entryBand`: a second <table> inside the SAME
 * scroll box as the rows, carrying an identical <colgroup>, so a draft cell sits
 * exactly under the column heading that names it and both scroll sideways
 * together. It is sticky to the bottom of that box, because the table is sorted
 * oldest first and the true last row can be 42,912 down.
 *
 * The band exists only while there are unsubmitted rows. Submit creates them,
 * they leave the band and appear in the table above as real tickets, and when
 * the last one goes the band closes itself.
 *
 * DRAFTS ARE NEVER HELD ONLY IN MEMORY. Every change is written to
 * localStorage under the signed-in user's key, so a reload, a crash, a closed
 * tab or a stray navigation cannot lose typing that has not been submitted yet.
 * They are cleared only when the server has confirmed the rows were created.
 */

// ── Which columns a draft row can hold ──────────────────────────────────────
//
// The MR half of a ticket (ticket_central/constants.MR_FIELDS). Everything else
// in the table — the DMD result, the LX-2 pass, the provenance, the system
// stamps — is not MR's to fill and shows as a locked cell.
//
// `carry` marks what a new row inherits from the row above it: the event and its
// classification repeat down a batch, while the link, its keywords and the
// comment are what make each row a different ticket and must start empty.
const FIELDS = {
  link_url: { kind: 'text', req: true, ph: 'https://' },
  linkedin_keywords: { kind: 'text' },
  type_of_ticket: { kind: 'pick', opts: TK_TYPES, req: true, carry: true },
  purpose: { kind: 'pick', free: true, mono: true, req: true, carry: true },
  priority: { kind: 'pick', opts: Object.keys(TK_PRIORITY), carry: true },
  estimate: { kind: 'num' },
  organizer: { kind: 'text', carry: true },
  competitor_event_name: { kind: 'text', carry: true },
  event_month_year: { kind: 'month', carry: true },
  event_location: { kind: 'text', carry: true },
  relationship: { kind: 'pick', opts: TK_RELATIONSHIPS, carry: true },
  mr_comments: { kind: 'text' },
  assigned_mr: { kind: 'pick', free: true, carry: true },
};
const REQUIRED = Object.keys(FIELDS).filter((k) => FIELDS[k].req);
const NUM_KEYS = new Set(['estimate']);

// Mirrors ticket_central/utils.extract_type_code: the segment after the last
// dash, or the whole string. 'White - WH' → 'WH'.
const typeCode = (v) => {
  const s = (v || '').trim();
  if (!s) return '';
  return s.includes('-') ? s.split('-').pop().trim() : s;
};

// Mirrors utils.normalize_link, so the band can call a repeat within the batch
// itself without a round trip. The server stays the authority.
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
const nextKey = () => `d${Date.now().toString(36)}${(uid += 1)}`;

function newDraft(prev) {
  const draft = { key: nextKey(), v: {}, carry: {} };
  if (prev) {
    Object.keys(FIELDS).forEach((k) => {
      if (FIELDS[k].carry && (prev.v[k] || '').trim()) {
        draft.v[k] = prev.v[k];
        draft.carry[k] = true;
      }
    });
  }
  return draft;
}

/**
 * A draft holding nothing but inherited values has not been started.
 *
 * Without this the band would always end in a half-filled row that counts
 * itself as a ticket, fails on the link it does not have, and blocks Submit.
 */
const notStarted = (d) => Object.keys(FIELDS)
  .every((k) => !(d.v[k] || '').trim() || d.carry[k]);

/** Two source values a constant step apart continue as a series; else repeat. */
function series(src, n) {
  const out = [];
  const nums = src.map((v) => (/^-?\d+$/.test((v || '').trim()) ? parseInt(v, 10) : null));
  if (src.length > 1 && nums.every((x) => x !== null)) {
    const step = nums[1] - nums[0];
    if (nums.every((x, i) => i === 0 || x - nums[i - 1] === step) && step !== 0) {
      let last = nums[nums.length - 1];
      for (let i = 0; i < n; i += 1) { last += step; out.push(String(last)); }
      return out;
    }
  }
  for (let i = 0; i < n; i += 1) out.push(src[i % src.length]);
  return out;
}

// ── Draft storage ───────────────────────────────────────────────────────────

const storeKey = (who) => `tickets.drafts.${who || 'anon'}`;

function loadDrafts(who) {
  try {
    const raw = window.localStorage.getItem(storeKey(who));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Re-key on load: the keys are React list keys, not identity, and a stored
    // key colliding with a fresh one would make two rows share an identity.
    return parsed
      .filter((d) => d && typeof d === 'object' && d.v)
      .map((d) => ({ key: nextKey(), v: d.v || {}, carry: d.carry || {} }));
  } catch {
    // A quota error, private mode, or a half-written value. An unreadable draft
    // store is an empty one; it must never stop the page rendering.
    return [];
  }
}

function saveDrafts(who, drafts) {
  try {
    if (!drafts.length) window.localStorage.removeItem(storeKey(who));
    else {
      window.localStorage.setItem(storeKey(who), JSON.stringify(
        drafts.map((d) => ({ v: d.v, carry: d.carry })),
      ));
    }
  } catch { /* nothing to do about a full or blocked store, and it must not throw */ }
}

// ── One editable cell ───────────────────────────────────────────────────────

function CellEditor({ field, initial, options, seeded, onCommit, onCancel }) {
  const ref = useRef(null);
  const [q, setQ] = useState(initial || '');
  const [ix, setIx] = useState(0);
  // Every exit funnels through here. Choosing an option commits and unmounts the
  // editor, and the input's blur then fires on the way out — without this latch
  // that second commit writes the half-typed text over the option just picked.
  const settled = useRef(false);
  const finish = (val, dir) => {
    if (settled.current) return;
    settled.current = true;
    onCommit(val, dir);
  };

  const list = useMemo(() => {
    if (field.kind !== 'pick') return [];
    const needle = (q || '').trim().toLowerCase();
    return (options || [])
      .filter((o) => !needle || o.toLowerCase().includes(needle))
      .slice(0, 50);
  }, [field.kind, options, q]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    if (!el.value) return;
    if (seeded) el.setSelectionRange(el.value.length, el.value.length);
    else el.select();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = () => (ref.current ? ref.current.value : '');

  /**
   * What Enter or Tab stores. Order matters: an emptied cell stays empty, or
   * clearing a picklist and pressing Enter would silently store the first option
   * (an empty query filters nothing out). An exact typed match beats the
   * highlight. Otherwise the highlight wins, and failing that the typed text
   * stands, because purpose and Assigned MR are free CharFields and refusing an
   * unlisted value would make a newly agreed code unenterable.
   */
  const take = (dir) => {
    const typed = value().trim();
    if (field.kind !== 'pick' || !typed) { finish(value(), dir); return; }
    const exact = (options || []).find((o) => o.toLowerCase() === typed.toLowerCase());
    if (exact) { finish(exact, dir); return; }
    if (list.length && ix >= 0 && list[ix] != null) { finish(list[ix], dir); return; }
    finish(value(), dir);
  };

  const key = (e) => {
    e.stopPropagation();          // the band's own handler must not see these
    if (e.key === 'Escape') { e.preventDefault(); settled.current = true; onCancel(); return; }
    if (field.kind === 'pick' && list.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      setIx((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length);
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); take(e.shiftKey ? 'up' : 'down'); return; }
    if (e.key === 'Tab') { e.preventDefault(); take(e.shiftKey ? 'left' : 'right'); return; }
    if (field.kind !== 'pick' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault(); take(e.key === 'ArrowDown' ? 'down' : 'up');
    }
  };

  return (
    <div className={'eg-ed' + (field.kind === 'num' ? ' n' : '') + (field.mono ? ' m' : '')}>
      <input
        ref={ref}
        type={field.kind === 'month' ? 'month' : 'text'}
        defaultValue={initial}
        placeholder={field.ph || ''}
        onChange={(e) => { setQ(e.target.value); setIx(0); }}
        onKeyDown={key}
        onBlur={() => finish(value(), null)}
      />
      {field.kind === 'pick' ? (
        <div className="eg-pop" role="listbox">
          {list.length ? list.map((o, i) => (
            <button
              type="button"
              key={o}
              className={'eg-opt' + (i === ix ? ' on' : '')}
              // mousedown with preventDefault, not click: a click lands after
              // the input's blur has already closed the editor.
              onMouseDown={(e) => { e.preventDefault(); finish(o, 'stay'); }}
            >
              {o}
              {field.opts === TK_TYPES ? <small>{typeCode(o)}</small> : null}
            </button>
          )) : (
            <div className="eg-none">{field.free ? 'Not on the list, Enter keeps it' : 'No match'}</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

// ── The band ────────────────────────────────────────────────────────────────

export default function TicketEntryRows({ cols, select, colWidth, pins, onCreated, openRef }) {
  const { user } = useSession();
  const toast = useToast();
  const who = user && user.username;

  const [drafts, setDrafts] = useState(() => loadDrafts(who));
  const [cur, setCur] = useState({ r: 0, c: 0 });      // active cell, in editable-column space
  const [anchor, setAnchor] = useState({ r: 0, c: 0 });
  const [editing, setEditing] = useState(null);        // { r, c, seed }
  const [dups, setDups] = useState({});
  const [rowErrs, setRowErrs] = useState({});
  const [saving, setSaving] = useState(false);
  const bandRef = useRef(null);

  const { data: users } = useFetch(usersApi.list, [], { initialData: [] });
  const { data: purposeRows } = useFetch(ticketsApi.purposes, [], { initialData: [] });

  const mrEmails = useMemo(
    () => (users || []).filter((u) => u.status === 'active' && u.email)
      .map((u) => u.email).sort((a, b) => a.localeCompare(b)),
    [users],
  );
  const purposeOpts = useMemo(() => (purposeRows || []).map((p) => p.purpose), [purposeRows]);

  // Editable columns, in the TABLE's own column order and honouring the Columns
  // menu — a column the user has hidden is not somewhere they can type.
  const editable = useMemo(
    () => cols.filter((c) => FIELDS[c.key]).map((c) => c.key),
    [cols],
  );
  const editIx = useMemo(
    () => editable.reduce((m, k, i) => ({ ...m, [k]: i }), {}),
    [editable],
  );
  // The table's own column labels, so a message names the field the way the
  // heading above the cell names it.
  const labelOf = useMemo(
    () => cols.reduce((m, c) => ({ ...m, [c.key]: c.label }), {}),
    [cols],
  );
  const optionsFor = useCallback((key) => {
    if (key === 'purpose') return purposeOpts;
    if (key === 'assigned_mr') return mrEmails;
    return FIELDS[key].opts || [];
  }, [purposeOpts, mrEmails]);

  // A required column the user has hidden cannot be filled, and Submit would
  // then be permanently blocked with no visible cause. Say so instead.
  const hiddenRequired = useMemo(
    () => REQUIRED.filter((k) => !editable.includes(k)),
    [editable],
  );

  // ── Persistence ─────────────────────────────────────────────────────
  useEffect(() => { saveDrafts(who, drafts); }, [who, drafts]);

  /**
   * Add rows and put the cursor on the first of them.
   *
   * The target index is the CURRENT length, read from the closure rather than
   * from inside the updater, because the first new row lands exactly where the
   * list used to end.
   */
  const addRows = useCallback((n) => {
    const at = drafts.length;
    setDrafts((cur2) => {
      const next = [...cur2];
      for (let i = 0; i < n; i += 1) next.push(newDraft(next[next.length - 1] || null));
      return next;
    });
    setCur({ r: at, c: 0 });
    setAnchor({ r: at, c: 0 });
    // Focus lands after the row exists, hence the frame's delay.
    requestAnimationFrame(() => bandRef.current && bandRef.current.focus());
  }, [drafts.length]);

  // How the page opens the band. This component still MOUNTS when there are no
  // drafts — it returns null from the render, below, which is not the same as
  // being absent — so this handle is registered either way and "New tickets"
  // always has something to call.
  useEffect(() => {
    if (openRef) openRef.current = { addRows, count: drafts.length };
  }, [openRef, addRows, drafts.length]);

  // ── Writes ──────────────────────────────────────────────────────────
  const setCell = useCallback((r, key, val) => {
    setDrafts((cur2) => {
      const next = [...cur2];
      if (!next[r]) return cur2;
      const v = { ...next[r].v, [key]: val == null ? '' : String(val) };
      const carry = { ...next[r].carry };
      delete carry[key];
      next[r] = { ...next[r], v, carry };
      return next;
    });
    setRowErrs((e) => (e[r] ? { ...e, [r]: undefined } : e));
  }, []);

  const removeRow = useCallback((r) => {
    setDrafts((cur2) => cur2.filter((_, i) => i !== r));
    setCur((p) => ({ ...p, r: Math.max(0, p.r - (r <= p.r ? 1 : 0)) }));
  }, []);

  const clearAll = useCallback(() => {
    setDrafts([]);
    setDups({});
    setRowErrs({});
  }, []);

  const fill = useCallback((axis) => {
    const r1 = Math.min(anchor.r, cur.r); const r2 = Math.max(anchor.r, cur.r);
    const c1 = Math.min(anchor.c, cur.c); const c2 = Math.max(anchor.c, cur.c);
    setDrafts((cur2) => {
      const next = cur2.map((d) => ({ ...d, v: { ...d.v }, carry: { ...d.carry } }));
      if (axis === 'down') {
        const from = r1 === r2 ? r1 - 1 : r1;
        if (from < 0) return cur2;
        for (let c = c1; c <= c2; c += 1) {
          const key = editable[c];
          const src = next[from].v[key] || '';
          for (let r = from + 1; r <= r2; r += 1) {
            if (!next[r]) break;
            next[r].v[key] = src;
            delete next[r].carry[key];
          }
        }
      } else {
        const from = c1 === c2 ? c1 - 1 : c1;
        if (from < 0) return cur2;
        for (let r = r1; r <= r2; r += 1) {
          if (!next[r]) break;
          const src = next[r].v[editable[from]] || '';
          for (let c = from + 1; c <= c2; c += 1) {
            next[r].v[editable[c]] = src;
            delete next[r].carry[editable[c]];
          }
        }
      }
      return next;
    });
  }, [anchor, cur, editable]);

  const dragFillDown = useCallback((toRow) => {
    const r1 = Math.min(anchor.r, cur.r); const r2 = Math.max(anchor.r, cur.r);
    const c1 = Math.min(anchor.c, cur.c); const c2 = Math.max(anchor.c, cur.c);
    if (toRow <= r2) return;
    setDrafts((cur2) => {
      const next = cur2.map((d) => ({ ...d, v: { ...d.v }, carry: { ...d.carry } }));
      while (next.length <= toRow) next.push(newDraft(next[next.length - 1] || null));
      for (let c = c1; c <= c2; c += 1) {
        const key = editable[c];
        const src = [];
        for (let r = r1; r <= r2; r += 1) src.push(next[r].v[key] || '');
        const vals = series(src, toRow - r2);
        for (let i = 0; i < vals.length; i += 1) {
          next[r2 + 1 + i].v[key] = vals[i];
          delete next[r2 + 1 + i].carry[key];
        }
      }
      return next;
    });
    setCur({ r: toRow, c: c2 });
  }, [anchor, cur, editable]);

  const pasteBlock = useCallback((text) => {
    const lines = text.replace(/\r\n|\r/g, '\n').split('\n');
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    if (!lines.length) return;
    const startR = Math.min(anchor.r, cur.r);
    const startC = Math.min(anchor.c, cur.c);
    setDrafts((cur2) => {
      const next = cur2.map((d) => ({ ...d, v: { ...d.v }, carry: { ...d.carry } }));
      while (next.length < startR + lines.length) {
        next.push(newDraft(next[next.length - 1] || null));
      }
      lines.forEach((line, i) => {
        line.split('\t').forEach((cell, j) => {
          const key = editable[startC + j];
          if (!key) return;
          next[startR + i].v[key] = cell.trim();
          delete next[startR + i].carry[key];
        });
      });
      return next;
    });
    toast(`Pasted ${lines.length} ${lines.length === 1 ? 'row' : 'rows'}`, 'ok');
  }, [anchor, cur, editable, toast]);

  // ── Repeated links ──────────────────────────────────────────────────
  const localDups = useMemo(() => {
    const seen = new Map(); const out = {};
    drafts.forEach((d, i) => {
      const link = normalizeLink(d.v.link_url);
      if (!link || notStarted(d)) return;
      const k = `${link}||${(d.v.purpose || '').trim().toUpperCase()}`;
      if (seen.has(k)) out[i] = seen.get(k) + 1;
      else seen.set(k, i);
    });
    return out;
  }, [drafts]);

  const signature = drafts
    .map((d) => `${normalizeLink(d.v.link_url)}|${(d.v.purpose || '').trim().toUpperCase()}`)
    .join('~');
  useEffect(() => {
    const pairs = drafts.map((d) => ({
      link_url: d.v.link_url || '', purpose: d.v.purpose || '',
    }));
    if (!pairs.some((p) => p.link_url.trim())) { setDups({}); return undefined; }
    let alive = true;
    const t = setTimeout(() => {
      ticketsApi.checkLinks(pairs).then((res) => {
        if (!alive) return;
        const out = {};
        res.forEach((v, i) => { if (v && v.severity) out[i] = v; });
        setDups(out);
      }).catch(() => { /* Submit re-checks server-side; no need to shout here */ });
    }, 450);
    return () => { alive = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // ── Validation ──────────────────────────────────────────────────────
  const issueFor = useCallback((r, key) => {
    const d = drafts[r];
    if (!d || notStarted(d)) return null;
    const field = FIELDS[key];
    const val = (d.v[key] || '').trim();
    const srv = rowErrs[r] && rowErrs[r][key];
    if (srv) return { kind: 'bad', blocks: true, msg: Array.isArray(srv) ? srv.join(' ') : String(srv) };
    if (field.req && !val) return { kind: 'warn', blocks: true, msg: `${labelOf[key] || key} is needed before this ticket can be submitted` };
    if (key === 'estimate' && val && !/^\d+$/.test(val)) return { kind: 'bad', blocks: true, msg: 'Estimate takes a whole number' };
    if (key === 'estimate' && val && Number(val) === 0) return { kind: 'bad', blocks: true, msg: 'Estimate has to be above zero' };
    if (key === 'link_url' && val && !/^https?:\/\//i.test(val)) return { kind: 'bad', blocks: true, msg: 'A link starts with http or https' };
    if (key === 'link_url' && localDups[r]) return { kind: 'bad', blocks: true, msg: `Same link and purpose as row ${localDups[r]} of this batch` };
    if ((key === 'link_url' || key === 'purpose') && dups[r]) return dupIssue(dups[r], key === 'purpose');
    return null;
  }, [drafts, rowErrs, localDups, dups, labelOf]);

  const stats = useMemo(() => {
    let staged = 0; let blocked = 0; let warn = 0; let first = '';
    drafts.forEach((d, r) => {
      if (notStarted(d)) return;
      staged += 1;
      let rowBlocked = false; let rowWarn = false; let msg = '';
      editable.forEach((key) => {
        const i = issueFor(r, key);
        if (!i) return;
        if (i.blocks) { rowBlocked = true; if (!msg) msg = i.msg; }
        else { rowWarn = true; if (!msg) msg = i.msg; }
      });
      if (rowBlocked) blocked += 1; else if (rowWarn) warn += 1;
      if (msg && !first) first = `Row ${r + 1}. ${msg}`;
    });
    return { staged, blocked, warn, first };
  }, [drafts, editable, issueFor]);

  const rowState = useCallback((r) => {
    const d = drafts[r];
    if (!d || notStarted(d)) return '';
    let worst = 'ok';
    for (const key of editable) {
      const i = issueFor(r, key);
      if (!i) continue;
      if (i.kind === 'bad') return 'bad';
      if (i.blocks) worst = 'warn';
      else if (worst === 'ok') worst = 'warn';
    }
    return worst;
  }, [drafts, editable, issueFor]);

  // ── Keyboard ────────────────────────────────────────────────────────
  const move = useCallback((dr, dc, keep) => {
    const r = Math.max(0, Math.min(drafts.length - 1, cur.r + dr));
    const c = Math.max(0, Math.min(editable.length - 1, cur.c + dc));
    setCur({ r, c });
    if (!keep) setAnchor({ r, c });
  }, [cur, drafts.length, editable.length]);

  const onKeyDown = (e) => {
    if (editing) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key;
    if (mod && (k === 'd' || k === 'D')) { e.preventDefault(); fill('down'); return; }
    if (mod && (k === 'r' || k === 'R')) { e.preventDefault(); fill('right'); return; }
    if (mod && k === 'Enter') { e.preventDefault(); addRows(1); return; }
    if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'ArrowLeft' || k === 'ArrowRight') {
      e.preventDefault();
      move(k === 'ArrowDown' ? 1 : k === 'ArrowUp' ? -1 : 0,
        k === 'ArrowRight' ? 1 : k === 'ArrowLeft' ? -1 : 0, e.shiftKey);
      return;
    }
    if (k === 'Enter' || k === 'F2') {
      e.preventDefault(); setEditing({ r: cur.r, c: cur.c, seed: null }); return;
    }
    if (k === 'Tab') {
      e.preventDefault();
      if (e.shiftKey) {
        if (cur.c > 0) move(0, -1);
        else if (cur.r > 0) { setCur({ r: cur.r - 1, c: editable.length - 1 }); setAnchor({ r: cur.r - 1, c: editable.length - 1 }); }
      } else if (cur.c < editable.length - 1) move(0, 1);
      else if (cur.r < drafts.length - 1) { setCur({ r: cur.r + 1, c: 0 }); setAnchor({ r: cur.r + 1, c: 0 }); }
      else addRows(1);
      return;
    }
    if (k === 'Delete' || k === 'Backspace') {
      e.preventDefault();
      const r1 = Math.min(anchor.r, cur.r); const r2 = Math.max(anchor.r, cur.r);
      const c1 = Math.min(anchor.c, cur.c); const c2 = Math.max(anchor.c, cur.c);
      for (let r = r1; r <= r2; r += 1) {
        for (let c = c1; c <= c2; c += 1) setCell(r, editable[c], '');
      }
      return;
    }
    if (!mod && !e.altKey && k.length === 1) {
      e.preventDefault(); setEditing({ r: cur.r, c: cur.c, seed: k });
    }
  };

  // ── Submit ──────────────────────────────────────────────────────────
  const submit = async () => {
    const payload = []; const rowMap = [];
    drafts.forEach((d, i) => {
      if (notStarted(d)) return;
      const body = {};
      editable.forEach((key) => {
        const raw = (d.v[key] || '').trim();
        if (!raw) return;
        if (NUM_KEYS.has(key)) body[key] = Number(raw);
        // <input type="month"> gives YYYY-MM; event_month_year is a DateField,
        // so it becomes the first of that month. The column only ever displays
        // month and year, which is why the band asks for a month.
        else if (FIELDS[key].kind === 'month') body[key] = raw.length === 7 ? `${raw}-01` : raw;
        else body[key] = raw;
      });
      payload.push(body);
      rowMap.push(i);
    });
    if (!payload.length) { toast('Nothing to submit yet', 'wn'); return; }

    setSaving(true);
    setRowErrs({});
    try {
      const res = await ticketsApi.bulkCreate(payload);
      const made = (res.created || []).length;
      // Only the rows the server confirmed leave the band; anything still
      // unsubmitted stays exactly as typed.
      const submitted = new Set(rowMap.map((i) => drafts[i].key));
      setDrafts((cur2) => cur2.filter((d) => !submitted.has(d.key)));
      setDups({});
      toast(`${made} ${made === 1 ? 'ticket' : 'tickets'} submitted`, 'ok');
      onCreated && onCreated();
    } catch (err) {
      const data = err && err.response && err.response.data;
      const errs = (data && data.errors) || null;
      if (errs) {
        const mapped = {};
        Object.keys(errs).forEach((k) => {
          const row = rowMap[Number(k)];
          if (row != null) mapped[row] = errs[k];
        });
        setRowErrs(mapped);
        toast((data && data.detail) || 'Some rows need attention', 'er');
      } else {
        toast((data && data.detail) || 'Could not submit the batch', 'er');
      }
    } finally {
      setSaving(false);
    }
  };

  // The band exists only for unsubmitted rows.
  if (!drafts.length) return null;

  const r1 = Math.min(anchor.r, cur.r); const r2 = Math.max(anchor.r, cur.r);
  const c1 = Math.min(anchor.c, cur.c); const c2 = Math.max(anchor.c, cur.c);

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      className="eg-band"
      ref={bandRef}
      tabIndex={0}
      role="region"
      aria-label="New tickets"
      onKeyDown={onKeyDown}
      onPaste={(e) => {
        if (editing) return;
        const t = e.clipboardData && e.clipboardData.getData('text/plain');
        if (!t) return;
        e.preventDefault();
        pasteBlock(t);
      }}
    >
      <table className="dt dt-grid eg-t">
        <colgroup>
          {select ? <col style={{ width: 40 }} /> : null}
          {cols.map((c) => <col key={c.key} style={{ width: colWidth(c) }} />)}
        </colgroup>
        <tbody>
          {drafts.map((d, r) => (
            <tr key={d.key} className="eg-row">
              {/* The state dot, and the only way to drop a single row. Lives in
                  the checkbox column when the table has one; when it does not,
                  it rides in the first cell instead (see `dotHere` below) so the
                  row never loses its status marker. */}
              {select ? (
                <td className={'eg-gut' + (pins && pins.size ? ' pin-col' : '')}
                  style={pins && pins.size ? { left: 0 } : undefined}>
                  <span className={'eg-dot ' + rowState(r)} title={rowState(r) || 'not started yet'} />
                  <button
                    type="button"
                    className="eg-x"
                    aria-label={`Discard row ${r + 1}`}
                    title="Discard this row"
                    onMouseDown={(ev) => ev.stopPropagation()}
                    onClick={() => removeRow(r)}
                  >
                    <Icon name="x" size={11} />
                  </button>
                </td>
              ) : null}
              {cols.map((col, colIdx) => {
                const dotHere = !select && colIdx === 0;
                const field = FIELDS[col.key];
                if (!field) {
                  // Not MR's to fill. The ticket number is the exception worth
                  // showing: the prefix the backend will build it from, so the
                  // row reads as a ticket while it is still being typed.
                  const prefix = col.key === 'ticket_number' ? numberPrefix(d) : '';
                  const lockPin = pins ? pins.get(col.key) : null;
                  return (
                    <td
                      key={col.key}
                      className={'eg-lock' + (lockPin ? ' pin-col' + (lockPin.last ? ' pin-last' : '') : '')}
                      style={lockPin ? { left: lockPin.left } : undefined}
                    >
                      {dotHere ? <span className={'eg-dot ' + rowState(r)} /> : null}
                      {prefix ? <span className="eg-tkn">{prefix} <i>next</i></span> : null}
                    </td>
                  );
                }
                const c = editIx[col.key];
                const isCur = cur.r === r && cur.c === c;
                const inRange = r >= r1 && r <= r2 && c >= c1 && c <= c2;
                const issue = issueFor(r, col.key);
                const raw = d.v[col.key] || '';
                const cls = ['eg-c'];
                if (field.kind === 'num') cls.push('n');
                if (field.mono) cls.push('m');
                if (inRange) cls.push('sel');
                if (isCur) cls.push('cur');
                if (d.carry[col.key]) cls.push('carry');
                if (issue) cls.push(issue.kind);
                const pin = pins ? pins.get(col.key) : null;
                if (pin) cls.push('pin-col', ...(pin.last ? ['pin-last'] : []));
                const isEditing = editing && editing.r === r && editing.c === c;
                return (
                  <td
                    key={col.key}
                    className={cls.join(' ')}
                    style={pin ? { left: pin.left } : undefined}
                    title={issue ? issue.msg : undefined}
                    onMouseDown={(ev) => {
                      if (ev.shiftKey) { setCur({ r, c }); return; }
                      setCur({ r, c }); setAnchor({ r, c });
                      if (bandRef.current) bandRef.current.focus();
                    }}
                    onDoubleClick={() => setEditing({ r, c, seed: null })}
                  >
                    {isEditing ? (
                      <CellEditor
                        field={field}
                        options={optionsFor(col.key)}
                        initial={editing.seed != null ? editing.seed : raw}
                        seeded={editing.seed != null}
                        onCancel={() => { setEditing(null); bandRef.current?.focus(); }}
                        onCommit={(val, dir) => {
                          setEditing(null);
                          if (val !== raw) setCell(r, col.key, val);
                          if (dir === 'down') {
                            if (r === drafts.length - 1) addRows(1);
                            else { setCur({ r: r + 1, c }); setAnchor({ r: r + 1, c }); }
                          } else if (dir === 'up' && r > 0) { setCur({ r: r - 1, c }); setAnchor({ r: r - 1, c }); }
                          else if (dir === 'right') {
                            if (c < editable.length - 1) { setCur({ r, c: c + 1 }); setAnchor({ r, c: c + 1 }); }
                            else if (r === drafts.length - 1) addRows(1);
                            else { setCur({ r: r + 1, c: 0 }); setAnchor({ r: r + 1, c: 0 }); }
                          } else if (dir === 'left' && c > 0) { setCur({ r, c: c - 1 }); setAnchor({ r, c: c - 1 }); }
                          bandRef.current?.focus();
                        }}
                      />
                    ) : (
                      <>
                        {dotHere ? <span className={'eg-dot ' + rowState(r)} /> : null}
                        <span className={'eg-v' + (d.carry[col.key] && raw ? ' inh' : '')}>
                          {raw ? cellText(col.key, raw) : null}
                        </span>
                        {/* The fill handle, on the bottom-right of the range. */}
                        {r === r2 && c === c2 ? (
                          // eslint-disable-next-line jsx-a11y/no-static-element-interactions
                          <span
                            className="eg-hnd"
                            title="Drag down to fill, or to extend a number series"
                            onMouseDown={(ev) => {
                              ev.preventDefault(); ev.stopPropagation();
                              // Only the drop point matters, so this listens for
                              // mouseup alone. The rows highlight as the range
                              // grows once the fill lands, which is enough
                              // feedback over a band a few rows tall.
                              const up = (m) => {
                                document.removeEventListener('mouseup', up);
                                const el = document.elementFromPoint(m.clientX, m.clientY);
                                const tr = el && el.closest ? el.closest('tr.eg-row') : null;
                                if (!tr || !tr.parentNode) return;
                                const idx = Array.prototype.indexOf.call(tr.parentNode.children, tr);
                                dragFillDown(idx);
                              };
                              document.addEventListener('mouseup', up);
                            }}
                          />
                        ) : null}
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="eg-foot">
        <span className="eg-n"><b>{stats.staged}</b> {stats.staged === 1 ? 'row' : 'rows'} to submit</span>
        {stats.blocked ? <span className="tg bg-red">{stats.blocked} to fix</span> : null}
        {stats.warn && !stats.blocked ? <span className="tg bg-amber">{stats.warn} to check, will submit</span> : null}
        {hiddenRequired.length ? (
          <span className="tg bg-amber">Turn on the {hiddenRequired.join(' and ')} column to fill it</span>
        ) : null}
        <span className="eg-hint">{stats.first}</span>
        <span className="eg-keys">
          <kbd>Ctrl</kbd><kbd>D</kbd> fill down
          <kbd>Ctrl</kbd><kbd>R</kbd> fill right
          <kbd>Tab</kbd> next
          <kbd>Ctrl</kbd><kbd>V</kbd> paste
        </span>
        <div className="eg-acts">
          <button type="button" className="btn btn-s btn-sm" onClick={() => addRows(1)}>
            <Icon name="plus" size={13} />Row
          </button>
          <button type="button" className="btn btn-s btn-sm" onClick={clearAll} title="Discard every unsubmitted row">
            Discard
          </button>
          <button
            type="button"
            className="btn btn-p btn-sm"
            onClick={submit}
            disabled={saving || !stats.staged || !!stats.blocked}
          >
            <Icon name="send" size={13} />
            {saving ? 'Submitting…' : `Submit ${stats.staged || ''}`.trim()}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Cell display ────────────────────────────────────────────────────────────

function numberPrefix(d) {
  const p = (d.v.purpose || '').trim();
  if (!p) return '';
  const t = typeCode(d.v.type_of_ticket);
  return t ? `${t}-${p}` : p;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function cellText(key, raw) {
  if (key === 'event_month_year') {
    const m = /^(\d{4})-(\d{2})/.exec(raw);
    return m ? `${MONTHS[+m[2] - 1]} ${m[1]}` : raw;
  }
  if (key === 'priority') return <span className={'tg bg-' + (TK_PRIORITY[raw] || 'neutral')}>{raw}</span>;
  if (key === 'relationship') return <span className="tg bg-neutral">{raw}</span>;
  return raw;
}

/**
 * One reading of a server verdict, shown on the link cell and the purpose cell,
 * because the purpose is half of what makes a repeat a repeat.
 */
function dupIssue(verdict, onPurpose) {
  const first = (verdict.matches && verdict.matches[0]) || {};
  const where = first.ticket_number || 'an earlier ticket';
  const when = age(first.created_at);
  const purpose = first.purpose || 'no purpose';
  const total = verdict.total || (verdict.matches || []).length;
  const more = total > 1 ? `, and on ${total - 1} other ${total === 2 ? 'ticket' : 'tickets'}` : '';

  // `blocks` is the rule, not the colour: only a same-purpose repeat inside the
  // 90-day window refuses the submit. The other two are advice and go through,
  // which is the whole point of the three-way rule.
  if (verdict.severity === 'block') {
    return {
      kind: 'bad',
      blocks: true,
      msg: `Already raised under the same purpose ${purpose}, as ${where} ${when}. Change the purpose, or work that ticket instead.`,
    };
  }
  if (first.same_purpose) {
    return {
      kind: 'warn',
      blocks: false,
      msg: `Raised under this same purpose before, as ${where}${more}, but that was ${when}, so it will submit.`,
    };
  }
  return {
    kind: 'warn',
    blocks: false,
    msg: onPurpose
      ? `This link was raised ${when} under a different purpose, ${purpose}, as ${where}${more}. A new purpose is allowed, so it will submit.`
      : `Seen before under a different purpose, ${purpose}, as ${where}${more}. That is allowed, so it will submit.`,
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
