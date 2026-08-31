/**
 * lib/menuNav.js
 * ──────────────
 * Up/Down highlight for the custom dropdown menus.
 *
 * These menus replaced native <select>, whose arrow-key behaviour the OS gave us
 * for free (the option list can't be restyled, see the note atop Select.jsx).
 * Nothing reimplemented it, so Escape closed a menu and Tab crawled through it
 * but the arrows did nothing — and where a menu autofocuses a search box, they
 * moved the text caret instead, which reads as a dead key. CommandPalette had
 * its own copy of this; these are the same three lines, shared.
 *
 * `idx` starts at NOTHING, not at 0. OptsPicker's Enter already means "commit
 * what I typed", so a highlight that existed before the user pressed an arrow
 * would hijack it and commit a row they never aimed at. Enter only takes the
 * highlighted row once there IS one.
 */
import { useEffect, useRef, useState } from 'react';

export const NONE = -1;

/**
 * Pure so it can be tested without a DOM. Down from nothing lands on the first
 * row, Up from nothing on the last, which is what a native select does. Both
 * ends clamp rather than wrap: wrapping past the end of a long list (the event
 * code picker runs to hundreds) loses people.
 */
export function nextNavIdx(key, idx, count) {
  if (count <= 0) return NONE;
  if (key === 'ArrowDown') return idx < 0 ? 0 : Math.min(idx + 1, count - 1);
  if (key === 'ArrowUp') return idx < 0 ? count - 1 : Math.max(idx - 1, 0);
  return idx;
}

/**
 * Returns { idx, setIdx, boxRef, onKeyDown }.
 *
 * Put `boxRef` on the element wrapping the rows and `data-nav={i}` on each row,
 * so the highlighted one can be scrolled into view. Put `onKeyDown` on a parent
 * of both the rows and any search input: keydown bubbles, so one handler up
 * there catches the arrows whether focus sits in the input or on a row button.
 *
 * `onPick(idx)` fires on Enter, and only when a row is highlighted.
 */
export function useMenuNav(count, onPick) {
  const [idx, setIdx] = useState(NONE);
  const boxRef = useRef(null);

  // Filtering shrinks the list under the highlight; a stale index would leave
  // Enter pointing at a row that is no longer on screen.
  useEffect(() => { setIdx((i) => (i >= count ? NONE : i)); }, [count]);

  useEffect(() => {
    if (idx < 0) return;
    boxRef.current
      ?.querySelector(`[data-nav="${idx}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [idx]);

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();          // else the caret jumps, or the page scrolls
      setIdx((i) => nextNavIdx(e.key, i, count));
    } else if (e.key === 'Enter' && idx >= 0) {
      e.preventDefault();
      onPick(idx);
    }
  }

  return { idx, setIdx, boxRef, onKeyDown };
}
