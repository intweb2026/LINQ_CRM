import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../lib/icons';
import { NAV_FLAT } from '../lib/nav';
import * as searchApi from '../api/search';
import { useSession } from '../context/SessionContext';

export default function CommandPalette({ open, onClose }) {
  const { canView } = useSession();
  const nav = useNavigate();
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef(null);

  // Entity results come from the server's own search endpoint, debounced, and
  // ONLY while the palette is open with at least MIN_QUERY characters typed.
  //
  // This previously held five useFetch calls - bookings, events, tickets, users,
  // teams - each a fetchAllPages walk, and they ran on EVERY route: the
  // `if (!open) return null` below sits after the hooks, and CommandPalette is
  // mounted permanently by AppShell. Measured, that was ~50 requests and ~49,000
  // rows deserialised per navigation, for a palette the user had not opened. It
  // was the single largest contributor to the reported "backend running in a loop".
  //
  // Server-side is also the only correct place for it: /api/search/ is RBAC-scoped,
  // whereas filtering rows the browser happens to hold can only ever search what
  // was already downloaded.
  const [hits, setHits] = useState(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => { if (open) { setQ(''); setIdx(0); setHits(null); setTimeout(() => inputRef.current?.focus(), 10); } }, [open]);

  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < searchApi.MIN_QUERY) { setHits(null); setSearching(false); return undefined; }
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(() => {
      searchApi.global(term)
        .then((d) => { if (!cancelled) setHits(d.results || {}); })
        .catch(() => { if (!cancelled) setHits({}); })
        .finally(() => { if (!cancelled) setSearching(false); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q, open]);

  const results = useMemo(() => {
    const v = q.trim().toLowerCase();
    const res = [];
    NAV_FLAT.forEach((i) => {
      if (i.mod && !canView(i.mod)) return;
      if (!v || i.l.toLowerCase().includes(v)) res.push({ t: 'Navigate', l: i.l, s: '', ic: i.ic, go: () => nav(i.path) });
    });
    // Buckets are omitted by the backend when the caller cannot see that type
    // (companies are admin-only), so every read here is guarded.
    const b = hits || {};
    const join = (parts) => parts.filter(Boolean).join(' \u00b7 ');
    (b.delegates?.items || []).slice(0, 5).forEach((d) => res.push({
      t: 'Bookings', l: join([d.full_name, d.company_display]),
      s: join([d.invoice_number, d.event_code]), ic: 'receipt', go: () => nav('/bookings'),
    }));
    (b.invoices?.items || []).slice(0, 4).forEach((i) => res.push({
      t: 'Invoices', l: join([i.invoice_number, i.company_name]),
      s: join([i.event_code, i.payment_status]), ic: 'receipt', go: () => nav('/bookings'),
    }));
    (b.events?.items || []).slice(0, 5).forEach((e) => res.push({
      t: 'Events', l: e.name, s: join([e.event_code, e.location]), ic: 'calendar', go: () => nav('/events'),
    }));
    (b.companies?.items || []).slice(0, 4).forEach((c) => res.push({
      t: 'Companies', l: c.name, s: join([c.city, c.country]), ic: 'building', go: () => nav('/companies'),
    }));
    return res.slice(0, 16);
  }, [q, canView, nav, hits]);

  useEffect(() => { setIdx(0); }, [results.length]);

  if (!open) return null;

  function pick(r) { onClose(); r.go(); }

  let lastGroup = '';
  return (
    <div className="plw show" onClick={onClose}>
      <div className="pl-m" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef} className="pl-in" placeholder="Search invoice, delegate, company, event, ticket…" autoComplete="off" aria-label="Search"
          value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, results.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
            else if (e.key === 'Enter') { e.preventDefault(); if (results[idx]) pick(results[idx]); }
            else if (e.key === 'Escape') onClose();
          }}
        />
        <div className="pl-ls">
          {!results.length ? (
            <div style={{ padding: '26px 16px', textAlign: 'center', color: 'var(--text-4)', fontSize: 12.5 }}>
              {searching ? 'Searching' + '\u2026'
                : q.trim().length < searchApi.MIN_QUERY ? 'Type at least ' + searchApi.MIN_QUERY + ' characters to search'
                : 'No matches for "' + q + '"'}
            </div>
          ) : results.map((r, i) => {
            let groupLabel = null;
            if (r.t !== lastGroup) { lastGroup = r.t; groupLabel = r.t; }
            return (
              <div key={i}>
                {groupLabel ? <div className="pl-g">{groupLabel}</div> : null}
                <div className={'pl-i2' + (i === idx ? ' cur' : '')} onMouseEnter={() => setIdx(i)} onClick={() => pick(r)}>
                  <span className="pl-ic"><Icon name={r.ic} size={13} /></span>
                  <span className="pl-bd"><span className="pl-t2">{r.l}</span><span className="pl-s2">{r.s}</span></span>
                  <span className="pl-go">Open →</span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="pl-f"><span><kbd>↑</kbd><kbd>↓</kbd>Navigate</span><span><kbd>↵</kbd>Open</span><span><kbd>esc</kbd>Close</span></div>
      </div>
    </div>
  );
}
