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

  const run = useCallback(async (...args) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchFn(...args);
      if (mountedRef.current) setData(result);
      return result;
    } catch (err) {
      if (mountedRef.current) setError(err);
      // Rethrown so an explicit `refetch()` caller can await and handle failure.
      // The automatic mount fetch below must therefore catch it — see there.
      throw err;
    } finally {
      if (mountedRef.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

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

  return { data, loading, error, refetch: run };
}
