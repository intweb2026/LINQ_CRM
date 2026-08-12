import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Lightweight anchored popover — replaces the legacy openPop()/closePop() pair.
// `trigger` renders the button ({open, toggle}); `children` renders the panel
// content ({close}). Closes on outside click or Escape.
// The panel renders through a portal at a fixed, viewport-computed position so it
// is never clipped by a scrolling ancestor (e.g. a horizontally-scrollable table).
// Position is recomputed on scroll/resize so the panel always stays visually
// anchored to its trigger — it must track the button, not freeze in place.
export default function Popover({ trigger, children, align = 'left', width, panelClassName }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const anchorRef = useRef(null);
  const panelRef = useRef(null);

  function place() {
    const el = anchorRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos(align === 'right'
      ? { top: r.bottom + 6, right: window.innerWidth - r.right }
      : { top: r.bottom + 6, left: r.left });
  }

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
        <div ref={panelRef} className={'pop' + (panelClassName ? ' ' + panelClassName : '')} style={{ position: 'fixed', top: pos.top, left: pos.left, right: pos.right, width, zIndex: 160 }}>
          {children({ close: () => setOpen(false) })}
        </div>,
        document.body
      ) : null}
    </div>
  );
}
