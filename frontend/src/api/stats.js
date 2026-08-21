// Dashboard aggregates — /api/stats/dashboard_aggregate/ (config/views.py
// DashboardAggregateView).
//
// Was api/reports.js, which also wrapped the /api/reports/* sheet-source and
// sync-log endpoints for the Reports page. That page is gone; what is left here
// has nothing to do with it and is read by the Dashboard and Teams Management.
import { http } from './client';
import * as ticketsApi from './tickets';
import * as webhooksApi from './webhooks';

/**
 * Dashboard aggregates, from the database.
 *
 * Was: bookingAggregates() (a full fetchAllPages walk of every delegate, plus
 * users and teams) AND webhooksApi.listLogs() (a full walk of 130,287 webhook
 * logs) purely to count failures — ~350 sequential requests per dashboard load.
 * Now three requests, none of which walk anything:
 *   /api/stats/dashboard_aggregate/  the GROUP BYs (config/views.py)
 *   /api/tickets/stats/              ticket counts
 *   /api/webhooks/logs/?status=failed&page_size=1   read for `count`
 *
 * `period` is a DASH_PERIODS key and reaches the backend verbatim, which 400s on
 * anything it does not know rather than quietly answering for all time. The
 * ticket and webhook counts are NOT period-scoped — their dates are their own
 * (assign_date, received_at) and have nothing to do with when a booking was
 * raised; the returned `period.applies_to` says which figures the window covers.
 */
export async function dashboard(period) {
  const [agg, ticketStats, whFailed] = await Promise.all([
    http.get('stats/dashboard_aggregate/', { params: { period } }).then((r) => r.data),
    ticketsApi.stats().catch(() => ({})),
    webhooksApi.countByStatus('failed').catch(() => 0),
  ]);

  // Calendar-year total and the H1→H2 swing, both read off the same monthly
  // series. Only meaningful for the unfiltered view: inside a 7-day window there
  // is no half-year to compare, which is why DashboardPage hides them there
  // rather than rendering a confident -100%.
  const now = new Date();
  const yearPrefix = String(now.getFullYear());
  const yearMonths = (agg.months || []).filter((m) => m.label.startsWith(yearPrefix));
  const year = yearMonths.reduce((s, m) => s + m.total, 0);
  const h1 = yearMonths.filter((m) => +m.label.slice(5, 7) <= 6).reduce((s, m) => s + m.total, 0);
  const h2 = yearMonths.filter((m) => +m.label.slice(5, 7) > 6).reduce((s, m) => s + m.total, 0);
  const delta = h1 ? Math.round(((h2 - h1) / h1) * 100) : 0;

  const teams = agg.booking_team_productivity || [];
  return {
    period: agg.period || {}, attribution: agg.attribution || {},
    // `outstanding` is all-time whatever the window is — the action queue is a
    // worklist, and an unpaid invoice does not stop being unpaid because the
    // dashboard is showing the last 7 days. Falls back to the windowed line so a
    // response from a backend without the field still renders something real.
    outstanding: agg.outstanding || agg.all,
    all: agg.all, sales: agg.sales, spex: agg.spex, speaker: agg.speaker,
    months: agg.months || [], channels: agg.channels || [],
    team_productivity: teams,
    // `pipeline` is set by the backend for exactly the teams that sell bookings
    // (sales, telemarketing, spex, speaker_sales) — asking the payload beats
    // re-listing the team types here and having the two lists drift.
    booking_team_productivity: teams.filter((t) => t.pipeline),
    tickets: ticketStats, whFailed,
    year, delta,
  };
}

/**
 * Per-display-name booking and ticket totals, from the SQL aggregate.
 *
 * Teams Management needs "how many bookings / tickets does this person have" for
 * every member on the board. It used to get that by walking every delegate
 * (13,269) and every ticket (35,690) and tallying in the browser — measured at 83
 * requests for one visit to /teams.
 */
export function teamMemberStats() {
  return http.get('stats/dashboard_aggregate/').then((r) => r.data.per_user_by_name || {});
}
