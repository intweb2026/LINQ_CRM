import { Fragment, useState } from 'react';
import { Icon } from '../../lib/icons';
import { toExcel, fileName } from '../../lib/exportSheet';
import * as pedApi from '../../api/preEventDocs';

/**
 * The badge log, made visible.
 *
 * WHY THIS EXISTS IN THIS FORM. The log worked before this, but the page showed
 * only a count and a timestamp per run, so there was no way to answer either of
 * the two questions that matter: what exactly did we freeze, and does what is
 * stored still hold. A record nobody can inspect is a record nobody can trust.
 *
 * So a run expands into its FROZEN ROWS, each shown beside what the booking says
 * today, and every run carries a drift count.
 *
 * DRIFT IS NOT AN ERROR, and the wording is careful about that. A row reading
 * "Name changed" means the log correctly remembers the older spelling, which is
 * the entire reason it exists; that row is exactly what Additional Name Badges
 * turns into a correction. A run where everything reads "Matches" is one where
 * nothing has moved since it was frozen.
 *
 * The per-run Excel export is the other half of verification: take the frozen
 * list, open it beside whatever went to the badge printer, and compare.
 */

const DRIFT_TONE = {
  Matches: 'green',
  'Name changed': 'amber',
  'Company changed': 'amber',
  'Both changed': 'amber',
  'Booking removed': 'red',
};

export default function RunHistory({ runs, label, mayUndo, onUndo }) {
  const [openRun, setOpenRun] = useState(null);
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  async function toggle(runId) {
    if (openRun === runId) {
      setOpenRun(null);
      setRows(null);
      return;
    }
    setOpenRun(runId);
    setRows(null);
    setErr('');
    setLoading(true);
    try {
      const d = await pedApi.runDetail(runId);
      setRows(d.badges);
    } catch (e) {
      setErr('Could not load that snapshot.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="ped-runs">
      <header className="ped-sec-h">
        <div>
          <h3>Frozen badge runs</h3>
          <p>
            Every run ever logged for this event, stored in{' '}
            <code>pre_event_docs_badge_issues</code>. Open one to see exactly
            what was frozen and whether it still matches the booking.
          </p>
        </div>
        <span className="tg">{runs.length}</span>
      </header>

      {runs.length ? (
        <table className="ped-tbl">
          <thead>
            <tr>
              <th />
              <th>Frozen</th>
              {/* .num and .ta-r on the HEADER too, or the figures and the button
                  sit right while their headings sit left. */}
              <th className="num">Badges</th>
              <th>Changed since</th>
              <th>By</th>
              <th className="ta-r" />
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.run_id}>
                <tr className={openRun === r.run_id ? 'ped-run-on' : ''}>
                  <td className="ped-run-x">
                    <button className="ped-run-t" onClick={() => toggle(r.run_id)}
                      aria-expanded={openRun === r.run_id}
                      aria-label={openRun === r.run_id ? 'Hide the snapshot' : 'Show the snapshot'}>
                      <Icon name={openRun === r.run_id ? 'chevD' : 'chevR'} size={13} />
                    </button>
                  </td>
                  <td>{new Date(r.issued_at).toLocaleString()}</td>
                  <td className="num">{r.badges}</td>
                  <td>
                    {r.drifted
                      ? <span className="tg bg-amber">{r.drifted} of {r.badges}</span>
                      : <span className="tg bg-green">none</span>}
                  </td>
                  <td>
                    {r.issued_by
                      || <span className="dim" title="Brought across from the workbook Database_Sent tab">imported</span>}
                  </td>
                  <td className="ta-r">
                    {mayUndo ? (
                      <button className="btn btn-sm btn-s" onClick={() => onUndo(r)}>
                        <Icon name="trash" size={13} />Undo
                      </button>
                    ) : null}
                  </td>
                </tr>
                {openRun === r.run_id ? (
                  <tr>
                    <td colSpan={6} className="ped-run-d">
                      {loading ? <p className="ped-none">Loading the snapshot…</p> : null}
                      {err ? <p className="ped-err">{err}</p> : null}
                      {rows ? (
                        <>
                          <div className="ped-run-bar">
                            <span>
                              {rows.length} badges frozen on{' '}
                              {new Date(r.issued_at).toLocaleDateString()}
                            </span>
                            <button className="btn btn-sm btn-s" onClick={() => toExcel(
                              rows,
                              [['name', 'Name as frozen'], ['company', 'Company as frozen'],
                                ['name_now', 'Name now'], ['company_now', 'Company now'],
                                ['drift', 'Status']],
                              fileName(label, 'frozen badge run'),
                              'Frozen run',
                            )}>
                              <Icon name="download" size={13} />Export this run
                            </button>
                          </div>
                          <table className="ped-tbl">
                            <thead>
                              <tr>
                                <th>Name as frozen</th>
                                <th>Company as frozen</th>
                                <th>Status</th>
                                <th>Reads now</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((x) => (
                                <tr key={x.badge_id}>
                                  <td>{x.name}</td>
                                  <td>{x.company}</td>
                                  <td>
                                    <span className={'tg bg-' + (DRIFT_TONE[x.drift] || 'neutral')}>
                                      {x.drift}
                                    </span>
                                  </td>
                                  {/* Only shown where it differs, so the eye lands
                                      on the rows that moved rather than reading
                                      the same two values twice on every line. */}
                                  <td className="dim">
                                    {x.drift === 'Matches' ? ''
                                      : x.drift === 'Booking removed'
                                        ? 'no booking'
                                        : [x.name_now, x.company_now].filter(Boolean).join(' · ')}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="ped-none">
          Nothing frozen yet, so every name on the check-in sheet reads as a new
          badge. Freezing happens when you print a badge run.
        </p>
      )}
    </section>
  );
}
