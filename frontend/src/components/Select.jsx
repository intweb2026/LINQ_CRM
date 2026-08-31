import { useEffect, useMemo, useRef, useState } from 'react';
import Popover from './Popover';
import { useMenuNav } from '../lib/menuNav';
import { Icon } from '../lib/icons';

// Custom-styled dropdown replacing native <select> in forms — the OS-native
// option list can't be restyled with CSS (see DelegateTable.jsx), so this
// renders its own themed menu via the existing Popover primitive instead.
//
// `labelOf` separates what an option IS from what it READS AS: the callback gets
// the stored value and returns the wording to show, so a picklist can be relabelled
// without touching the value it writes (Paid → "Payable", see lib/constants.js).
// Omitted, the option is its own label, which is how every other caller uses this.
//
// `search` adds a filter box at the top of the open menu, for pickers whose list
// is too long to scan (event codes, see NewBookingModal.jsx). It filters only;
// the typed text is never a value, so the committed value still comes from a row
// the user clicked. `subOf` returns an optional second line per option, which is
// how an event code shows its event name without widening the trigger.
export default function Select({ value, options, onChange, className = 'in', placeholder = 'Select…', width, labelOf, search = false, searchPlaceholder = 'Search…', subOf, emptyText = 'No matches' }) {
  const text = labelOf || ((o) => o);
  // Only an absent value shows the placeholder. A truthiness test would hide a
  // legitimate 0 — Delegate Number offers 0 and 1 (see lib/constants.js).
  const empty = value === '' || value === null || value === undefined;
  return (
    <Popover
      width={width}
      trigger={({ toggle, open }) => (
        <button type="button" className={className + ' sel-trigger' + (open ? ' open' : '')} onClick={toggle}>
          <span className="sel-v">{empty ? <span className="dim">{placeholder}</span> : text(value)}</span>
          <Icon name="chevD" size={13} />
        </button>
      )}
    >
      {({ close }) => (
        <SelectMenu
          options={options} value={value} onChange={onChange} close={close}
          text={text} subOf={subOf}
          search={search} searchPlaceholder={searchPlaceholder} emptyText={emptyText}
        />
      )}
    </Popover>
  );
}

// A child component, not inline JSX, so the query lives and dies with one opening
// of the menu: Popover mounts its children only while open, so reopening the
// dropdown always starts from the full list rather than the last search.
function SelectMenu({ options, value, onChange, close, text, subOf, search, searchPlaceholder, emptyText }) {
  const [query, setQuery] = useState('');

  // Matched against the option VALUE, not its label or sub-line: for the event
  // code picker the code is what people type, and matching the name too would
  // surface rows whose visible code has nothing to do with the query.
  const shown = useMemo(() => {
    if (!search) return options;
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => String(o).toLowerCase().includes(q));
  }, [options, query, search]);

  const pick = (o) => { onChange(o); close(); };
  const nav = useMenuNav(shown.length, (i) => pick(shown[i]));

  // With a search box its autoFocus puts focus inside the menu and keydown
  // bubbles up from there. Without one, focus stays on the trigger, which is
  // outside this subtree, so the wrapper would never see the arrows — focus it.
  const wrapRef = useRef(null);
  useEffect(() => { if (!search) wrapRef.current?.focus(); }, [search]);

  return (
    // The handler sits above both the search box and the rows so it catches the
    // arrows wherever focus is. tabIndex makes the div focusable for the
    // no-search case, where nothing inside would otherwise hold focus.
    <div ref={wrapRef} onKeyDown={nav.onKeyDown} tabIndex={-1} className="sel-menu">
      {search ? (
        <div className="sel-search">
          <input
            className="in in-xs in-s" type="search" autoFocus
            value={query} placeholder={searchPlaceholder}
            onChange={(e) => setQuery(e.target.value)}
            // Enter inside a modal form would otherwise submit it, and the
            // typed text is a filter, never a value, so it must not commit
            // either. preventDefault only — the event still bubbles to the
            // wrapper, which commits the highlighted row if the user arrowed
            // to one.
            onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault(); }}
          />
        </div>
      ) : null}
      <div className="pop-mx" ref={nav.boxRef}>
        {shown.length === 0 ? (
          <div className="sel-none">{emptyText}</div>
        ) : shown.map((o, i) => {
          const sub = subOf ? subOf(o) : null;
          return (
            <button
              type="button" key={o} data-nav={i}
              className={'pop-i' + (sub ? ' pop-i-2' : '') + (i === nav.idx ? ' cur' : '')}
              // Mouse and keyboard share one highlight, so moving the pointer
              // never leaves a second row looking selected.
              onMouseEnter={() => nav.setIdx(i)}
              onClick={() => pick(o)}
            >
              {o === value ? <Icon name="check" size={14} /> : <span style={{ width: 14, flexShrink: 0 }} />}
              <span className="sel-o">
                {/* A blank option is a real choice on some pickers (Payment Status,
                    Payment Type), and it must be clickable — text('') renders an
                    empty row nobody can see or aim at. */}
                <span className="sel-o-t">{text(o) === '' ? <span className="dim">Blank</span> : text(o)}</span>
                {sub ? <span className="sel-o-s">{sub}</span> : null}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
