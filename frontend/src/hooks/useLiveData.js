/**
 * useLiveData — keep what a component is showing current, without a page refresh.
 *
 * Three triggers, because "without a refresh" has three different causes and only
 * one of them is cheap to detect:
 *
 *  1. A WRITE, anywhere. api/client.js publishes every successful non-GET to
 *     lib/liveData.js, including from another tab. This is the instant path: the
 *     record you just added, or the one a colleague added in the next seat, lands
 *     in well under a second. It covers writes made on OTHER pages of the app too,
 *     which is what used to leave a mounted page stale.
 *
 *  2. A POLL, while the tab is visible. The only way to see a change this browser
 *     did not make: a webhook booking, the Google Sheets sync, a Django-admin
 *     edit, a cron job. Paused while hidden — a backgrounded tab polling an
 *     aggregate for an hour is load nobody is reading.
 *
 *  3. RETURNING to the tab. Coming back after twenty minutes away should not mean
 *     waiting out the rest of an interval, so focus refreshes immediately if the
 *     data is stale, and does nothing if it is not (a quick alt-tab is not a
 *     reason to re-run a query over 35,000 delegates).
 *
 * `refresh` should be a QUIET refetch — useFetch's refetchQuiet, or
 * useServerRows' refetch({ quiet: true }). A background refresh that flips a
 * loading flag makes pages flicker on their own, and one that sets an error flag
 * can blank a screen that was showing perfectly good data.
 */
import { useCallback, useEffect, useRef } from 'react';
import { LIVE_POLL_MS, pathTouches, subscribeDataChanged } from '../lib/liveData';

/**
 * Floor between two refreshes. A single user action can write several times — the
 * bulk "mark paid" PATCHes per row, an import POSTs one batch per 500 rows — and
 * each one publishes. Coalescing keeps that at one refetch instead of dozens.
 */
const MIN_GAP_MS = 1200;

/** How long to wait for a burst of writes to finish before refetching. */
const BURST_MS = 300;

/** Returning to the tab only refetches if the data is at least this stale. */
const FOCUS_STALE_MS = 10000;

const isVisible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';

/**
 * Visible AND focused.
 *
 * document.visibilityState alone treats a visible-but-unfocused window as
 * active, which is exactly the second monitor with Bookings open that nobody is
 * looking at. Combined with the poll interval, that is a recurring multi-row
 * query for nothing.
 *
 * GATES THE POLL TIMER ONLY. The write-bus subscription below is deliberately
 * NOT gated on this: a write arriving from another tab must still refresh
 * regardless of focus, because that is the instant path and the person who made
 * the write is watching this window for the result — often from the other tab,
 * which by definition means this one is unfocused.
 *
 * hasFocus() is guarded for the same reason visibilityState is: this module is
 * imported in environments without a document.
 */
const isTabActive = () => (
  typeof document === 'undefined'
    || (document.visibilityState !== 'hidden' && document.hasFocus())
);

export function useLiveData(refresh, options = {}) {
  const { resources = null, poll = LIVE_POLL_MS, enabled = true } = options;

  // Held in a ref so the interval and the subscription are not torn down and
  // rebuilt — restarting the clock — every time the caller re-renders with a new
  // closure. The webhook log page learned this the hard way; the comment there
  // says so, and this hook is where that lesson now lives for every page.
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  const lastRunRef = useRef(0);
  const burstRef = useRef(null);

  const run = useCallback((minGap) => {
    const now = Date.now();
    if (minGap > 0 && now - lastRunRef.current < minGap) return;
    lastRunRef.current = now;
    try {
      refreshRef.current();
    } catch { /* a refresher's own failure is not this hook's to report */ }
  }, []);

  /**
   * Refresh now, ignoring the gap — for a deliberate user action (a save, the
   * Refresh button). It also stamps the clock, so the echo of that same write
   * arriving over the bus a moment later does not refetch a second time.
   */
  const refreshNow = useCallback(() => run(0), [run]);

  /** Say "a refresh just happened" without performing one, for the same reason. */
  const markRefreshed = useCallback(() => { lastRunRef.current = Date.now(); }, []);

  // A string, so a caller passing a fresh array literal every render does not
  // resubscribe on every render.
  const key = resources === null ? '*' : [].concat(resources).join('|');

  useEffect(() => {
    if (!enabled) return undefined;
    const list = key === '*' ? null : key.split('|');
    const off = subscribeDataChanged((path) => {
      if (!pathTouches(path, list)) return;
      clearTimeout(burstRef.current);
      burstRef.current = setTimeout(() => run(MIN_GAP_MS), BURST_MS);
    });
    return () => { off(); clearTimeout(burstRef.current); };
  }, [enabled, key, run]);

  useEffect(() => {
    if (!enabled || !poll) return undefined;
    // The POLL is gated on focus as well as visibility; RETURNING to the tab is
    // gated on visibility alone. The two are deliberately different: a focus or
    // visibilitychange event is the moment someone came back to look, and it
    // should refresh stale data even in the instant before hasFocus() settles.
    // Both listeners feed the same FOCUS_STALE_MS path, so a quick alt-tab still
    // does nothing and a return after twenty minutes still refreshes at once.
    const id = setInterval(() => { if (isTabActive()) run(MIN_GAP_MS); }, poll);
    const onReturn = () => { if (isVisible()) run(FOCUS_STALE_MS); };
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onReturn);
    if (typeof window !== 'undefined') window.addEventListener('focus', onReturn);
    return () => {
      clearInterval(id);
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onReturn);
      if (typeof window !== 'undefined') window.removeEventListener('focus', onReturn);
    };
  }, [enabled, poll, run]);

  return { refreshNow, markRefreshed };
}

export default useLiveData;
