import { useState } from 'react';
import AddSheetSourceModal from '../../components/AddSheetSourceModal';
import { Icon } from '../../lib/icons';
import { nf, rel } from '../../lib/helpers';
import { useToast } from '../../context/ToastContext';
import { useFetch } from '../../hooks/useFetch';
import * as reportsApi from '../../api/reports';

const ST2T = { synced: 'green', syncing: 'amber', error: 'red' };

export default function ReportsRegistry() {
  const toast = useToast();
  const { data: sheets, refetch } = useFetch(reportsApi.sheets, [], { initialData: [] });
  const SHEETS = sheets || [];
  const [q, setQ] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const filtered = SHEETS.filter((s) => s.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <div className="tb">
        <div className="tb-s"><input className="in in-s" placeholder="Search sources…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="tb-sp" />
        <button className="btn btn-p btn-sm" onClick={() => { toast('Syncing all sources…', 'nf'); reportsApi.syncAll().then(refetch); }}><Icon name="refresh" size={13} />Sync all</button>
        <button className="btn btn-s btn-sm" onClick={() => setAddOpen(true)}><Icon name="plus" size={13} />Add source</button>
      </div>
      <div className="cg">
        {filtered.map((s) => (
          <div className="rc" key={s.id}>
            <div className="rc-t">
              <span className="kpi-i" style={{ background: 'var(--t-50)', color: 'var(--t-600)', flexShrink: 0 }}><Icon name="sheet" size={15} /></span>
              <span className="who-t" style={{ flex: 1 }}><span className="who-n">{s.name}</span><span className="who-s">{s.worksheet}</span></span>
              <span className={'bg bg-' + (ST2T[s.status] || 'neutral')}><i />{s.status}</span>
            </div>
            <div className="rc-m">
              <div><div className="l">Rows</div><div className="v">{nf(s.rows)}</div></div>
              <div><div className="l">Interval</div><div className="v">{s.interval}</div></div>
              <div><div className="l">Last sync</div><div className="v">{s.last_sync ? rel(s.last_sync) : 'Never'}</div></div>
              <div><div className="l">Type</div><div className="v">{s.type}</div></div>
            </div>
            {s.error ? <div className="vr er" style={{ margin: 0 }}><Icon name="warn" size={14} /><span>{s.error}</span></div> : null}
          </div>
        ))}
      </div>
      {addOpen ? (
        <AddSheetSourceModal onClose={() => setAddOpen(false)} onSaved={refetch} />
      ) : null}
    </>
  );
}
