// Real backend: /api/reports/* (see backend/reports/serializers.py + views.py).
//
// Known gap: `overview()`/`months` (team productivity + bookings-by-month)
// have no dedicated backend aggregate endpoint — computed here from the real
// bookings/users/teams lists rather than fabricated.
import { http, fetchAllPages } from './client';
import * as bookingsApi from './bookings';
import * as ticketsApi from './tickets';
import * as webhooksApi from './webhooks';

const SYNC_STATUS_TO_UI = { success: 'synced', syncing: 'syncing', failed: 'error', partial: 'error', never: 'idle', idle: 'idle' };
const LOG_STATUS_TO_UI = { success: 'success', partial: 'partial', failed: 'error', running: 'partial' };
const INTERVAL_TO_UI = { manual: 'Manual', hourly: 'Hourly', daily: 'Daily', weekly: 'Weekly' };

function sheetToFrontend(s) {
  return {
    id: s.id,
    name: s.name,
    worksheet: s.worksheet_name,
    status: SYNC_STATUS_TO_UI[s.sync_status] || 'idle',
    rows: s.records_count || 0,
    interval: INTERVAL_TO_UI[s.sync_frequency] || s.sync_frequency,
    last_sync: s.last_synced_at,
    type: s.sheet_type,
    error: s.last_error || '',
  };
}

function syncLogToFrontend(l) {
  return {
    id: l.id,
    status: LOG_STATUS_TO_UI[l.status] || l.status,
    source: l.source_name || '—',
    rows_read: l.records_processed || 0,
    rows_written: (l.records_created || 0) + (l.records_updated || 0),
    duration_ms: l.duration_seconds != null ? Math.round(l.duration_seconds * 1000) : 0,
    started_at: l.started_at,
    message: l.error_message || '',
  };
}

export const sheets = () => fetchAllPages('reports/sources/').then((rows) => rows.map(sheetToFrontend));
export const syncLogs = () => fetchAllPages('reports/sync-logs/').then((rows) => rows.map(syncLogToFrontend));

// bookingAggregates() lived here: it walked every delegate, user and team row
// to build the dashboard's GROUP BYs in the browser. Both callers now read
// /api/stats/dashboard_aggregate/ instead (config/views.py DashboardAggregateView).

export async function overview() {
  // Same SQL aggregate as dashboard() — this used to walk every delegate row
  // a second time to produce the identical two fields.
  const { data } = await http.get('stats/dashboard_aggregate/');
  return {
    booking_team_productivity: data.booking_team_productivity || [],
    months: data.months || [],
  };
}

// Full dashboard aggregate — see bookingAggregates() for the booking-derived
// pieces. `delta`/`year` are computed from the same monthly series rather
// than a dedicated backend endpoint.
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
 */
export async function dashboard() {
  const [agg, ticketStats, whFailed] = await Promise.all([
    http.get('stats/dashboard_aggregate/').then((r) => r.data),
    ticketsApi.stats().catch(() => ({})),
    webhooksApi.countByStatus('failed').catch(() => 0),
  ]);

  const now = new Date();
  const yearPrefix = String(now.getFullYear());
  const yearMonths = (agg.months || []).filter((m) => m.label.startsWith(yearPrefix));
  const year = yearMonths.reduce((s, m) => s + m.total, 0);
  const h1 = yearMonths.filter((m) => +m.label.slice(5, 7) <= 6).reduce((s, m) => s + m.total, 0);
  const h2 = yearMonths.filter((m) => +m.label.slice(5, 7) > 6).reduce((s, m) => s + m.total, 0);
  const delta = h1 ? Math.round(((h2 - h1) / h1) * 100) : 0;

  const BOOKING_TEAM_TYPES = ['sales', 'spex', 'speaker_sales'];
  const teams = agg.booking_team_productivity || [];
  return {
    all: agg.all, sales: agg.sales, spex: agg.spex, speaker: agg.speaker,
    months: agg.months || [], channels: agg.channels || [],
    team_productivity: teams,
    booking_team_productivity: teams.filter((t) => BOOKING_TEAM_TYPES.includes(t.team_type)),
    tickets: ticketStats, whFailed,
    year, delta,
  };
}

// `frequency` must be one of the backend's real SyncFrequency choices
// (manual/hourly/daily/weekly) — see GoogleSheetSource.SyncFrequency. Both
// sheet_id and sheet_url are sent as the raw pasted URL; the serializer's
// create() re-extracts sheet_id from it and keeps sheet_url intact for
// display, so this populates both fields correctly in one request.
export function addSource(payload) {
  return http.post('reports/sources/', {
    name: payload.name,
    sheet_id: payload.url || '',
    sheet_url: payload.url || '',
    worksheet_name: payload.worksheet || 'Sheet1',
    sheet_type: payload.type || 'custom',
    sync_frequency: payload.frequency || 'manual',
    sync_enabled: payload.syncEnabled !== false,
    description: payload.description || '',
    notes: payload.notes || '',
  }).then((r) => sheetToFrontend(r.data));
}
export function syncAll() {
  return http.post('reports/sources/sync-all/', {}).then((r) => r.data);
}
export function listWorksheets(url) {
  return http.post('reports/sources/list-worksheets/', { sheet_url: url }).then((r) => r.data.worksheets || []);
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
