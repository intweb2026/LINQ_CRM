/**
 * ProposalImportModal.jsx
 * ───────────────────────
 * Two-phase import: preview, then commit against the plan_hash the preview
 * returned.
 *
 * REWRITTEN AGAINST THE CURRENT TREE
 * The previous version of this file was unreachable and would have failed the
 * build the moment anything imported it. It pulled from four paths that no
 * longer exist — components/ui/Button, components/ui/Modal,
 * components/bookings/import/FileUpload and contexts/ToastContext (plural) — and
 * destructured { proposalSubmissionApi, IMPORT_MAX_ROWS } from
 * api/proposalSubmission, which exported neither. Nothing referenced it, so the
 * build passed only because it was dead code.
 *
 * It is rewritten rather than deleted because the endpoints behind it are live:
 * proposal_submission/views.py registers import/preview/ (:613) and
 * import/commit/ (:629), and api/proposalSubmission.js now exports a client for
 * both. This follows PaperReviewImportModal.jsx, which was itself written by
 * mirroring the original of this file, so the two importers stay one design:
 * components/Modal, context/ToastContext, lib/importParse, lib/icons and
 * className styling.
 *
 * The file is parsed IN THE BROWSER by lib/importParse and posted as JSON rows.
 * Nothing is uploaded; there is no multipart endpoint and MEDIA_ROOT is
 * unconfigured.
 *
 * Above 500 rows the file is chunked, because that is the server cap. Every
 * chunk carries the SAME import_batch_id — minted by the first preview and
 * echoed through the rest — so a failure in chunk 3 still leaves chunks 1-2
 * identifiable by one value.
 *
 * DIFFERENCE FROM THE PAPER-REVIEW TWIN
 * import/preview/ here returns no `ignored_columns` key (paper_review reports
 * recognised-but-skipped authorship columns; this importer has none), so that
 * panel is absent rather than rendered empty.
 */
import { useMemo, useState } from 'react';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { useToast } from '../../context/ToastContext';
import { parseFile } from '../../lib/importParse';
import * as proposalApi from '../../api/proposalSubmission';
import { IMPORT_MAX_ROWS } from '../../api/proposalSubmission';

const CLASS_LABEL = {
  CREATE: 'Create',
  CREATE_WITH_WARNING: 'Create (warned)',
  ERROR: 'Error',
};
const CLASS_TONE = { CREATE: 'bg-green', CREATE_WITH_WARNING: 'bg-amber', ERROR: 'bg-red' };

function chunk(rows, size) {
  const out = [];
  for (let i = 0; i < rows.length; i += size) out.push(rows.slice(i, i + size));
  return out;
}

export default function ProposalImportModal({ onClose, onImported }) {
  const toast = useToast();

  const [fileName, setFileName] = useState('');
  const [rows, setRows] = useState([]);
  // [{rows, plan_hash, counts, rows_plan, unrecognised}]
  const [chunks, setChunks] = useState([]);
  const [batchId, setBatchId] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [fatal, setFatal] = useState('');

  const reset = () => {
    setChunks([]); setFatal(''); setBatchId('');
    setProgress({ done: 0, total: 0 });
  };

  // Any new file invalidates the previous plan — commit must re-disable, which it
  // does because `previewed` is derived from chunks and this clears them.
  async function onPickFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    reset();
    setRows([]);
    setFileName(f.name || '');
    try {
      const parsed = await parseFile(f);
      setRows(Array.isArray(parsed.rows) ? parsed.rows : []);
    } catch {
      toast('Could not read that file — check the format and try again', 'er');
      setFileName('');
    }
  }

  const totals = useMemo(() => {
    const acc = { CREATE: 0, CREATE_WITH_WARNING: 0, ERROR: 0 };
    chunks.forEach((c) => {
      Object.entries(c.counts || {}).forEach(([k, v]) => { acc[k] = (acc[k] || 0) + v; });
    });
    return acc;
  }, [chunks]);

  // Row numbers are per-chunk on the server, so they are offset back to
  // file-relative here — otherwise a 1200-row file shows "#1" three times.
  const allPlanRows = useMemo(
    () => chunks.flatMap((c, ci) => (c.rows_plan || []).map((r) => ({
      ...r, row: r.row + ci * IMPORT_MAX_ROWS,
    }))),
    [chunks],
  );

  const unrecognised = useMemo(
    () => [...new Set(chunks.flatMap((c) => c.unrecognised || []))], [chunks]);

  const previewed = chunks.length > 0 && !fatal;
  const importable = totals.CREATE + totals.CREATE_WITH_WARNING;
  const errorRows = allPlanRows.filter((r) => r.classification === 'ERROR');
  const okRows = allPlanRows.filter((r) => r.classification !== 'ERROR');

  async function runPreview() {
    if (!rows.length) return;
    setPreviewing(true); setFatal(''); setChunks([]);
    const batches = chunk(rows, IMPORT_MAX_ROWS);
    setProgress({ done: 0, total: batches.length });
    const collected = [];
    let id = '';
    try {
      for (let i = 0; i < batches.length; i += 1) {
        // The first call mints the id; every later chunk passes it back so the
        // whole file shares one.
        const res = await proposalApi.importPreview(batches[i], id || undefined);
        id = id || res.import_batch_id;
        collected.push({
          rows: batches[i], plan_hash: res.plan_hash, counts: res.counts,
          rows_plan: res.rows, unrecognised: res.unrecognised_columns,
        });
        setProgress({ done: i + 1, total: batches.length });
      }
      setChunks(collected);
      setBatchId(id);
    } catch (err) {
      const data = err.response?.data;
      // A whole-file refusal (MR columns, row cap, no recognisable headers)
      // arrives here and is not per-row — show it as fatal.
      setFatal(data?.detail || data?.rows || 'Could not preview this file.');
      setChunks([]);
    } finally {
      setPreviewing(false);
    }
  }

  async function runCommit() {
    setCommitting(true);
    setProgress({ done: 0, total: chunks.length });
    let created = 0; let skipped = 0;
    try {
      for (let i = 0; i < chunks.length; i += 1) {
        const c = chunks[i];
        const res = await proposalApi.importCommit(
          c.rows, c.plan_hash, batchId, fileName);
        created += res.created;
        skipped += res.skipped;
        setProgress({ done: i + 1, total: chunks.length });
      }
      toast(`Imported ${created} proposal submission${created === 1 ? '' : 's'}`
        + (skipped ? `, skipped ${skipped}` : ''), 'ok');
      onImported?.();
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        setChunks([]);
        setFatal('The data changed since this preview was generated — re-preview the file.');
        toast('The data changed, re-preview', 'er');
      } else {
        toast(err.response?.data?.detail || 'Import failed', 'er');
      }
    } finally {
      setCommitting(false);
    }
  }

  return (
    <Modal size="lg" title="Import proposal submissions"
      sub="Bring historical submissions in from a Zoho export."
      onClose={onClose}
      footer={(
        <>
          <span style={{ flex: 1, fontSize: 11, color: 'var(--text-4)' }}>
            {rows.length > IMPORT_MAX_ROWS
              ? `${rows.length} rows — sent in ${Math.ceil(rows.length / IMPORT_MAX_ROWS)} chunks of ${IMPORT_MAX_ROWS}`
              : rows.length ? `${rows.length} rows` : ''}
            {(previewing || committing) && progress.total > 1
              ? ` · chunk ${progress.done}/${progress.total}` : ''}
          </span>
          <button className="btn btn-s" onClick={onClose}>Cancel</button>
          <button className="btn btn-s" disabled={!rows.length || previewing || committing}
            onClick={runPreview}>
            {previewed ? 'Re-preview' : 'Preview'}
          </button>
          {/* Disabled until a preview returns, and re-disabled the moment the
              file changes (onPickFile clears chunks). */}
          <button className="btn btn-p"
            disabled={!previewed || importable === 0 || committing || previewing}
            onClick={runCommit}>
            <Icon name="upload" size={15} />
            Import{importable > 0 ? ` ${importable}` : ''}
          </button>
        </>
      )}>

      <label className="dz" style={{ cursor: 'pointer', display: 'block' }}>
        <input type="file" accept=".xlsx,.xls,.csv,.json" style={{ display: 'none' }}
          onChange={onPickFile} />
        <div className="dz-i"><Icon name="upload" size={20} /></div>
        <h3>Drop a file, or click to browse</h3>
        <p>.xlsx, .csv or .json — Zoho column names are matched automatically</p>
      </label>

      {fileName && rows.length ? (
        <div className="vr ok" style={{ marginTop: 11 }}>
          <Icon name="check" size={15} />
          <span><b>{fileName}</b> · {rows.length} row{rows.length === 1 ? '' : 's'}</span>
        </div>
      ) : null}

      {/* Stated in the UI rather than left to be discovered: an import writes
          submissions directly and is not a form create. */}
      <div className="hint" style={{ marginTop: 11 }}>
        An import creates proposal submissions only. Rows are written directly, so
        nothing linked to a paper review is generated and no notifications are
        sent — those belong to the form create path.
      </div>

      {fatal ? (
        <div className="vr er" style={{ marginTop: 11 }}>
          <Icon name="warn" size={15} />
          <span>{typeof fatal === 'string' ? fatal : JSON.stringify(fatal)}</span>
        </div>
      ) : null}

      {previewed ? (
        <>
          <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
            {Object.keys(CLASS_LABEL).map((key) => (
              <div key={key} className="kpi" style={{ minWidth: 130 }}>
                <div className="kpi-l">{CLASS_LABEL[key]}</div>
                <div className="kpi-v">{totals[key] || 0}</div>
              </div>
            ))}
          </div>

          {unrecognised.length ? (
            <div className="hint" style={{ marginTop: 11 }}>
              <b>Columns not recognised, and therefore not imported:</b>{' '}
              {unrecognised.join(', ')}
            </div>
          ) : null}

          {/* ERROR rows visually separated, with their reasons. */}
          {errorRows.length ? (
            <div style={{ marginTop: 14 }}>
              <div className="fs-t" style={{ color: 'var(--red)' }}>
                <Icon name="warn" size={13} />
                {errorRows.length} row{errorRows.length === 1 ? '' : 's'} will be skipped
              </div>
              <div style={{ maxHeight: 200, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
                <table className="tb" style={{ fontSize: 11.5 }}>
                  <tbody>
                    {errorRows.map((r) => (
                      <tr key={`e${r.row}`}>
                        <td style={{ width: 52, color: 'var(--text-4)' }}>#{r.row}</td>
                        <td style={{ width: 170 }}>
                          {r.speaker_name || <span className="dim">no name</span>}
                        </td>
                        <td style={{ color: 'var(--red)' }}>
                          {(r.errors || []).map((e, i) => (
                            <div key={i}>
                              <b>{e.field}</b>: {e.problem}
                              {e.value ? ` — got ${JSON.stringify(e.value)}` : ''}
                            </div>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          <div style={{ marginTop: 14 }}>
            <div className="fs-t"><Icon name="check" size={13} />Rows to import</div>
            <div style={{ maxHeight: 260, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
              <table className="tb" style={{ fontSize: 11.5 }}>
                <tbody>
                  {okRows.map((r) => (
                    <tr key={`i${r.row}`}>
                      <td style={{ width: 52, color: 'var(--text-4)' }}>#{r.row}</td>
                      <td style={{ width: 120 }}>
                        <span className={'tg ' + CLASS_TONE[r.classification]}>
                          {CLASS_LABEL[r.classification]}
                        </span>
                      </td>
                      <td className="mono" style={{ width: 130, color: 'var(--t-600)' }}>
                        {r.event_code}
                      </td>
                      <td>{r.speaker_name}</td>
                      <td className="dim">{r.email}</td>
                      <td style={{ color: 'var(--amber)', fontSize: 11 }}>
                        {r.warning || ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : null}
    </Modal>
  );
}
