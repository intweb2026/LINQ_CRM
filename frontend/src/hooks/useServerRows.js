/**
 * useServerRows — one page of a list endpoint, filtered and ordered by Django.
 *
 * Used by DataTable when it is given a `server` prop. Owns three things the
 * in-memory path did not need:
 *
 *  1. THE FILTER SCHEMA. Fetched from {resource}/filter_schema/ so which fields
 *     and operators are filterable comes from the server's own registry rather
 *     than a hardcoded list that can drift from filter_spec.py.
 *
 *  2. THE HYDRATION GATE. A spec restored from localStorage may name a field or
 *     operator the backend has since dropped, and the backend answers 400 for
 *     the whole request — which presents to the user as a permanently broken
 *     table. So when a stored spec exists, NO list request is issued until the
 *     schema has arrived and the stored conditions have been checked against it.
 *     With nothing stored there is nothing to validate and the first fetch goes
 *     out immediately, so the common case has no extra round-trip.
 *
 *  3. DEBOUNCE. Typing in a filter value updates the spec on every keystroke.
 *     Without a delay that is one request per character.
 *
 * If the schema fetch fails, the table loads UNFILTERED rather than staying
 * blank, and says so — a filter that cannot be validated is not sent, because
 * sending an unvalidated stale criterion is how every request starts 400ing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchPage, fetchFilterSchema } from '../api/client';

const DEBOUNCE_MS = 350;

export function useServerRows({ resource, page, pageSize, ordering, filterSpec, search, enabled = true, hasStoredSpec = false }) {
  const [schema, setSchema] = useState(null);
  const [schemaFailed, setSchemaFailed] = useState(false);
  const [rows, setRows] = useState([]);
  const [count, setCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(!!enabled);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  // Which request produced the rows currently in state. See rowsPage below.
  const [loaded, setLoaded] = useState(null);

  // ── Schema ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !resource) return undefined;
    let cancelled = false;
    setSchema(null);
    setSchemaFailed(false);
    fetchFilterSchema(resource)
      .then((s) => { if (!cancelled) setSchema(s); })
      .catch(() => { if (!cancelled) setSchemaFailed(true); })
      .finally(() => {});
    return () => { cancelled = true; };
  }, [resource, enabled]);

  // A stored spec must be validated against the schema before it travels. Once
  // the schema has arrived (or definitively failed) the gate is open for good.
  const gateOpen = !hasStoredSpec || schema !== null || schemaFailed;

  // ── Rows ──────────────────────────────────────────────────────────────────
  // filterSpec is a string (or null), so it compares by value in the dep list —
  // no memoisation needed at the call site and no re-fetch from a new object
  // identity that describes an identical filter.
  const effectiveSpec = schemaFailed ? null : filterSpec;

  // Identity of the request the current params describe. Stamped onto whatever
  // rows come back, so a caller can tell rows that answer THIS request from rows
  // still sitting in state from the last one.
  const reqKey = `${page}|${pageSize}|${ordering || ''}|${effectiveSpec || ''}|${search || ''}`;

  useEffect(() => {
    if (!enabled || !resource || !gateOpen) return undefined;
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      fetchPage(resource, { page, pageSize, ordering, filterSpec: effectiveSpec, search })
        .then((res) => {
          if (cancelled) return;
          setRows(res.results);
          setCount(res.count);
          setTotalPages(res.totalPages);
          setLoaded({ key: reqKey, page: res.page });
          setError('');
        })
        .catch((err) => {
          if (cancelled) return;
          setRows([]);
          setCount(0);
          setError(err?.response?.data?.detail || err?.message || 'Could not load records.');
        })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
    // reqKey is derived from the params already listed here; including it would
    // only re-trigger the fetch on the same changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource, enabled, gateOpen, page, pageSize, ordering, effectiveSpec, search, reloadKey]);

  const refetch = useCallback(() => setReloadKey((k) => k + 1), []);

  /**
   * Which page `rows` holds — and null unless those rows answer the CURRENT
   * params, so a caller that accumulates pages cannot append the wrong one.
   *
   * A caller cannot work this out from its own `page` state plus `loading`.
   * `loading` is set inside this hook's effect, which runs AFTER the render that
   * changed `page`, so in that render the caller still sees loading=false beside
   * the previous page's rows. DataTable's infinite scroll did exactly that: on
   * every advance it appended the page BEFORE the one it had just asked for,
   * which appended page 1 twice and then shifted each page down by one. Measured
   * on Bookings: five scrolls produced 250 rows holding 200 distinct records.
   */
  const rowsPage = loaded && loaded.key === reqKey ? loaded.page : null;

  return { schema, schemaFailed, gateOpen, rows, rowsPage, count, totalPages, loading, error, refetch };
}

export default useServerRows;
