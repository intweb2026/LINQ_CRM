import { useCallback, useEffect, useState } from 'react';
import { bulkUpdate as bulkUpdateOn, fetchBulkUpdateSchema } from '../api/client';
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
 * WHAT IT DELIBERATELY DOES NOT DO
 * No toast on success or failure of the update itself — BulkUpdateModal owns that
 * messaging, so that a 409 retry does not notify twice. The one toast here is for
 * the schema fetch, which the modal cannot report because it never opens.
 *
 * @param resource  the DRF path segment, e.g. 'delegates', 'paper-reviews'
 * @param refresh   called after a successful commit, to reload the table
 */
export function useBulkUpdate(resource, refresh) {
  const toast = useToast();
  // { ids, clear } — `clear` is DataTable's own selection reset, handed to the
  // bulk bar. Held here so a committed update empties the checkboxes it applied to
  // rather than leaving a selection that no longer describes anything.
  const [selection, setSelection] = useState(null);
  const [schema, setSchema] = useState(null);

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

  const onPreview = useCallback(
    // `value` passes through untouched — undefined means "no target chosen yet"
    // and must stay absent from the body, which is how the backend tells it apart
    // from null ("clear this field").
    (field, value) => bulkUpdateOn(resource, { ids: selection.ids, field, value, commit: false }),
    [resource, selection],
  );

  const onCommit = useCallback(
    (field, value, planHash) => bulkUpdateOn(
      resource, { ids: selection.ids, field, value, commit: true, planHash },
    ).then((res) => { selection.clear(); refresh(); return res; }),
    [resource, selection, refresh],
  );

  return {
    /** Bulk-bar button handler: useBulkUpdate(...).open(ids, clear) */
    open: useCallback((ids, clear) => setSelection({ ids, clear }), []),
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
