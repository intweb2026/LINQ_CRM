import { Icon } from '../../lib/icons';
import { nf } from '../../lib/helpers';
import * as reportsApi from '../../api/reports';
import { useFetch } from '../../hooks/useFetch';

export default function ReportsGrowth() {
  const { data } = useFetch(reportsApi.overview, [], { initialData: { months: [] } });
  const S = data || { months: [] };
  const mx = Math.max(1, ...S.months.map((m) => m.total));

  return (
    <>
      <div className="dg w21">
        <div className="card full">
          <div className="card-h">
            <div><div className="card-t">Bookings by month</div><div className="card-s">Paid, pending, free · 2026</div></div>
            <button className="btn btn-s btn-sm"><Icon name="download" size={13} />Export</button>
          </div>
          <div className="bc">
            {S.months.map((mo) => {
              const H = 142, a = Math.round((mo.paid / mx) * H), b = Math.round((mo.pending / mx) * H), c = Math.round((mo.free / mx) * H);
              return (
                <div className="bc-g" key={mo.label}>
                  <div className="bc-s" style={{ height: a + b + c }}>
                    <i style={{ height: c, background: 'var(--blue)' }} /><i style={{ height: b, background: 'var(--amber)' }} /><i style={{ height: a, background: 'var(--green)' }} />
                    <span className="bc-t"><b>{mo.label}</b> · {nf(mo.total)}<br />Paid {nf(mo.paid)} · Pending {nf(mo.pending)} · Free {nf(mo.free)}</span>
                  </div>
                  <div className="bc-l">{mo.label}</div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
