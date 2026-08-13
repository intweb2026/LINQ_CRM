/**
 * PaperReviewImportModal.jsx
 * ──────────────────────────
 * Two-phase import: preview, then commit against the plan_hash the preview
 * returned.
 *
 * The file is parsed IN THE BROWSER by lib/importParse and posted as JSON rows —
 * matching every other import in this app. Nothing is uploaded; there is no
 * multipart endpoint and MEDIA_ROOT is unconfigured.
 *
 * Above 500 rows the file is chunked, because that is the server cap
 * (DATA_UPLOAD_MAX_MEMORY_SIZE is at Django's 2.5 MB default and an uncapped
 * paste fails opaquely). Every chunk carries the SAME import_batch_id — minted by
 * the first preview and echoed through the rest — so a failure in chunk 3 still
 * leaves chunks 1-2 identifiable by one value.
 *
 * ON MIRRORING ProposalImportModal.jsx
 * That file describes the right UX and is what this follows, but it could not be
 * copied: it imports components/ui/Button, components/ui/Modal,
 * contexts/ToastContext and components/bookings/import/FileUpload — none of which
 * exist in this tree — and destructures { proposalSubmissionApi, IMPORT_MAX_ROWS }
 * from an api module that exports neither. It is referenced by nothing and would
 * fail to build the moment it were imported. This file uses the LIVE conventions
 * instead: components/Modal, context/ToastContext, lib/importParse and className
 * styling, the same set components/ImportWizard.jsx (the import component
 * actually in use, on four pages) is built from.
 */
import { useMemo, useState } from 'react';
import Modal from '../../components/Modal';
import FileDropZone from '../../components/FileDropZone';
import { Icon } from '../../lib/icons';
import { useToast } from '../../context/ToastContext';
import { parseFile } from '../../lib/importParse';
import * as paperReviewApi from '../../api/paperReview';
import { IMPORT_MAX_ROWS } from '../../api/paperReview';

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

export default function PaperReviewImportModal({ onClose, onImported }) {
  const toast = useToast();

  const [fileName, setFileName] = useState('');
  const [rows, setRows] = useState([]);
  // [{rows, plan_hash, counts, rows_plan, unrecognised, ignored}]
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
  //
  // The preview then runs IMMEDIATELY, without waiting for the button. Import is
  // disabled until a plan exists, so picking a file and finding a greyed-out Import
  // is a dead end that gives no clue the missing step is Preview — the state this
  // was reported from. Previewing writes nothing, so there is nothing to consent to;
  // the button stays as "Re-preview" for a deliberate re-run.
  // Takes a File, not a change event — FileDropZone hands over the file whether it
  // came from the browse dialog or from a drop.
  async function onPickFile(f) {
    if (!f) return;
    reset();
    setRows([]);
    setFileName(f.name || '');
    try {
      const parsed = await parseFile(f);
      const parsedRows = Array.isArray(parsed.rows) ? parsed.rows : [];
      setRows(parsedRows);
      // Passed explicitly: setRows has not landed in state yet at this point.
      if (parsedRows.length) runPreview(parsedRows);
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

  // One per file, not per chunk: every chunk asks the same server the same
  // question, so the first non-empty answer is the answer.
  const notice = useMemo(
    () => chunks.map((c) => c.notice).find(Boolean) || '', [chunks]);

  const unrecognised = useMemo(
    () => [...new Set(chunks.flatMap((c) => c.unrecognised || []))], [chunks]);
  const ignored = useMemo(
    () => [...new Set(chunks.flatMap((c) => c.ignored || []))], [chunks]);

  const previewed = chunks.length > 0 && !fatal;
  const importable = totals.CREATE + totals.CREATE_WITH_WARNING;
  const errorRows = allPlanRows.filter((r) => r.classification === 'ERROR');
  const okRows = allPlanRows.filter((r) => r.classification !== 'ERROR');

  // `sourceRows` defaults to state for the button, and is passed explicitly by
  // onPickFile, which runs before setRows has landed.
  async function runPreview(sourceRows = rows) {
    if (!sourceRows.length) return;
    setPreviewing(true); setFatal(''); setChunks([]);
    const batches = chunk(sourceRows, IMPORT_MAX_ROWS);
    setProgress({ done: 0, total: batches.length });
    const collected = [];
    let id = '';
    try {
      for (let i = 0; i < batches.length; i += 1) {
        // The first call mints the id; every later chunk passes it back so the
        // whole file shares one.
        const res = await paperReviewApi.importPreview(batches[i], id || undefined);
        id = id || res.import_batch_id;
        collected.push({
          rows: batches[i], plan_hash: res.plan_hash, counts: res.counts,
          rows_plan: res.rows, unrecognised: res.unrecognised_columns,
          ignored: res.ignored_columns, notice: res.notice,
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
        const res = await paperReviewApi.importCommit(
          c.rows, c.plan_hash, batchId, fileName);
        created += res.created;
        skipped += res.skipped;
        setProgress({ done: i + 1, total: chunks.length });
      }
      toast(`Imported ${created} paper review${created === 1 ? '' : 's'}`
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
    <Modal size="lg" title="Import paper reviews"
      sub="Bring historical reviews in from a Zoho export."
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
          {/* runPreview is WRAPPED, not passed bare: onClick would hand the click
              event to its first parameter (`sourceRows`), and an event has no
              .length, so the preview would return immediately having done nothing. */}
          <button className="btn btn-s" disabled={!rows.length || previewing || committing}
            onClick={() => runPreview()}>
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

      <FileDropZone
        onFile={onPickFile}
        onReject={(msg) => toast(msg, 'er')}
        disabled={previewing || committing}
      />

      {fileName && rows.length ? (
        <div className="vr ok" style={{ marginTop: 11 }}>
          <Icon name="check" size={15} />
          <span><b>{fileName}</b> · {rows.length} row{rows.length === 1 ? '' : 's'}</span>
        </div>
      ) : null}

      {/* B2, stated in the UI rather than left to be discovered: an import is not
          a form create, and neither workflow fires. */}
      <div className="hint" style={{ marginTop: 11 }}>
        An import creates paper reviews only. It does <b>not</b> generate proposal
        submissions and does <b>not</b> send production-team notifications — those
        fire when a review is created through the form.
      </div>

      {fatal ? (
        <div className="vr er" style={{ marginTop: 11 }}>
          <Icon name="warn" size={15} />
          <span>{typeof fatal === 'string' ? fatal : JSON.stringify(fatal)}</span>
        </div>
      ) : null}

      {/* A whole-file explanation from the server — currently only "the Events
          catalogue is empty". Shown ABOVE the per-row errors because when it is
          set, every row carries the same unhelpful "no matching event" and this is
          the only line that says why. */}
      {notice ? (
        <div className="vr wn" style={{ marginTop: 11 }}>
          <Icon name="warn" size={15} />
          <span>{notice}</span>
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

          {ignored.length ? (
            <div className="hint" style={{ marginTop: 8 }}>
              <b>Recognised but deliberately not imported:</b> {ignored.join(', ')} —
              authorship and creation time are recorded for whoever runs the import,
              not copied from the file.
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
