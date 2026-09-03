import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Lightweight anchored popover — replaces the legacy openPop()/closePop() pair.
// `trigger` renders the button ({open, toggle}); `children` renders the panel
// content ({close}). Closes on outside click or Escape.
// The panel renders through a portal at a fixed, viewport-computed position so it
// is never clipped by a scrolling ancestor (e.g. a horizontally-scrollable table).
// Position is recomputed on scroll/resize so the panel always stays visually
// anchored to its trigger — it must track the button, not freeze in place.
/**
 * Keeps a fixed panel inside the viewport horizontally. It mirrors the maxH cap;
 * a `position: fixed` panel hanging past either edge is UNREACHABLE, no page
 * scroll brings it back. `offset` is measured from the same edge the panel is
 * anchored to, so this serves both alignments. 8px of breathing room each side.
 */
export function fitX(offset, panelW, viewportW) {
  return Math.max(8, Math.min(offset, viewportW - panelW - 8));
}

export default function Popover({ trigger, children, align = 'left', width, panelClassName, openRef }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const anchorRef = useRef(null);
  const panelRef = useRef(null);

  function place() {
    const el = anchorRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // The panel is position:fixed, so whatever falls past the bottom of the
    // viewport is unreachable — no scrollbar on the page brings it back. This is
    // the only place that CAN cap it: the cap depends on where the trigger sits,
    // which is exactly what place() has just measured, and it is re-measured on
    // scroll and resize like the rest of the position. A panel tall enough to
    // reach the cap scrolls inside itself (see .pop-lg).
    const maxH = Math.max(220, window.innerHeight - (r.bottom + 6) - 12);
    // The horizontal cap, for the same reason; see fitX. The column-header
    // filters on the LAST columns of a wide table open with their trigger hard
    // against the right edge, which put the operator select and the value box
    // off screen entirely, with no way to reach them. Width is MEASURED, not
    // assumed, because .pop is sized by its content above a 228px floor; only
    // the mounted panel knows how wide it actually is.
    const w = panelRef.current ? panelRef.current.offsetWidth : 228;
    const next = align === 'right'
      ? { top: r.bottom + 6, right: fitX(window.innerWidth - r.right, w, window.innerWidth), maxH }
      : { top: r.bottom + 6, left: fitX(r.left, w, window.innerWidth), maxH };
    // Same position in, same object out. `scroll` is listened for in CAPTURE
    // phase, so this runs for every scroll anywhere in the page — including the
    // panel's own list — and a fresh object each time re-rendered the entire
    // panel on every scroll frame. That churn is what made controls inside the
    // panel shift underneath an open dropdown mid-click.
    setPos((prev) => (prev
      && prev.top === next.top && prev.left === next.left && prev.right === next.right
      && prev.maxH === next.maxH
      ? prev : next));
  }

  // Opens the panel from somewhere other than its own trigger — DataTable's
  // filter chips open the filter panel, which stays anchored to the toolbar
  // button rather than to the chip that was clicked.
  useEffect(() => {
    if (!openRef) return undefined;
    openRef.current = () => setOpen(true);
    return () => { openRef.current = null; };
  }, [openRef]);

  // A layout effect, not a plain effect. The panel is mounted but unpositioned
  // on this commit; placing it after the browser has painted flashes it at 0,0.
  useLayoutEffect(() => {
    if (!open) return undefined;
    place();
    function onDown(e) {
      if (anchorRef.current && anchorRef.current.contains(e.target)) return;
      if (panelRef.current && panelRef.current.contains(e.target)) return;
      setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    function onReposition() { place(); }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, align]);

  return (
    <div ref={anchorRef} style={{ position: 'relative', display: 'inline-block' }}>
      {trigger({ open, toggle: () => setOpen((o) => !o) })}
      {open ? createPortal(
        /* Rendered before it is positioned, and invisible until it is. place()
           has to MEASURE the real panel to keep it inside the viewport, and it
           can only measure one already in the DOM. place() runs in a layout
           effect, so this unplaced pass never reaches the screen. Transparent
           rather than `visibility: hidden`, which would silently swallow the
           focus a panel takes on mount. */
        <div ref={panelRef} className={'pop' + (panelClassName ? ' ' + panelClassName : '')} style={{ position: 'fixed', top: pos ? pos.top : 0, left: pos ? pos.left : undefined, right: pos ? pos.right : undefined, width, maxHeight: pos ? pos.maxH : undefined, zIndex: 160, opacity: pos ? undefined : 0 }}>
          {children({ close: () => setOpen(false) })}
        </div>,
        document.body
      ) : null}
    </div>
  );
}
