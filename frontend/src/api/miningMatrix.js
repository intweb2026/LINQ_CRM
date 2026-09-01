// Real backend: /api/mining-matrix/ (see backend/mining_matrix/views.py + services.py).
//
// ONE REQUEST PER VIEW, AND NO SECOND ENDPOINT FOR THE TAB COUNTS. Every view
// shares one aggregate over Ticket Central and one read of the Events catalogue,
// so the list response carries `view_counts` for all three tabs alongside the
// rows for the one being shown. A `summary` call would repeat both queries to
// return three integers this response already knows.
//
// Not paginated, deliberately: the priority columns only mean anything read
// across the whole set, and the footer totals are over every row.
import { http } from './client';

export const VIEWS = { UPCOMING: 'upcoming', ALL: 'all', UNLINKED: 'unlinked' };

export const list = (view = VIEWS.UPCOMING, includeZero = false) =>
  http
    .get('mining-matrix/', {
      params: { view, ...(includeZero ? { include_zero: 1 } : {}) },
    })
    .then((r) => r.data);

/**
 * The Ticket Central URL a matrix row links to.
 *
 * `canonical_code` and NOT `event_code`: the row is labelled with the Events
 * code the user recognises ("Feb2027_AFS-JS"), but the tickets underneath it are
 * filed under the Ticket Central purpose ("AFS"), which is what the destination
 * table can actually filter on. Linking the displayed code would land on an
 * empty table for every event whose code carries a month prefix or a stream
 * suffix — which, in the live catalogue, is nearly all of them.
 *
 * The parameters are read by TicketCentralPage, which turns them into the same
 * two filter_spec criteria the matrix itself aggregates on. Kept as plain query
 * params rather than a serialised spec so the link is legible, shareable and
 * bookmarkable.
 */
export const ticketsHref = (row) =>
  `/tickets?purpose=${encodeURIComponent(row.canonical_code || '')}&unmined=1`;
