import { useCallback, useEffect, useRef, useState } from 'react';
import DataTable from '../../components/DataTable';
import Drawer from '../../components/Drawer';
import { Icon } from '../../lib/icons';
import { WhBadge } from '../../components/Badge';
import { rel, ftime, fdate } from '../../lib/helpers';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import * as webhooksApi from '../../api/webhooks';

const LIVE_POLL_MS = 15000;

function json(v) {
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function stamp(d) {
  return d ? `${fdate(d)} ${ftime(d)}` : '—';
}

/**
 * One delivery in full.
 *
 * The list row says a delivery failed; only the detail route says why. It
 * carries payload, headers, response, processing notes and stack trace, and
 * until now nothing in the UI ever asked for it.
 */
function LogDrawer({ row, onClose, onRetried }) {
  const toast = useToast();
  const [retrying, setRetrying] = useState(false);
  const { data, loading, error } = useFetch(() => webhooksApi.getLog(row.id), [row.id], { initialData: null });
  const d = data || {};

  async function retry() {
    setRetrying(true);
    toast('Retrying delivery ' + row.id + '…', 'nf');
    try {
      const res = await webhooksApi.retry(row.id);
      toast(res.success ? 'Delivery succeeded on retry' : (res.detail || 'Retry did not succeed'), res.success ? 'ok' : 'er');
      onRetried();
      onClose();
    } catch (err) {
      toast(err.response?.data?.error || err.response?.data?.detail || 'Retry failed', 'er');
    } finally {
      setRetrying(false);
    }
  }

  return (
    <Drawer wide onClose={onClose}
      head={(
        <div>
          <span className="mono" style={{ color: 'var(--t-600)' }}>Delivery #{row.id}</span>
          <h2>{row.invoice_number || 'No invoice number'}</h2>
          <p><WhBadge value={row.status} /> <span className="dim" style={{ fontSize: 11 }}>HTTP {d.http_status ?? row.http_status ?? '—'}</span></p>
        </div>
      )}
      foot={(
        <>
          <button className="btn btn-s" onClick={onClose}>Close</button>
          {row.status !== 'success' ? (
            <button className="btn btn-p" onClick={retry} disabled={retrying}>
              <Icon name="refresh" size={15} />{retrying ? 'Retrying…' : 'Retry delivery'}
            </button>
          ) : null}
        </>
      )}
    >
      {error ? (
        <div className="vr er" style={{ marginBottom: 14 }}>
          <Icon name="warn" size={15} /><span>Could not load this delivery&apos;s detail.</span>
        </div>
      ) : null}

      <div className="ms">
        <div><div className="l">Event</div><div className="v" style={{ fontSize: 13 }}>{row.event_code || '—'}</div></div>
        <div><div className="l">Records</div><div className="v">{row.records}</div></div>
        <div><div className="l">Duration</div><div className="v" style={{ fontSize: 13 }}>{row.duration_ms}ms</div></div>
      </div>

      <div className="sl">Delivery</div>
      <div className="nt">
        <div className="nt-h"><span className="w">Received</span><span className="d">{stamp(d.received_at || row.received_at)}</span></div>
        <div className="nt-h"><span className="w">Processed</span><span className="d">{stamp(d.processed_at)}</span></div>
        <div className="nt-h"><span className="w">Source</span><span className="d">{d.source || '—'}</span></div>
        <div className="nt-h"><span className="w">API key</span><span className="d">{row.api_key_name}</span></div>
        <div className="nt-h"><span className="w">From IP</span><span className="d mono">{d.ip_address || '—'}</span></div>
        <div className="nt-h"><span className="w">DB outcome</span><span className="d">{row.db_status || '—'}</span></div>
        <div className="nt-h" style={{ marginBottom: 0 }}><span className="w">Retries</span><span className="d">{row.retries}</span></div>
      </div>

      {d.error_message || row.error_message ? (
        <>
          <div className="sl">Why it failed</div>
          <div className="jsn" style={{ color: 'var(--red-tx)', background: 'var(--red-bg)', borderColor: 'transparent' }}>
            {d.error_message || row.error_message}
          </div>
        </>
      ) : null}

      <div className="sl">Payload received</div>
      <pre className="jsn">{loading && !data ? 'Loading…' : json(d.payload)}</pre>

      <div className="sl">Response sent</div>
      <pre className="jsn">{loading && !data ? 'Loading…' : json(d.response)}</pre>

      {d.processing_notes ? (
        <>
          <div className="sl">Processing notes</div>
          <pre className="jsn">{d.processing_notes}</pre>
        </>
      ) : null}

      {d.stack_trace ? (
        <>
          <div className="sl">Stack trace</div>
          <pre className="jsn">{d.stack_trace}</pre>
        </>
      ) : null}

      <div className="sl">Request headers</div>
      <pre className="jsn">{loading && !data ? 'Loading…' : json(d.headers)}</pre>
    </Drawer>
  );
}

export default function WebhookLogs() {
  const toast = useToast();
  // No listLogs(): 130,287 rows is ~261 sequential requests at page_size=500 —
  // not a slow page, a hung one. WebhookLogViewSet now carries FilterSpecMixin.
  const [tableRefetch, setTableRefetch] = useState(null);
  const [live, setLive] = useState(false);
  const [drawerRow, setDrawerRow] = useState(null);
  // Wrapped in an updater — React would otherwise CALL a bare function passed to
  // a state setter instead of storing it.
  const keepRefetch = useCallback((fn) => setTableRefetch(() => fn), []);
  const refetch = useCallback(() => { if (tableRefetch) tableRefetch(); }, [tableRefetch]);

  // The table fetches on mount and on filter/sort/page change — nothing else.
  // A delivery that arrived while you were watching the page therefore never
  // appeared, which reads as "the webhook did not log anything". Poll on demand.
  // The ref keeps the interval off `refetch`'s identity, so it is not torn down
  // and rebuilt (restarting the 15s clock) every time the table re-registers.
  const refetchRef = useRef(refetch);
  useEffect(() => { refetchRef.current = refetch; }, [refetch]);
  useEffect(() => {
    if (!live) return undefined;
    const id = setInterval(() => refetchRef.current(), LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [live]);

  async function retry(id) {
    toast('Retrying delivery ' + id + '…', 'nf');
    try {
      const res = await webhooksApi.retry(id);
      toast(res.success ? 'Delivery succeeded on retry' : (res.detail || 'Retry did not succeed'), res.success ? 'ok' : 'er');
    } catch (err) {
      toast(err.response?.data?.error || 'Retry failed', 'er');
    }
    refetch();
  }

  return (
    <>
      <DataTable
        tableId="webhook-logs"
        server={{ resource: 'webhooks/logs', mapRow: webhooksApi.fromApi }}
        onServerReady={keepRefetch}
        onRow={(r) => setDrawerRow(r)}
        noun="deliveries" pageSize={50} defaultSort={{ key: 'received_at', dir: 'desc' }} searchPlaceholder="Search invoice, event, key…"
        extraToolbar={(
          <>
            <button className="btn btn-s btn-sm" onClick={refetch} title="Re-fetch the newest deliveries">
              <Icon name="refresh" size={13} />Refresh
            </button>
            <button className={'btn btn-sm' + (live ? ' btn-p' : ' btn-s')} onClick={() => setLive((v) => !v)}
              title={live ? `Re-fetching every ${LIVE_POLL_MS / 1000}s` : 'Auto-refresh while you watch'}>
              <Icon name={live ? 'eye' : 'refresh'} size={13} />{live ? 'Live' : 'Go live'}
            </button>
          </>
        )}
        cols={[
          { key: 'status', label: 'Status', serverField: 'status', serverOrdering: 'status', cell: (v) => <WhBadge value={v} />, opts: () => ['received', 'processing', 'success', 'failed', 'duplicate'] },
          { key: 'db_status', label: 'DB Status', serverField: 'db_insert_status', serverOrdering: 'db_insert_status', cell: (v) => <span className="dim" style={{ fontSize: 11 }}>{v}</span> },
          { key: 'invoice_number', label: 'Invoice', serverField: 'invoice_number', serverOrdering: 'invoice_number', cell: (v) => <span className="mono lnk">{v}</span> },
          { key: 'event_code', label: 'Event', serverField: 'event_code', serverOrdering: 'event_code', cell: (v) => <span className="mono" style={{ color: 'var(--t-600)' }}>{v}</span> },
          // Failure reason inline. It is already in the list payload, so this
          // costs nothing and saves opening a row to learn a delivery was
          // rejected for a bad event code.
          // No serverOrdering: error_message is not in the viewset's
          // ordering_fields, so DataTable renders the header unsortable but
          // keeps its filter — which is the useful half for a reason column.
          { key: 'error_message', label: 'Reason', serverField: 'error_message', cell: (v) => (v
            ? <span title={v} style={{ fontSize: 11, color: 'var(--red-tx)', display: 'block', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
            : <span className="dim" style={{ fontSize: 11 }}>—</span>) },
          { key: 'api_key_name', label: 'API Key', cell: (v) => <span className="mono" style={{ fontSize: 11 }}>{v}</span> },
          { key: 'records', label: 'Records', num: true },
          { key: 'duration_ms', label: 'Duration', num: true, cell: (v) => v + 'ms' },
          { key: 'retries', label: 'Retries', serverField: 'retry_count', serverOrdering: 'retry_count', num: true, cell: (v) => (v ? <b style={{ color: 'var(--red)' }}>{v}</b> : '0') },
          // Displays received_at but ORDERS BY created_at. received_at is
          // nullable and Postgres sorts NULLs first under DESC, which pinned
          // ten ancient rows to the top of page one and hid every new delivery
          // below them. created_at is NOT NULL, indexed, and written in the
          // same request microseconds apart.
          { key: 'received_at', label: 'Received', type: 'date', serverField: 'received_at', serverOrdering: 'created_at', cell: (v) => rel(v) },
          { key: '_a', label: '', sortable: false, cell: (v, r) => (r.status === 'failed' ? <button className="btn btn-sm btn-s" onClick={(e) => { e.stopPropagation(); retry(r.id); }}><Icon name="refresh" size={12} />Retry</button> : null) },
        ]}
      />
      {drawerRow ? <LogDrawer row={drawerRow} onClose={() => setDrawerRow(null)} onRetried={refetch} /> : null}
    </>
  );
}
