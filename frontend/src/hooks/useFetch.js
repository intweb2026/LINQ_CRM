import { useState, useEffect, useCallback, useRef } from 'react';

export function useFetch(fetchFn, deps = [], options = {}) {
  const { immediate = true, initialData = null } = options;
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  /**
   * One implementation, two visibilities.
   *
   * A background refresh (a poll, or another tab's write arriving — see
   * hooks/useLiveData.js) must not announce itself, because the page is already
   * showing correct data. `quiet` therefore skips two things:
   *
   *   LOADING. Flipping it would drop pages that render a skeleton off it back to
   *   the skeleton every 30 seconds, on data the user is reading.
   *
   *   ERROR. A failed background poll must leave what is on screen alone. Paper
   *   Review and Proposal Submission replace the whole table with an error panel
   *   when `error` is set, so one dropped connection would blank a page that had
   *   perfectly good rows a moment ago — a refresh making things WORSE.
   */
  const exec = useCallback(async (quiet, args) => {
    if (!quiet) {
      setLoading(true);
      setError(null);
    }
    try {
      const result = await fetchFn(...args);
      if (mountedRef.current) setData(result);
      return result;
    } catch (err) {
      if (mountedRef.current && !quiet) setError(err);
      // Rethrown so an explicit `refetch()` caller can await and handle failure.
      // The automatic mount fetch below must therefore catch it — see there.
      throw err;
    } finally {
      if (mountedRef.current && !quiet) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const run = useCallback((...args) => exec(false, args), [exec]);
  const refetchQuiet = useCallback(
    // Swallowed here, not at the call site: every caller is a background refresh
    // that has nothing to report and no one waiting on the promise, so a rejection
    // would only surface as an unhandled one.
    (...args) => exec(true, args).catch(() => {}),
    [exec],
  );

  useEffect(() => {
    if (!immediate) return;
    // `.catch()` is load-bearing. `run` rethrows so manual refetch() callers can
    // await it, but nothing awaits THIS call — so a rejected fetch became an
    // unhandled promise rejection and surfaced as a console error on every route
    // where any endpoint failed (a 404 on paper-reviews/, a 500 mid-walk, a
    // dropped connection). The error is already captured in `error` state for
    // consumers to render; swallowing it here only stops it being reported twice,
    // once usefully and once as noise.
    run().catch(() => {});
  }, [run, immediate]);

  return { data, loading, error, refetch: run, refetchQuiet };
}
