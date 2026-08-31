import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Lightweight anchored popover — replaces the legacy openPop()/closePop() pair.
// `trigger` renders the button ({open, toggle}); `children` renders the panel
// content ({close}). Closes on outside click or Escape.
// The panel renders through a portal at a fixed, viewport-computed position so it
// is never clipped by a scrolling ancestor (e.g. a horizontally-scrollable table).
// Position is recomputed on scroll/resize so the panel always stays visually
// anchored to its trigger — it must track the button, not freeze in place.
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
    const next = align === 'right'
      ? { top: r.bottom + 6, right: window.innerWidth - r.right, maxH }
      : { top: r.bottom + 6, left: r.left, maxH };
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

  useEffect(() => {
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
      {open && pos ? createPortal(
        <div ref={panelRef} className={'pop' + (panelClassName ? ' ' + panelClassName : '')} style={{ position: 'fixed', top: pos.top, left: pos.left, right: pos.right, width, maxHeight: pos.maxH, zIndex: 160 }}>
          {children({ close: () => setOpen(false) })}
        </div>,
        document.body
      ) : null}
    </div>
  );
}
