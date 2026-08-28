// Real backend: /api/webhooks/logs/ and /api/webhooks/keys/
// (see backend/webhooks/serializers.py + views.py).
import { http, fetchAllPages, fetchPage } from './client';

function logToFrontend(l) {
  return {
    id: l.id,
    status: l.status,
    db_status: l.db_insert_status || '',
    invoice_number: l.invoice_number,
    event_code: l.event_code,
    api_key_name: l.api_key_name || '—',
    records: (l.records_inserted || 0) + (l.records_updated || 0),
    duration_ms: l.processing_duration != null ? Math.round(l.processing_duration * 1000) : 0,
    retries: l.retry_count || 0,
    received_at: l.received_at || l.created_at,
    // A failed row that does not say why is a row you have to go and ask the
    // database about. Both are already in the list payload — carry them.
    http_status: l.http_status,
    error_message: l.error_message || '',
  };
}

function keyToFrontend(k) {
  return {
    id: k.id,
    name: k.name,
    event_code: k.event || 'ALL',
    key: k.api_key || null,
    active: k.is_active,
    calls: k.usage_count || 0,
    last_used: k.last_used_at,
    // Which ingest endpoint the key is scoped to, and the path it posts to.
    // ingest_path is resolved server-side from webhooks/urls.py, so no webhook
    // path is written anywhere in this app: a key made for tickets copies a
    // ticket URL, and a route renamed in Django needs no change here.
    target: k.target || '',
    ingest_path: k.ingest_path || '/api/webhooks/ingest/',
  };
}

// listLogs() removed: it walked all 130,287 webhook logs (~261 sequential
// requests at page_size=500). The Logs table pages server-side via DataTable's
// `server` prop; counts use countByStatus(); the feed uses recentLogs(n).
export const listKeys = () => fetchAllPages('webhooks/keys/').then((rows) => rows.map(keyToFrontend));

export function retry(id) {
  return http.post(`webhooks/logs/${id}/retry/`, {}).then((r) => r.data);
}

/**
 * One delivery in full — payload, headers, response, stack trace, notes.
 *
 * WebhookLogSerializer has always served these on the detail route; nothing in
 * the UI called it, so the reason a delivery failed was only ever visible in
 * the database.
 */
export function getLog(id) {
  return http.get(`webhooks/logs/${id}/`).then((r) => r.data);
}
export function toggleKey(id) {
  return http.post(`webhooks/keys/${id}/toggle/`, {}).then((r) => r.data);
}
export async function generateKey(name, eventCode, target) {
  // WebhookApiKeyCreateSerializer's fields don't include `id`, so the create
  // response can't tell us which row was just made — re-fetch the list
  // (default-ordered newest-first per the model's Meta.ordering) instead.
  //
  // An empty `target` is meaningful and must be sent as '': it is what every
  // key issued before the column existed holds, and it means "posts to every
  // ingest endpoint". Only a key the operator deliberately scoped is narrowed.
  await http.post('webhooks/keys/', {
    name,
    event: eventCode === 'ALL' ? '' : eventCode,
    target: target && target !== 'ALL' ? target : '',
  });
  const { data } = await http.get('webhooks/keys/');
  const rows = data.results || data;
  return keyToFrontend(rows[0]);
}
export function regenerateKey(id) {
  return http.post(`webhooks/keys/${id}/regenerate/`, {}).then((r) => r.data);
}

/**
 * How many logs are in `status`, as one request for one row.
 *
 * The dashboard wanted "how many webhook deliveries failed" and got it by
 * walking every page of webhooks/logs/ — 130,287 rows, ~261 sequential requests
 * at page_size=500. The paginator already answers this in `count`.
 */
export function countByStatus(status) {
  return fetchPage('webhooks/logs/', { pageSize: 1, params: { status } }).then((r) => r.count);
}

/** The newest `n` logs (model ordering is -created_at), mapped for display. */
export function recentLogs(n = 5) {
  return fetchPage('webhooks/logs/', { pageSize: n }).then((r) => r.results.map(logToFrontend));
}

// Row mapper for DataTable server mode, which receives raw API rows.
export const fromApi = logToFrontend;
