import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../lib/icons';

/**
 * The event selector. NOT a dropdown.
 *
 * WHY NOT. There are 241 pickable events and each one needs four facts to be
 * identified — the internal code, the event name, where it is and when. A
 * <select> or the themed Select can show one line of text per option, so
 * picking the right edition of a recurring code means guessing.
 *
 * So it is a trigger that displays the CURRENT event in full, and opens a
 * searchable overlay of cards. Search matches the code, the name and the
 * location together, because people arrive knowing any one of the three.
 *
 * Keyboard once open: type to filter, arrows to move, Enter to pick, Escape to
 * close. The search box takes focus on open, so all four work without reaching
 * for the mouse.
 *
 * An event with no exact row in the catalogue shows its CODE rather than the
 * words "Unnamed event". 84 of 241 are in that position, because the delegates
 * table holds a bare family code where the catalogue holds a per-edition one.
 * They are deliberately NOT matched up: doing so means choosing an edition on
 * the user's behalf, and a wrong event name on a badge report header is worse
 * than none. See the note in services.event_meta.
 */

const fmt = (from, to) => {
  if (!from) return '';
  const d = (v) => new Date(v).toLocaleDateString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric' });
  if (!to || to === from) return d(from);
  return `${new Date(from).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} – ${d(to)}`;
};

export default function EventPicker({ events, value, onPick }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef(null);

  const key = (e) => (e ? `${e.event_code}#${e.edition || ''}` : '');

  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return events;
    return events.filter((e) => (
      `${e.event_code} ${e.edition || ''} ${e.event_name} ${e.location} ${e.venue}`
        .toLowerCase().includes(t)
    ));
  }, [events, q]);

  useEffect(() => { setIdx(0); }, [q, open]);
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 10);
    else setQ('');
  }, [open]);

  function choose(e) {
    onPick(e);
    setOpen(false);
  }

  return (
    <>
      <button className="ped-ev-trigger" onClick={() => setOpen(true)}
        title="Change event">
        {value ? (
          <>
            <span className="ped-ev-code">{value.event_code}</span>
            <span className="ped-ev-meta">
              <b>{value.event_name || value.event_code}</b>
              <span>
                {[value.location, fmt(value.event_date, value.end_date)]
                  .filter(Boolean).join('  ·  ')
                  || 'Not in the Events catalogue'}
              </span>
            </span>
          </>
        ) : <span className="ped-ev-meta"><b>Choose an event</b></span>}
        <Icon name="chevD" size={14} />
      </button>

      {open ? (
        <>
          <div className="modal-scrim show" onClick={() => setOpen(false)} />
          <div className="ped-ev-pop" role="dialog" aria-modal="true"
            aria-label="Choose an event">
            <div className="ped-ev-search">
              <Icon name="filter" size={14} />
              <input
                ref={inputRef} className="in" autoComplete="off"
                placeholder="Search code, name or place…"
                value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, hits.length - 1)); }
                  else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
                  else if (e.key === 'Enter') { e.preventDefault(); if (hits[idx]) choose(hits[idx]); }
                  else if (e.key === 'Escape') setOpen(false);
                }}
              />
              <span className="ped-ev-count">{hits.length}</span>
            </div>
            <div className="ped-ev-list">
              {hits.length ? hits.map((e, i) => (
                <button
                  key={key(e)}
                  className={'ped-ev-row'
                    + (i === idx ? ' cur' : '')
                    + (key(e) === key(value) ? ' on' : '')}
                  onMouseEnter={() => setIdx(i)}
                  onClick={() => choose(e)}
                >
                  <span className="ped-ev-code">{e.event_code}</span>
                  <span className="ped-ev-rmeta">
                    <b>{e.event_name || e.event_code}</b>
                    <span>
                      {[e.location, fmt(e.event_date, e.end_date)]
                        .filter(Boolean).join('  ·  ')}
                    </span>
                  </span>
                  <span className="ped-ev-n">{e.delegates}</span>
                </button>
              )) : <p className="ped-none">No event matches that.</p>}
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
