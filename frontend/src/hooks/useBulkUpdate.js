import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { bulkUpdate as bulkUpdateOn, chunk, fetchBulkUpdateSchema, mapLimit } from '../api/client';
import { useToast } from '../context/ToastContext';

/**
 * Wiring for "select rows → update one field on all of them", against the generic
 * engine in backend/accounts/bulk_update.py.
 *
 * WHY A HOOK RATHER THAN FIVE COPIES
 * Bookings, Ticket Central, Events, Paper Review and Proposal Submission all
 * declare `bulk_update_fields` server-side, and each needs the same four pieces:
 * lazily fetch the schema, remember which ids are selected, preview, commit. Only
 * Bookings had them, written inline; the other four viewsets have supported mass
 * update all along with nothing in the UI reaching it. Copied four more times, a
 * fix to any of it (the plan_hash retry, the schema failure path) would have to be
 * found in five places.
 *
 * BATCHING
 * bulk_update refuses more than `bulk_update_max` ids per request — 1000, and the
 * schema reports it as `max`. That ceiling was invisible while the table could
 * only select one page; now that the header checkbox selects every matching row
 * (see DataTable's selectEverything), a Ticket Central select-all is 35,690 ids
 * and one request would come back 400 "Maximum 1000 IDs per request", which the
 * user reads as the update being broken.
 *
 * So both halves batch, in `max`-sized chunks:
 *
 *   PREVIEW  runs every batch and merges the plans, so the numbers on screen
 *            describe the whole selection rather than its first 1000 rows.
 *   COMMIT   walks the batches IN ORDER, each carrying the plan_hash the preview
 *            minted for that exact batch. The backend's staleness check is
 *            per-request and is preserved unchanged.
 *
 * The cost of batching is that a commit is no longer one transaction. A batch
 * that fails leaves the earlier ones applied, and that is reported as exactly
 * what it is — see the `partial` field on the thrown error, which the modal
 * renders instead of the ordinary failure message. Silently reporting "0
 * updated" after 11,000 rows had already changed would be far worse.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * No toast on success or failure of the update itself — BulkUpdateModal owns that
 * messaging, so that a 409 retry does not notify twice. The one toast here is for
 * the schema fetch, which the modal cannot report because it never opens.
 *
 * @param resource  the DRF path segment, e.g. 'delegates', 'paper-reviews'
 * @param refresh   called after a successful commit, to reload the table
 */

/** Fallback when the schema has not landed. Mirrors bulk_update_max. */
const DEFAULT_BATCH = 1000;

/**
 * Preview batches in flight at once.
 *
 * Previews are read-only and independent, so they need not be serialised — but
 * 36 at once on a full Ticket Central selection is a burst the API should not
 * have to absorb from one checkbox. Commits are NOT run through this: they are
 * strictly sequential, because the failure report has to be able to say how many
 * rows were already written, and that number is only knowable if the batches
 * finish in a known order.
 */
const PREVIEW_CONCURRENCY = 4;

/**
 * Fold per-batch plans into one plan describing the whole selection.
 *
 * Every count is additive, which is what makes this safe: `requested`,
 * `permitted`, `no_op` and each bucket of `distribution` are counts of disjoint
 * id sets, since the batches partition the selection.
 *
 * COLLATERAL IS THE EXCEPTION, and it is why `collateral.batched` exists. The
 * backend computes it as "rows sharing a parent with this batch, minus this
 * batch" — so when a selection spans several batches, rows in batch 2 that share
 * an invoice with batch 1 are counted as collateral of batch 1 even though the
 * user selected them too. The sum is therefore an UPPER BOUND, not a count, and
 * the modal says so rather than presenting an inflated number as fact. Erring
 * high is the safe direction for a warning about rows nobody chose; erring low
 * would hide them.
 */
export function mergePlans(plans) {
  const distribution = {};
  const sample = [];
  let requested = 0, permitted = 0, noOp = 0;
  let collateral = 0, hidden = 0, overflow = 0;

  plans.forEach((p) => {
    requested += p.requested || 0;
    permitted += p.permitted || 0;
    noOp += p.no_op || 0;
    Object.entries(p.distribution || {}).forEach(([k, n]) => {
      distribution[k] = (distribution[k] || 0) + n;
    });
    const c = p.collateral || {};
    collateral += c.count || 0;
    hidden += c.hidden_count || 0;
    // Rows the sample could not name are counted, never dropped: the 20-row cap
    // is per batch, so what overflows one batch's sample plus everything past
    // this merged cap both belong in the same "and N more" figure.
    (c.sample || []).forEach((row) => {
      if (sample.length < 20) sample.push(row); else overflow += 1;
    });
    overflow += c.overflow || 0;
  });

  // no_op is absent from a value-less preview, and absent must stay absent —
  // the modal distinguishes "not asked yet" from "none of them". Only claim it
  // when every batch reported it.
  const hasNoOp = plans.length > 0 && plans.every((p) => p.no_op !== undefined);

  return {
    success: true,
    updated: 0,
    requested,
    permitted,
    ...(hasNoOp ? { no_op: noOp } : {}),
    distribution,
    // side_effects are a function of (field, value) alone, so every batch
    // returns the same list and the first one is the whole answer.
    side_effects: plans.find((p) => p.side_effects)?.side_effects || [],
    collateral: {
      count: collateral,
      sample,
      hidden_count: hidden,
      overflow,
      batched: plans.length > 1,
    },
    errors: [],
    // A single batch keeps the plain contract, hash and all, so nothing about
    // the ordinary case changes. Past that the hashes are per batch and live on
    // the batch list, and a single one here would be meaningless.
    plan_hash: plans.length === 1 ? plans[0].plan_hash : null,
    batches: plans.length,
  };
}

export function useBulkUpdate(resource, refresh) {
  const toast = useToast();
  // { ids, clear } — `clear` is DataTable's own selection reset, handed to the
  // bulk bar. Held here so a committed update empties the checkboxes it applied to
  // rather than leaving a selection that no longer describes anything.
  const [selection, setSelection] = useState(null);
  const [schema, setSchema] = useState(null);

  /**
   * The batches the last preview priced, each with the plan_hash the server
   * minted for it. Commit replays exactly these, so the ids and the hash a batch
   * is committed with are the same pair the user was shown numbers for.
   */
  const batchesRef = useRef([]);

  // Fetched once, lazily, on first use rather than on page load: the modal renders
  // entirely from it, so the field list is the server's and nothing about what is
  // editable is hardcoded in the UI.
  useEffect(() => {
    if (!selection || schema) return;
    let live = true;
    fetchBulkUpdateSchema(resource).then(
      (s) => { if (live) setSchema(s); },
      () => {
        if (!live) return;
        toast('Could not load the list of editable fields', 'er');
        setSelection(null);
      },
    );
    return () => { live = false; };
  }, [selection, schema, resource, toast]);

  // The server's own ceiling, never a number chosen here: a hardcoded 1000 that
  // drifted above bulk_update_max would put the 400 back, one batch at a time.
  const batchSize = useMemo(
    () => (schema && schema.max > 0 ? schema.max : DEFAULT_BATCH),
    [schema],
  );

  const onPreview = useCallback(
    // `value` passes through untouched — undefined means "no target chosen yet"
    // and must stay absent from the body, which is how the backend tells it apart
    // from null ("clear this field").
    async (field, value) => {
      const batches = chunk(selection.ids, batchSize);
      const plans = await mapLimit(batches, PREVIEW_CONCURRENCY, (ids) =>
        bulkUpdateOn(resource, { ids, field, value, commit: false }));
      batchesRef.current = batches.map((ids, i) => ({ ids, planHash: plans[i].plan_hash }));
      return mergePlans(plans);
    },
    [resource, selection, batchSize],
  );

  const onCommit = useCallback(
    async (field, value, planHash, onProgress) => {
      // planHash is the merged plan's, which is null once there is more than one
      // batch. The per-batch hashes are the real ones; fall back to the argument
      // for the single-batch case so a caller that previewed elsewhere still works.
      const batches = batchesRef.current.length
        ? batchesRef.current
        : [{ ids: selection.ids, planHash }];

      let updated = 0, noOp = 0, done = 0;
      for (const batch of batches) {
        try {
          // eslint-disable-next-line no-await-in-loop
          const res = await bulkUpdateOn(resource, {
            ids: batch.ids, field, value, commit: true, planHash: batch.planHash,
          });
          updated += res.updated || 0;
          noOp += res.no_op || 0;
        } catch (err) {
          // Nothing was written yet, so this is an ordinary failure and must
          // stay one — in particular a 409 has to reach the modal untouched so
          // it can show the refreshed plan and let the user confirm again.
          if (done === 0) throw err;
          // Past the first batch the picture is different and retrying whole
          // would double-apply what already landed. Report the real position.
          err.partial = {
            updated,
            batchesDone: done,
            batchesTotal: batches.length,
            rowsRemaining: batches.slice(done).reduce((n, b) => n + b.ids.length, 0),
          };
          // The rows that DID change are on screen as stale values until this
          // runs, so refresh regardless of the failure. The selection is left
          // alone deliberately: it is the only record of what was being acted on.
          refresh();
          throw err;
        }
        done += 1;
        if (onProgress) onProgress({ done, total: batches.length, updated });
      }

      selection.clear();
      refresh();
      return { updated, no_op: noOp, batches: batches.length };
    },
    [resource, selection, refresh],
  );

  return {
    /** Bulk-bar button handler: useBulkUpdate(...).open(ids, clear) */
    open: useCallback((ids, clear) => {
      batchesRef.current = [];
      setSelection({ ids, clear });
    }, []),
    /** Both the selection and the schema are in hand — safe to render the modal. */
    ready: Boolean(selection && schema),
    /** Spread onto <BulkUpdateModal>; pass rowLabel and totalMatching per page. */
    props: {
      selectedIds: selection ? selection.ids : [],
      schema,
      onPreview,
      onCommit,
      onClose: () => setSelection(null),
    },
  };
}

export default useBulkUpdate;
