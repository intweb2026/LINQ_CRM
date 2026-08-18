import { useState } from 'react';
import { EmptyState, Tabs } from '../components/UI';
import Modal from '../components/Modal';
import AddSheetSourceModal from '../components/AddSheetSourceModal';
import SheetTargetModal from '../components/SheetTargetModal';
import Popover from '../components/Popover';
import { Icon } from '../lib/icons';
import { GsBadge } from '../components/Badge';
import { nf, fdate, ftime } from '../lib/helpers';
import { GSYNC_TYPE_LABEL, GSYNC_TRIGGER_LABEL } from '../lib/constants';
import * as gsyncApi from '../api/googleSync';
import { useFetch } from '../hooks/useFetch';
import { useLiveData } from '../hooks/useLiveData';
import { useSession } from '../context/SessionContext';
import { useToast } from '../context/ToastContext';
import NoAccessPage from './NoAccessPage';

const TARGET_TONE = { success: 'green', failed: 'red', never: 'neutral' };
const TARGET_LABEL = { success: 'Success', failed: 'Failed', never: 'Never run' };

const STAB = [{ id: '', label: 'All' }, { id: 'running', label: 'Running' }, { id: 'success', label: 'Success' }, { id: 'failed', label: 'Failed' }, { id: 'partial_success', label: 'Partial' }, { id: 'pending', label: 'Pending' }];
const PS = 15;

function DetailModal({ log: l, onClose, onRetry }) {
  const canRetry = l.status === 'failed' || l.status === 'partial_success';
  return (
    <Modal size="lg" onClose={onClose}
      header={
        <div className="md-h">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1 }}>
            <GsBadge value={l.status} /><span style={{ fontWeight: 700, fontSize: 14 }}>{GSYNC_TYPE_LABEL[l.sync_type]}</span><span className="mono dim">#{l.id}</span>
          </div>
          {canRetry ? <button className="btn btn-s btn-sm" style={{ color: 'var(--t-600)', marginRight: 8 }} onClick={() => { onClose(); onRetry(l); }}>Retry</button> : null}
          <button className="dr-x" aria-label="Close" onClick={onClose}><Icon name="x" size={15} /></button>
        </div>
      }
      footer={<button className="btn btn-s" onClick={onClose}>Close</button>}
    >
      <div className="ro" style={{ marginBottom: 16 }}>
        <div className="ro-c"><div className="ro-l">Sheet</div><div className="ro-v">{l.sheet_name}</div></div>
        <div className="ro-c"><div className="ro-l">Mode</div><div className="ro-v" style={{ textTransform: 'capitalize' }}>{l.sync_mode}</div></div>
        <div className="ro-c"><div className="ro-l">Source</div><div className="ro-v">{GSYNC_TRIGGER_LABEL[l.trigger_source] || l.trigger_source}</div></div>
        <div className="ro-c"><div className="ro-l">By</div><div className="ro-v">{l.triggered_by || '—'}</div></div>
        <div className="ro-c"><div className="ro-l">Started</div><div className="ro-v">{fdate(l.started_at)} {ftime(l.started_at)}</div></div>
        <div className="ro-c"><div className="ro-l">Completed</div><div className="ro-v">{l.completed_at ? fdate(l.completed_at) + ' ' + ftime(l.completed_at) : '—'}</div></div>
        <div className="ro-c"><div className="ro-l">Duration</div><div className="ro-v">{l.duration_seconds != null ? l.duration_seconds.toFixed(2) + 's' : '—'}</div></div>
        <div className="ro-c"><div className="ro-l">Records</div><div className="ro-v">{l.records_processed ?? '—'}</div></div>
      </div>
      {l.error_message ? <div className="vr er" style={{ marginBottom: 16 }}><Icon name="warn" size={15} /><span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, whiteSpace: 'pre-wrap' }}>{l.error_message}</span></div> : null}
      <div className="fg">
        <div>
          <div className="fs-t" style={{ border: 'none', marginBottom: 8 }}>Counters</div>
          {[['Processed', l.records_processed], ['Created', l.records_created], ['Updated', l.records_updated], ['Failed', l.records_failed]].map(([lb, v]) => (
            <div key={lb} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--n-50)', fontSize: 12.5 }}><span className="dim">{lb}</span><span className="mono" style={{ fontWeight: 650 }}>{v ?? 0}</span></div>
          ))}
        </div>
        <div>
          <div className="fs-t" style={{ border: 'none', marginBottom: 8 }}>Sync summary</div>
          <pre style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: 10, fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-2)', overflow: 'auto', maxHeight: 140 }}>{JSON.stringify(l.sync_summary, null, 2)}</pre>
        </div>
      </div>
    </Modal>
  );
}

export default function GoogleSyncPage() {
  const { canView } = useSession();
  const toast = useToast();
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [triggerFilter, setTriggerFilter] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState(null);
  const [addSheetOpen, setAddSheetOpen] = useState(false);
  // `null` closed, `{}` a new push, a target object an edit.
  const [pushForm, setPushForm] = useState(null);
  const [runningId, setRunningId] = useState(null);
  const { data: modules } = useFetch(gsyncApi.catalog, [], { initialData: [] });
  const { data: targets, refetchQuiet: reloadTargets } = useFetch(gsyncApi.listTargets, [], { initialData: [] });
  const { data: logs, refetchQuiet: reloadLogs } = useFetch(gsyncApi.list, [], { initialData: [] });
  const GSYNC_LOGS = logs || [];
  // A sync is a long server-side job, and the cron schedule starts them without
  // anyone asking. The log this page renders is therefore written mostly by the
  // backend, which is precisely the case a poll exists for.
  const { refreshNow: refresh } = useLiveData(reloadLogs, { resources: ['google-sync'] });

  if (!canView('webhooks')) return <NoAccessPage module="Google Sync" />;

  const rows = GSYNC_LOGS.filter((l) => (!statusFilter || l.status === statusFilter) && (!typeFilter || l.sync_type === typeFilter) && (!triggerFilter || l.trigger_source === triggerFilter)
    && (!q || l.sheet_name.toLowerCase().includes(q.toLowerCase()) || String(l.id).includes(q)));
  const totalPages = Math.max(1, Math.ceil(rows.length / PS));
  const curPage = Math.min(page, totalPages);
  const slice = rows.slice((curPage - 1) * PS, curPage * PS);
  const isFiltered = !!(statusFilter || typeFilter || triggerFilter || q);
  function clearFilters() { setStatusFilter(''); setTypeFilter(''); setTriggerFilter(''); setQ(''); setPage(1); }

  async function run(type) {
    toast((type === 'full_sync' ? 'Full sync' : GSYNC_TYPE_LABEL[type] + ' sync') + ' started…', 'nf');
    try {
      await gsyncApi.run(type);
      toast('Sync complete', 'ok');
    } catch (err) {
      toast(err.response?.data?.error || 'Sync failed', 'er');
    }
    refresh();
  }
  async function runPush(t) {
    setRunningId(t.id);
    toast(t.name + ' started…', 'nf');
    try {
      const result = await gsyncApi.runTarget(t.id);
      toast(t.name + ' wrote ' + nf(result.log?.records_processed || 0) + ' rows', 'ok');
    } catch (err) {
      toast(err.response?.data?.error || (t.name + ' failed'), 'er');
    }
    setRunningId(null);
    reloadTargets();
    refresh();
  }
  async function removePush(t) {
    try {
      await gsyncApi.deleteTarget(t.id);
      toast(t.name + ' removed', 'ok');
    } catch {
      toast('Could not remove ' + t.name, 'er');
    }
    reloadTargets();
  }
  async function retry(l) {
    toast('Retrying sync #' + l.id + '…', 'nf');
    try {
      const result = await gsyncApi.retry(l.id);
      toast('Retry succeeded · ' + nf(result.records_processed || 0) + ' records', 'ok');
    } catch (err) {
      toast(err.response?.data?.error || 'Retry failed', 'er');
    }
    refresh();
  }

  return (
    <>
      {/* Actions on the tab row, title and description dropped — the shared
          header pattern, see BookingsPage. */}
      <Tabs list={STAB} active={statusFilter} onPick={(id) => { setStatusFilter(id); setPage(1); }}
        actions={<div className="ph-act">
          <button className="btn btn-s btn-sm" onClick={() => setAddSheetOpen(true)}><Icon name="plus" size={13} />Add Sheet</button>
          <button className="btn btn-s btn-sm" onClick={() => setPushForm({})}><Icon name="sheet" size={13} />New push</button>
          <div className="seg" style={{ height: 34 }}>
            <button className="btn btn-p btn-sm" style={{ borderRadius: 'var(--r-md) 0 0 var(--r-md)' }} onClick={() => run('full_sync')}><Icon name="refresh" size={13} />Sync all</button>
            <Popover align="right" trigger={({ toggle }) => <button className="btn btn-p btn-sm btn-ic" style={{ borderRadius: '0 var(--r-md) var(--r-md) 0', borderLeft: '1px solid rgba(255,255,255,.25)' }} onClick={toggle}><Icon name="chevD" size={13} /></button>}>
              {({ close }) => (
                <>
                  <div className="pop-t">Sync now</div>
                  <div className="pop-r">
                    <button className="pop-i" onClick={() => { close(); run('full_sync'); }}><Icon name="refresh" size={15} />Sync all</button>
                    <button className="pop-i" onClick={() => { close(); run('bookings'); }}><Icon name="receipt" size={15} />Bookings only</button>
                    <button className="pop-i" onClick={() => { close(); run('events'); }}><Icon name="calendar" size={15} />Events only</button>
                    <button className="pop-i" onClick={() => { close(); run('crm_mirror'); }}><Icon name="sheet" size={15} />CRM data sheet</button>
                  </div>
                </>
              )}
            </Popover>
          </div>
        </div>}
      />
      {(targets || []).length ? (
        <div className="tw" style={{ marginBottom: 16 }}>
          <div className="tb">
            <span className="tb-m" style={{ fontWeight: 700 }}>Sheet pushes</span>
            <span className="dim" style={{ fontSize: 11.5 }}>Selected columns of one module, written to one tab. Every run replaces that tab in full.</span>
            <div className="tb-sp" /><span className="tb-m"><b>{nf(targets.length)}</b> pushes</span>
          </div>
          <div className="tsc">
            <table className="dt dt-form">
              <thead><tr>{['Name', 'Module', 'Columns', 'Tab', 'Last run', 'Rows', 'Status', ''].map((l) => <th key={l}>{l}</th>)}</tr></thead>
              <tbody>
                {targets.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <span style={{ fontWeight: 650 }}>{t.name}</span>
                      {t.is_enabled ? null : <span className="tg bg-neutral" style={{ marginLeft: 6 }}>Disabled</span>}
                    </td>
                    <td><span className="tg bg-neutral">{t.module_label || t.module}</span></td>
                    <td className="dim" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={(t.column_labels || []).join(', ')}>{(t.column_labels || []).join(', ')}</td>
                    <td className="mono">{t.tab_name}</td>
                    <td className="dim">{t.last_synced_at ? fdate(t.last_synced_at) + ' ' + ftime(t.last_synced_at) : '—'}</td>
                    <td className="mono num">{t.records_synced ? nf(t.records_synced) : '—'}</td>
                    <td><span className={'tg bg-' + (TARGET_TONE[t.last_status] || 'neutral')} title={t.last_error || ''}>{TARGET_LABEL[t.last_status] || t.last_status}</span></td>
                    <td>
                      <div style={{ display: 'flex', gap: 5 }}>
                        <button className="btn btn-sm btn-p" disabled={runningId === t.id || !t.is_enabled} onClick={() => runPush(t)}>{runningId === t.id ? 'Running…' : 'Run'}</button>
                        <button className="btn btn-sm btn-s" onClick={() => setPushForm(t)}>Edit</button>
                        <button className="btn btn-sm btn-s" style={{ color: 'var(--red)' }} onClick={() => removePush(t)}>Remove</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <div className="tb">
        <div className="tb-s"><input className="in in-s" placeholder="Search sync logs…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
        <select className="in" style={{ width: 'auto', height: 35, fontSize: 12 }} value={typeFilter} onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}>
          <option value="">All types</option><option value="bookings">Bookings</option><option value="events">Events</option><option value="full_sync">Full Sync</option><option value="crm_mirror">CRM Data Sheet</option>
        </select>
        <select className="in" style={{ width: 'auto', height: 35, fontSize: 12 }} value={triggerFilter} onChange={(e) => { setTriggerFilter(e.target.value); setPage(1); }}>
          <option value="">All sources</option><option value="admin_manual">Manual</option><option value="scheduler">Scheduler</option><option value="system">System</option>
        </select>
        <div className="tb-sp" /><span className="tb-m"><b>{nf(rows.length)}</b> logs</span>
      </div>
      {slice.length ? (
        <div className="tw">
          <div className="tsc">
            <table className="dt dt-form">
              <thead><tr>{['#', 'Type', 'Sheet', 'Status', 'Mode', 'Started', 'Duration', 'Records', 'Updated', 'Failed', 'Source', 'By', ''].map((l) => <th key={l}>{l}</th>)}</tr></thead>
              <tbody>
                {slice.map((l) => {
                  const canRetry = l.status === 'failed' || l.status === 'partial_success';
                  return (
                    <tr key={l.id} style={{ cursor: 'pointer' }} onClick={() => setDetail(l)}>
                      <td className="mono dim">#{l.id}</td>
                      <td><span className="tg bg-neutral">{GSYNC_TYPE_LABEL[l.sync_type]}</span></td>
                      <td>{l.sheet_name}</td>
                      <td><GsBadge value={l.status} /></td>
                      <td className="dim" style={{ textTransform: 'capitalize' }}>{l.sync_mode}</td>
                      <td className="dim">{fdate(l.started_at)} {ftime(l.started_at)}</td>
                      <td className="mono">{l.duration_seconds != null ? l.duration_seconds.toFixed(1) + 's' : '—'}</td>
                      <td className="mono num">{l.records_processed ?? '—'}</td>
                      <td className="mono num dim">{l.records_updated || '—'}</td>
                      <td className="mono num" style={l.records_failed > 0 ? { color: 'var(--red)', fontWeight: 700 } : { color: 'var(--text-4)' }}>{l.records_failed || '—'}</td>
                      <td className="dim">{GSYNC_TRIGGER_LABEL[l.trigger_source] || l.trigger_source || '—'}</td>
                      <td className="dim" style={{ maxWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis' }}>{l.triggered_by || '—'}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: 'flex', gap: 5 }}>
                          <button className="btn btn-sm btn-s" onClick={() => setDetail(l)}>Detail</button>
                          {canRetry ? <button className="btn btn-sm btn-s" style={{ color: 'var(--t-600)', borderColor: 'var(--t-300)' }} onClick={() => retry(l)}>Retry</button> : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="tf">
            <span>Showing <b>{nf(slice.length)}</b> of <b>{nf(rows.length)}</b> logs</span>
            <div className="pgr">
              <button className="pgb" disabled={curPage <= 1} onClick={() => setPage((p) => p - 1)}><Icon name="chevL" size={13} /></button>
              <span className="pge">Page {curPage} of {totalPages}</span>
              <button className="pgb" disabled={curPage >= totalPages} onClick={() => setPage((p) => p + 1)}><Icon name="chevR" size={13} /></button>
            </div>
          </div>
        </div>
      ) : isFiltered ? (
        <EmptyState icon="filter" title="No matching records found" body="No sync logs match your current search or filters."
          action={<button className="btn btn-s btn-sm" onClick={clearFilters}><Icon name="refresh" size={13} />Clear filters</button>} />
      ) : (
        <EmptyState icon="sheet" title="No Sync Logs Found" body="Click Sync all to start one." />
      )}
      {detail ? <DetailModal log={detail} onClose={() => setDetail(null)} onRetry={retry} /> : null}
      {addSheetOpen ? (
        <AddSheetSourceModal onClose={() => setAddSheetOpen(false)}
          onSaved={() => toast('Manage or sync it from Reports → Sheet Registry', 'nf')} />
      ) : null}
      {pushForm ? (
        <SheetTargetModal target={pushForm.id ? pushForm : null} modules={modules || []}
          onClose={() => setPushForm(null)} onSaved={reloadTargets} />
      ) : null}
    </>
  );
}
