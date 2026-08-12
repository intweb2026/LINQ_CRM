import * as reportsApi from '../../api/reports';
import { EmptyState } from '../../components/UI';
import { useFetch } from '../../hooks/useFetch';
import { nf, rel } from '../../lib/helpers';

const T2T = { success: 'green', error: 'red', partial: 'amber' };

export default function ReportsLogs() {
  const { data: logs } = useFetch(reportsApi.syncLogs, [], { initialData: [] });
  const SYNC_LOGS = logs || [];
  return (
    <>
      <div className="tb">
        <div className="tb-s"><input className="in in-s" placeholder="Search logs…" /></div>
        <div className="tb-sp" />
        <span className="tb-m"><b>{SYNC_LOGS.length}</b> runs</span>
      </div>
      {SYNC_LOGS.length ? (
        <div className="tw">
          <div className="tsc" style={{ maxHeight: 520 }}>
            <table className="dt">
              <thead><tr><th>Status</th><th>Source</th><th className="num">Read</th><th className="num">Written</th><th className="num">Duration</th><th>Started</th><th>Message</th></tr></thead>
              <tbody>
                {SYNC_LOGS.map((l) => (
                  <tr key={l.id}>
                    <td><span className={'bg bg-' + T2T[l.status]}><i />{l.status}</span></td>
                    <td>{l.source}</td>
                    <td className="num">{nf(l.rows_read)}</td>
                    <td className="num">{nf(l.rows_written)}</td>
                    <td className="num mono">{(l.duration_ms / 1000).toFixed(1)}s</td>
                    <td>{rel(l.started_at)}</td>
                    <td className="dim" style={{ fontSize: 11.5 }}>{l.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <EmptyState icon="sheet" title="No Sync Logs Found" body="Sync run history will appear here once a sync has completed." />
      )}
    </>
  );
}
