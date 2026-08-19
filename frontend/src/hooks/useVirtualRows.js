import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/**
 * The slice of `rows` currently inside the scroll container, plus pixel heights
 * to pad above and below it so the scrollbar reflects the full set.
 *
 * WHY SPACER <tr> AND NOT ABSOLUTE POSITIONING
 * The table owns a <colgroup> and a sticky <thead>. Taking rows out of table
 * layout and positioning them absolutely breaks column alignment because the
 * browser can no longer distribute column widths across header and body. A
 * spacer <tr> with one <td colSpan={colCount}> keeps the table a table.
 *
 * DISABLED BELOW THRESHOLD
 * At 50 rows the windowing overhead costs more than it saves, and every
 * in-memory table in the app is under it. Below the threshold this returns the
 * input array by reference, so a caller renders exactly what it rendered before
 * and the paged and in-memory modes are untouched.
 *
 * @param {Array}   rows       — the full set to virtualise over
 * @param {Object}  scrollRef  — a React ref to the scroll container element
 * @param {number}  rowHeight  — measured row height in pixels (see DataTable.jsx)
 * @param {Object}  opts
 * @param {number}  opts.threshold — row count below which virtualisation is off
 * @param {number}  opts.overscan  — extra rows above and below the viewport
 */
export default function useVirtualRows(rows, scrollRef, rowHeight, {
  threshold = 120,
  overscan = 14,
} = {}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [clientH, setClientH] = useState(800);
  const rafRef = useRef(null);

  const onScroll = useCallback(() => {
    // One state update per animation frame. A raw scroll handler fires far more
    // often than the browser paints, and each one here is a React re-render of
    // the table body.
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const el = scrollRef.current;
      if (el) setScrollTop(el.scrollTop);
    });
  }, [scrollRef]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    el.addEventListener('scroll', onScroll, { passive: true });
    // ResizeObserver rather than a window resize listener: .tsc is
    // max-height:calc(100vh - 300px) on desktop and uncapped under 880px, so its
    // height also changes when surrounding chrome does, not only on window
    // resize.
    const ro = new ResizeObserver(() => {
      setClientH(el.clientHeight || 800);
    });
    ro.observe(el);
    setClientH(el.clientHeight || 800);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // rows.length is a dependency because the scroll container is only mounted
    // once rows exist; re-running on the count picks up the element the first
    // time it appears, and re-measures after the set grows.
  }, [scrollRef, onScroll, rows.length]);

  return useMemo(() => {
    if (!rows.length || rows.length < threshold) {
      return { slice: rows, padTop: 0, padBottom: 0, virtual: false };
    }
    const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const visible = Math.ceil(clientH / rowHeight) + overscan * 2;
    const last = Math.min(rows.length, first + visible);
    return {
      slice: rows.slice(first, last),
      padTop: first * rowHeight,
      padBottom: Math.max(0, (rows.length - last) * rowHeight),
      virtual: true,
    };
  }, [rows, scrollTop, clientH, rowHeight, threshold, overscan]);
}
