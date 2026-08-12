import { useState, useEffect } from 'react';
import { StatusBadge } from '../../components/Badge';
import { EmptyState } from '../../components/UI';
import { fdate, nf } from '../../lib/helpers';
import * as reportsApi from '../../api/reports';
import * as bookingsApi from '../../api/bookings';
import { useFetch } from '../../hooks/useFetch';

export default function ReportsDataPreview() {
  const { data: sheets } = useFetch(reportsApi.sheets, [], { initialData: [] });
  const { data: bookings } = useFetch(bookingsApi.list, [], { initialData: [] });
  const SHEETS = sheets || [];
  const BOOKINGS = bookings || [];
  const [srcId, setSrcId] = useState('');
  useEffect(() => { if (!srcId && SHEETS.length) setSrcId(SHEETS[0].id); }, [SHEETS.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const src = SHEETS.find((s) => s.id === +srcId) || SHEETS[0];

  if (!src) return <EmptyState icon="sheet" title="No sheets connected" body="Connect a Google Sheet from the Sheet Registry to preview its synced rows here." />;

  return (
    <>
      <div className="tb">
        <select className="in" style={{ width: 'auto' }} value={srcId} onChange={(e) => setSrcId(e.target.value)}>
          {SHEETS.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <div className="tb-sp" />
        <span className="tb-m">Read-only preview of the synced sheet</span>
      </div>
      <div className="tw">
        <div className="tsc">
          <table className="dt">
            <thead><tr><th>Invoice</th><th>Event</th><th>Name</th><th>Status</th><th>Requested</th></tr></thead>
            <tbody>
              {BOOKINGS.slice(0, 12).map((b) => (
                <tr key={b.id}>
                  <td className="mono lnk">{b.invoice_number}</td>
                  <td className="mono" style={{ color: 'var(--t-600)' }}>{b.event_code}</td>
                  <td>{b.name}</td>
                  <td><StatusBadge value={b.payment_status} /></td>
                  <td>{fdate(b.request_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tf"><span>Previewing <b>12</b> of <b>{nf(src.rows)}</b> synced rows</span></div>
      </div>
    </>
  );
}
