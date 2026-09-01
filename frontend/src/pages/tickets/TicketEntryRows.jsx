import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../lib/icons';
import { TK_PRIORITY, TK_RELATIONSHIPS, TK_TYPES } from '../../lib/constants';
import * as ticketsApi from '../../api/tickets';
import { useFetch } from '../../hooks/useFetch';
import { useSession } from '../../context/SessionContext';
import { useToast } from '../../context/ToastContext';

/**
 * New tickets, typed into rows pinned under the Ticket Central table.
 *
 * Every cell is a NATIVE control, always mounted: a real <select> with its own
 * arrow, a real month picker, a real number box, a datalist combobox for the
 * purpose. This is the ticket form's field set laid on its side, one row per
 * ticket — deliberately.
 *
 * The previous version was a hand-rolled spreadsheet: cells looked inert until a
 * custom editor was mounted into them on click, with its own focus handling,
 * popup list and clipping rules. That editor was defeated in turn by the
 * browser's mousedown focus change and by the shared table CSS, and each failure
 * was invisible without a browser to test in. Nothing here mounts, steals focus
 * or draws its own popup, so that whole class of bug has nothing left to live
 * in: the dropdown is a dropdown because it IS one.
 *
 * The band exists only while there are unsubmitted rows. Submit creates them,
 * they appear in the table above as real tickets, and the last one to go closes
 * the band. DRAFTS ARE NEVER ONLY IN MEMORY: every change is written to
 * localStorage under the signed-in user's key and cleared only once the server
 * confirms the rows, so a reload, crash or closed tab cannot lose typing.
 */

// ── The thirteen fields, in the ticket form's own order ─────────────────────
//
// Exactly the MR half of a ticket (ticket_central/constants.MR_FIELDS), nothing
// else: no DMD columns, no provenance, no locked filler cells. `carry` marks
// what a new row inherits from the one above — the event and its classification
// repeat down a batch, while the link, its keywords and the comment are what
// make each row a different ticket. `req` is this grid's own bar: link_url is
// required HERE and not by the API, because a ticket with no link is not
// actionable for Data Mining.
//
// Labels and the estimate bound come from the server schema
// (/api/tickets/bulk_update_schema/, derived from the model), and so does the
// assignee list. Three choice lists are deliberate local overrides:
//   type_of_ticket  the schema offers enum CODES ("WH"); all 42,912 stored rows
//                   hold the display form ("White - WH"), so TK_TYPES it is
//   relationship    the enum is lowercase; every other screen shows "Direct"
//   purpose         typed text, correctly, but the useful values are the codes
//                   already in use, so it is a datalist over the live list that
//                   still accepts a new one
const FIELD_ORDER = [
  'purpose', 'link_url', 'linkedin_keywords', 'competitor_event_name',
  'organizer', 'event_month_year', 'event_location', 'relationship',
  'type_of_ticket', 'priority', 'estimate', 'mr_comments', 'assigned_mr',
];
const F = {
  purpose: { w: 110, req: true, carry: true, ph: 'e.g. CCU' },
  link_url: { w: 230, req: true, ph: 'https://' },
  linkedin_keywords: { w: 170, ph: 'hydrogen, electrolyser' },
  competitor_event_name: { w: 170, carry: true, ph: 'e.g. Hydrogen World 2026' },
  organizer: { w: 140, carry: true, ph: 'e.g. Informa' },
  event_month_year: { w: 140, carry: true },
  event_location: { w: 150, carry: true, ph: 'City, Country' },
  relationship: { w: 120, carry: true },
  type_of_ticket: { w: 140, req: true, carry: true },
  priority: { w: 100, carry: true },
  estimate: { w: 90 },
  mr_comments: { w: 190, ph: 'Anything Data Mining should know' },
  assigned_mr: { w: 190, carry: true },
};

// Mirrors ticket_central/utils.normalize_link, so a repeat inside the batch is
// called the same way the server calls one. The server stays the authority.
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
    FIELD_ORDER.forEach((k) => {
      if (F[k].carry && (prev.v[k] || '').trim()) {
        draft.v[k] = prev.v[k];
        draft.carry[k] = true;
      }
    });
  }
  return draft;
}

/**
 * A row holding nothing but inherited values has not been started. Without this
 * the band would always end in a half-filled row that counts itself as a
 * ticket, fails on the link it does not have, and blocks Submit.
 */
const notStarted = (d) => FIELD_ORDER.every((k) => !(d.v[k] || '').trim() || d.carry[k]);

// ── Draft storage ────────────────────────────────────────────────────────────

const storeKey = (who) => `tickets.drafts.${who || 'anon'}`;

function loadDrafts(who) {
  try {
    const raw = window.localStorage.getItem(storeKey(who));
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((d) => d && typeof d === 'object' && d.v)
      .map((d) => ({ key: nextKey(), v: d.v || {}, carry: d.carry || {} }));
  } catch {
    // Private mode, quota, a half-written value: an unreadable store is an
    // empty one, and must never stop the page rendering.
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
  } catch { /* a full or blocked store must not throw */ }
}

// ── The band ─────────────────────────────────────────────────────────────────

function TicketEntryRows({ onCreated, openRef }) {
  const { user } = useSession();
  const toast = useToast();
  const who = user && user.username;

  const [drafts, setDrafts] = useState(() => loadDrafts(who));
  const [dups, setDups] = useState({});
  const [serverErrs, setServerErrs] = useState({});
  const [saving, setSaving] = useState(false);
  const rootRef = useRef(null);

  const { data: schema } = useFetch(ticketsApi.fieldSchema, [], { initialData: {} });
  const { data: purposeRows } = useFetch(ticketsApi.purposes, [], { initialData: [] });
  const purposeOpts = useMemo(() => (purposeRows || []).map((p) => p.purpose), [purposeRows]);

  const labelOf = useCallback(
    (k) => (schema && schema[k] && schema[k].label) || k,
    [schema],
  );
  const estimateMax = (schema && schema.estimate && schema.estimate.max) || null;
  const assignees = (schema && schema.assigned_mr && schema.assigned_mr.choices) || [];

  // ── Persistence, debounced off the typing path ──────────────────────
  //
  // The cleanup FLUSHES, never drops. This component unmounts and remounts
  // whenever DataTable's rows empty and refill — which a post-submit refresh
  // does — and a dropped save let the remount reload a stale store and
  // resurrect rows that had just been submitted.
  useEffect(() => {
    const t = setTimeout(() => saveDrafts(who, drafts), 300);
    return () => { clearTimeout(t); saveDrafts(who, drafts); };
  }, [who, drafts]);

  const addRows = useCallback((n = 1) => {
    setDrafts((cur) => {
      const next = [...cur];
      for (let i = 0; i < n; i += 1) next.push(newDraft(next[next.length - 1] || null));
      return next;
    });
  }, []);

  // How the page opens the band: it registers this handle even while empty,
  // because returning null below still mounts the component.
  useEffect(() => {
    if (openRef) openRef.current = { addRows, count: drafts.length };
  }, [openRef, addRows, drafts.length]);

  const setCell = useCallback((r, k, val) => {
    setDrafts((cur) => {
      const next = [...cur];
      if (!next[r]) return cur;
      const v = { ...next[r].v, [k]: val };
      const carry = { ...next[r].carry };
      delete carry[k];
      next[r] = { ...next[r], v, carry };
      return next;
    });
    setServerErrs((e) => (e[r] ? { ...e, [r]: undefined } : e));
  }, []);

  const removeRow = useCallback((r) => {
    setDrafts((cur) => cur.filter((_, i) => i !== r));
  }, []);

  const discardAll = useCallback(() => {
    setDrafts([]); setDups({}); setServerErrs({});
  }, []);

  // ── Repeated links ───────────────────────────────────────────────────
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
    const pairs = drafts.map((d) => ({ link_url: d.v.link_url || '', purpose: d.v.purpose || '' }));
    if (!pairs.some((p) => p.link_url.trim())) { setDups({}); return undefined; }
    let alive = true;
    const t = setTimeout(() => {
      ticketsApi.checkLinks(pairs).then((res) => {
        if (!alive) return;
        const out = {};
        res.forEach((v, i) => { if (v && v.severity) out[i] = v; });
        setDups(out);
      }).catch(() => { /* Submit re-checks server-side */ });
    }, 450);
    return () => { alive = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // ── Validation: every cell's issue, in one pass ─────────────────────
  const issues = useMemo(() => {
    const out = {};
    drafts.forEach((d, r) => {
      if (notStarted(d)) return;
      const row = {};
      FIELD_ORDER.forEach((k) => {
        const val = (d.v[k] || '').trim();
        const srv = serverErrs[r] && serverErrs[r][k];
        if (srv) { row[k] = { kind: 'bad', blocks: true, msg: Array.isArray(srv) ? srv.join(' ') : String(srv) }; return; }
        if (F[k].req && !val) { row[k] = { kind: 'warn', blocks: true, msg: `${labelOf(k)} is needed before this ticket can be submitted` }; return; }
        if (k === 'estimate' && val && !/^\d+$/.test(val)) { row[k] = { kind: 'bad', blocks: true, msg: 'Estimate takes a whole number' }; return; }
        if (k === 'estimate' && val && Number(val) === 0) { row[k] = { kind: 'bad', blocks: true, msg: 'Estimate has to be above zero' }; return; }
        if (k === 'estimate' && val && estimateMax != null && Number(val) > estimateMax) { row[k] = { kind: 'bad', blocks: true, msg: `Estimate cannot exceed ${estimateMax.toLocaleString()}` }; return; }
        if (k === 'link_url' && val && !/^https?:\/\//i.test(val)) { row[k] = { kind: 'bad', blocks: true, msg: 'A link starts with http or https' }; return; }
        if (k === 'link_url' && localDups[r]) { row[k] = { kind: 'bad', blocks: true, msg: `Same link and purpose as row ${localDups[r]} of this batch` }; return; }
        if ((k === 'link_url' || k === 'purpose') && dups[r]) {
          const i = dupIssue(dups[r], k === 'purpose');
          if (i) row[k] = i;
        }
      });
      out[r] = row;
    });
    return out;
  }, [drafts, serverErrs, localDups, dups, labelOf, estimateMax]);

  const stats = useMemo(() => {
    let staged = 0; let blocked = 0; let warn = 0; let first = '';
    drafts.forEach((d, r) => {
      if (notStarted(d)) return;
      staged += 1;
      let rowBlocked = false; let rowWarn = false; let msg = '';
      Object.values(issues[r] || {}).forEach((i) => {
        if (i.blocks) rowBlocked = true; else rowWarn = true;
        if (!msg) msg = i.msg;
      });
      if (rowBlocked) blocked += 1; else if (rowWarn) warn += 1;
      if (msg && !first) first = `Row ${r + 1}. ${msg}`;
    });
    return { staged, blocked, warn, first };
  }, [drafts, issues]);

  const rowState = useCallback((r) => {
    if (!drafts[r] || notStarted(drafts[r])) return '';
    const all = Object.values(issues[r] || {});
    if (all.some((i) => i.kind === 'bad')) return 'bad';
    return all.length ? 'warn' : 'ok';
  }, [drafts, issues]);

  // ── Keyboard: the two spreadsheet habits that survive native inputs ─
  //
  // Tab is the browser's own and needs no code. Enter moves DOWN the same
  // column, growing the band off the last row; Ctrl+D pulls the value from the
  // row above into the focused field.
  const focusCell = useCallback((r, k) => {
    const el = rootRef.current
      && rootRef.current.querySelector(`[data-r="${r}"][data-k="${k}"]`);
    if (el) el.focus();
  }, []);

  const onKeyDown = (r, k) => (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'd' || e.key === 'D')) {
      e.preventDefault();
      if (r > 0) setCell(r, k, drafts[r - 1].v[k] || '');
      return;
    }
    if (e.key === 'Enter' && e.target.tagName !== 'SELECT') {
      e.preventDefault();
      if (r === drafts.length - 1) addRows(1);
      // The next row may not exist until React commits, hence the frame's delay.
      requestAnimationFrame(() => focusCell(r + 1, k));
    }
  };

  /** A pasted block spreads across fields and rows, in the band's own order. */
  const onPaste = (r, k) => (e) => {
    const text = e.clipboardData && e.clipboardData.getData('text/plain');
    if (!text || (!text.includes('\t') && !text.includes('\n'))) return;
    e.preventDefault();
    const lines = text.replace(/\r\n|\r/g, '\n').split('\n');
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    const c0 = FIELD_ORDER.indexOf(k);
    setDrafts((cur) => {
      const next = cur.map((d) => ({ ...d, v: { ...d.v }, carry: { ...d.carry } }));
      while (next.length < r + lines.length) next.push(newDraft(next[next.length - 1] || null));
      lines.forEach((line, i) => {
        line.split('\t').forEach((cell, j) => {
          const key = FIELD_ORDER[c0 + j];
          if (!key) return;
          next[r + i].v[key] = cell.trim();
          delete next[r + i].carry[key];
        });
      });
      return next;
    });
    toast(`Pasted ${lines.length} ${lines.length === 1 ? 'row' : 'rows'}`, 'ok');
  };

  // ── Submit ──────────────────────────────────────────────────────────
  const submit = async () => {
    const payload = []; const rowMap = [];
    drafts.forEach((d, i) => {
      if (notStarted(d)) return;
      const body = {};
      FIELD_ORDER.forEach((k) => {
        const raw = (d.v[k] || '').trim();
        if (!raw) return;
        if (k === 'estimate') body[k] = Number(raw);
        // <input type="month"> gives YYYY-MM; the model stores a date, and the
        // column only ever shows month and year, so it becomes the first.
        else if (k === 'event_month_year') body[k] = raw.length === 7 ? `${raw}-01` : raw;
        else body[k] = raw;
      });
      payload.push(body);
      rowMap.push(i);
    });
    if (!payload.length) { toast('Nothing to submit yet', 'wn'); return; }

    setSaving(true);
    setServerErrs({});
    try {
      const res = await ticketsApi.bulkCreate(payload);
      const made = (res.created || []).length;
      // Rows leave by identity, not index: a row added while the request is in
      // flight must not shift which ones are removed.
      const submitted = new Set(rowMap.map((i) => drafts[i].key));
      const remaining = drafts.filter((d) => !submitted.has(d.key));
      saveDrafts(who, remaining);   // synchronously, BEFORE the table refresh
      setDrafts((cur) => cur.filter((d) => !submitted.has(d.key)));
      setDups({});
      toast(`${made} ${made === 1 ? 'ticket' : 'tickets'} submitted`, 'ok');
      onCreated && onCreated();
    } catch (err) {
      const data = err && err.response && err.response.data;
      if (data && data.errors) {
        const mapped = {};
        Object.keys(data.errors).forEach((k) => {
          const row = rowMap[Number(k)];
          if (row != null) mapped[row] = data.errors[k];
        });
        setServerErrs(mapped);
        toast(data.detail || 'Some rows need attention', 'er');
      } else {
        toast((data && data.detail) || 'Could not submit the batch', 'er');
      }
    } finally {
      setSaving(false);
    }
  };

  // Only unsubmitted rows keep the band open.
  if (!drafts.length) return null;

  // ── One native control per cell ─────────────────────────────────────
  const control = (d, r, k) => {
    const issue = (issues[r] || {})[k];
    const cls = 'eg-in'
      + (issue ? ` ${issue.kind}` : '')
      + (d.carry[k] && (d.v[k] || '').trim() ? ' inh' : '');
    const common = {
      className: cls,
      value: d.v[k] || '',
      title: issue ? issue.msg
        : d.carry[k] && (d.v[k] || '').trim() ? 'Carried from the row above; type to change it' : undefined,
      'data-r': r,
      'data-k': k,
      onChange: (e) => setCell(r, k, e.target.value),
      onKeyDown: onKeyDown(r, k),
      onPaste: onPaste(r, k),
    };
    const selectOf = (opts) => (
      <select {...common}>
        <option value="">—Select—</option>
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
        {/* A stored value not on the list must not display as blank. */}
        {d.v[k] && !opts.includes(d.v[k]) ? <option value={d.v[k]}>{d.v[k]}</option> : null}
      </select>
    );
    switch (k) {
      case 'type_of_ticket': return selectOf(TK_TYPES);
      case 'relationship': return selectOf(TK_RELATIONSHIPS);
      case 'priority': return selectOf(Object.keys(TK_PRIORITY));
      case 'assigned_mr': return selectOf(assignees);
      case 'purpose': return (
        <>
          <input {...common} list="eg-purposes" placeholder={F[k].ph} autoComplete="off" />
          {r === 0 ? (
            <datalist id="eg-purposes">
              {purposeOpts.map((o) => <option key={o} value={o} />)}
            </datalist>
          ) : null}
        </>
      );
      case 'event_month_year': return <input {...common} type="month" />;
      case 'estimate': return (
        <input {...common} type="number" min="1" max={estimateMax || undefined}
          placeholder="0" inputMode="numeric" />
      );
      case 'link_url': return (
        <input {...common} type="url" placeholder={F[k].ph}
          inputMode="url" autoCapitalize="none" spellCheck={false} autoComplete="off" />
      );
      default: return <input {...common} type="text" placeholder={F[k].ph || ''} />;
    }
  };

  return (
    <div className="eg-band" ref={rootRef}>
      <div className="eg-scroll">
        <table className="eg-t">
          <colgroup>
            <col style={{ width: 46 }} />
            {FIELD_ORDER.map((k) => <col key={k} style={{ width: F[k].w }} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="eg-gut" aria-label="Row" />
              {FIELD_ORDER.map((k) => (
                <th key={k}>{labelOf(k)}{F[k].req ? <span className="eg-rq">*</span> : null}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {drafts.map((d, r) => (
              <tr key={d.key}>
                <td className="eg-gut">
                  <span className={'eg-dot ' + rowState(r)} title={rowState(r) || 'not started yet'} />
                  <span className="eg-rn">{r + 1}</span>
                  <button type="button" className="eg-x" aria-label={`Discard row ${r + 1}`}
                    title="Discard this row" onClick={() => removeRow(r)}>
                    <Icon name="x" size={11} />
                  </button>
                </td>
                {FIELD_ORDER.map((k) => <td key={k}>{control(d, r, k)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="eg-foot">
        <span className="eg-n"><b>{stats.staged}</b> {stats.staged === 1 ? 'row' : 'rows'} to submit</span>
        {stats.blocked ? <span className="tg bg-red">{stats.blocked} to fix</span> : null}
        {stats.warn && !stats.blocked ? <span className="tg bg-amber">{stats.warn} to check, will submit</span> : null}
        <span className="eg-hint">{stats.first}</span>
        <span className="eg-keys">
          <kbd>Enter</kbd> next row
          <kbd>Tab</kbd> next field
          <kbd>Ctrl</kbd><kbd>D</kbd> copy from above
          <kbd>Ctrl</kbd><kbd>V</kbd> paste a block
        </span>
        <div className="eg-acts">
          <button type="button" className="btn btn-s btn-sm" onClick={() => addRows(1)}>
            <Icon name="plus" size={13} />Row
          </button>
          <button type="button" className="btn btn-s btn-sm" onClick={discardAll}
            title="Discard every unsubmitted row">
            Discard
          </button>
          <button type="button" className="btn btn-p btn-sm" onClick={submit}
            disabled={saving || !stats.staged || !!stats.blocked}>
            <Icon name="send" size={13} />
            {saving ? 'Submitting…' : `Submit ${stats.staged || ''}`.trim()}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * One reading of a server duplicate verdict, on the link cell and the purpose
 * cell, because the purpose is half of what makes a repeat a repeat. `blocks` is
 * the rule, `kind` only the colour: a same-purpose repeat inside the 90-day
 * window refuses the submit, the other two are advice and go through.
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
      kind: 'bad', blocks: true,
      msg: `Already raised under the same purpose ${purpose}, as ${where} ${when}. Change the purpose, or work that ticket instead.`,
    };
  }
  if (first.same_purpose) {
    return {
      kind: 'warn', blocks: false,
      msg: `Raised under this same purpose before, as ${where}${more}, but that was ${when}, so it will submit.`,
    };
  }
  return {
    kind: 'warn', blocks: false,
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

/**
 * Memoised because DataTable re-renders once per animation frame while its rows
 * are scrolled, and calls the entryBand render prop on each one.
 */
export default memo(TicketEntryRows);
