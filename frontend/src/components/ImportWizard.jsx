import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import { useToast } from '../context/ToastContext';
import { parseFile } from '../lib/importParse';
import * as importApi from '../api/import';
import FileDropZone from './FileDropZone';

const STEPS = ['Upload', 'Map fields', 'Review', 'Import'];
const SKIP = '— Skip this column —';

const nrm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');

// Guesses a target field for a source column name — starting point only; the
// mapping step lets the user correct any of it.
//
// Exact matches on the key, the label or a declared alias are resolved across
// ALL fields before any loose matching. The loose test is symmetric, so a header
// like "Sales Team" matches speaker_sales_team (its key contains the header) just
// as "Speaker Sales Team" matches sales_team (the header contains its key).
// Without the exact pass, whichever field is declared first wins BOTH columns and
// one of them silently overwrites the other in buildRows().
function autoMap(headers, fields) {
  const map = {};
  headers.forEach((h) => {
    const norm = nrm(h);
    const exact = fields.find(([key, label, aliases]) => (
      norm === nrm(key) || norm === nrm(label) || (aliases || []).some((a) => nrm(a) === norm)
    ));
    const hit = exact || fields.find(([key]) => {
      const kn = nrm(key);
      return norm.includes(kn) || kn.includes(norm);
    });
    map[h] = hit ? hit[0] : SKIP;
  });
  return map;
}

// Rough time left, from the rate the batches have actually run at. Deliberately
// coarse: the point is "seconds or minutes", not a countdown, and a number that
// twitches every batch reads as less trustworthy than one that does not.
function fmtEta(ms) {
  const s = Math.ceil(ms / 1000);
  if (s < 10) return 'a few seconds left';
  if (s < 90) return `about ${Math.ceil(s / 5) * 5}s left`;
  return `about ${Math.ceil(s / 60)} min left`;
}

/**
 * `onImported` — called once the batches are in, so the table behind the wizard
 * shows what was just imported.
 *
 * It did not exist. This component took `{ kind, onClose }` only, on all four
 * pages that mount it, so the single largest way data enters this CRM — a
 * spreadsheet of bookings, tickets or events — landed in the database and left
 * the screen showing the old rows. "Import 4,000 bookings" followed by a table
 * that still says 12,000 reads as an import that silently failed, and the only
 * way to see otherwise was F5.
 *
 * Fired on the failure path too, not only on success. Rows go up in batches of
 * 500, so a request that throws on batch seven has already written six — and the
 * table behind is then MORE wrong than after a clean import, not less.
 */
export default function ImportWizard({ kind, onClose, onImported }) {
  const toast = useToast();
  /**
   * The mappable fields, from the server's own importer registry where it has one
   * (invoices/import_schema/, tickets/import_schema/), else the static list.
   *
   * Fetched rather than read straight off TARGET_FIELDS because that list had
   * drifted behind the importers — 17 of 28 accepted booking columns, 15 of ~40
   * ticket columns. An absent field cannot be mapped, so its column is skipped,
   * and a skipped column is indistinguishable from one the file never had.
   *
   * The static list is the initial value, so the mapping step is never empty while
   * the request is in flight and never broken if it fails.
   */
  const [fields, setFields] = useState(
    () => importApi.TARGET_FIELDS[kind] || importApi.TARGET_FIELDS.bookings,
  );
  useEffect(() => {
    let cancelled = false;
    importApi.fetchTargetFields(kind).then((f) => { if (!cancelled) setFields(f); });
    return () => { cancelled = true; };
  }, [kind]);
  const [step, setStep] = useState(0);
  const [file, setFile] = useState(null);
  const [parsed, setParsed] = useState(null); // { headers, rows }
  const [mapping, setMapping] = useState({});
  const [parsing, setParsing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  /**
   * Running totals from api/import.run, refreshed after every 500-row batch:
   * { imported, skipped, errors, sent, total, batch, totalBatches, eta }.
   *
   * The import step used to render a fixed 60%-wide bar and the words
   * "Keep this window open until it finishes" for however long the whole file
   * took, which on a 20,000-row spreadsheet is dozens of sequential requests.
   * Nothing on screen changed between the first batch and the last, so a slow
   * import and a hung one looked identical and there was no way to judge how
   * long was left.
   */
  const [progress, setProgress] = useState(null);

  // Takes a File, not a change event: the same handler now serves the browse dialog
  // and a dropped file, which are different events carrying the file in different
  // places (target.files vs dataTransfer.files). FileDropZone normalises that.
  async function onPickFile(f) {
    if (!f) return;
    setFile(f);
    setParsing(true);
    try {
      const p = await parseFile(f);
      setParsed(p);
      setMapping(autoMap(p.headers, fields));
    } catch (err) {
      toast('Could not read that file — check the format and try again', 'er');
      setFile(null);
      setParsed(null);
    } finally {
      setParsing(false);
    }
  }

  // Re-guess the mapping if the field list arrives after the file was picked.
  // Confined to step 0 so it can never overwrite a mapping the user has edited —
  // they can only edit it on step 1 — while still making sure the guesses were
  // made against the FULL list rather than the static fallback.
  useEffect(() => {
    if (step !== 0 || !parsed) return;
    setMapping(autoMap(parsed.headers, fields));
  }, [fields, parsed, step]);

  const mappedCount = useMemo(() => Object.values(mapping).filter((v) => v !== SKIP).length, [mapping]);

  function buildRows() {
    return (parsed?.rows || []).map((row) => {
      const out = {};
      Object.entries(mapping).forEach(([header, target]) => {
        if (target !== SKIP) out[target] = row[header];
      });
      return out;
    });
  }

  async function startImport() {
    setStep(3);
    setImporting(true);
    setProgress(null); // a retry after a failed attempt must not show the old run's counts
    const startedAt = Date.now();
    // Held alongside the state so the failure toast can name how many rows were
    // already written; setProgress is async and cannot be read back here.
    let last = null;
    try {
      const rows = buildRows();
      const res = await importApi.run(kind, rows, (p) => {
        // ETA from the measured rate rather than a guessed per-batch cost: batch
        // time varies with the resource and with how much of the file is
        // duplicates, so only this run's own throughput predicts this run.
        const eta = p.sent > 0 && p.sent < p.total
          ? ((Date.now() - startedAt) / p.sent) * (p.total - p.sent)
          : null;
        last = { ...p, eta };
        setProgress(last);
      });
      setResult(res);
      toast(res.imported + ' ' + kind + ' imported' + (res.skipped ? `, ${res.skipped} skipped` : ''), 'ok');
    } catch (err) {
      setImporting(false);
      setStep(2);
      const wrote = last?.imported
        ? `Import stopped after ${last.imported} of ${last.total} rows. `
        : '';
      toast(wrote + (err.response?.data?.detail || 'Import failed — check the file and try again'), 'er');
      // A throw part-way through a multi-batch import leaves the earlier batches
      // written, so the table behind is stale either way.
      onImported?.();
      return;
    }
    setImporting(false);
    onImported?.();
  }

  return (
    <Modal size="lg" onClose={onClose}
      header={
        <div style={{ width: '100%' }}>
          <div className="md-h">
            <div className="md-h-b"><h2>Smart import — {kind}</h2><p>Bring records in from a spreadsheet or export file.</p></div>
            <button className="dr-x" aria-label="Close" onClick={onClose}><Icon name="x" size={15} /></button>
          </div>
          <div className="wz">
            {STEPS.map((s, i) => (
              <div key={s} style={{ display: 'contents' }}>
                <div className={'wz-s' + (i < step ? ' dn' : i === step ? ' cu' : '')}>
                  <span className="wz-n">{i < step ? <Icon name="check" size={10} /> : i + 1}</span>{s}
                </div>
                {i < STEPS.length - 1 ? <div className={'wz-ln' + (i < step ? ' dn' : '')} /> : null}
              </div>
            ))}
          </div>
        </div>
      }
      footer={step === 0 ? (
        <><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" disabled={!parsed || parsing} onClick={() => setStep(1)}>Continue</button></>
      ) : step === 1 ? (
        <><button className="btn btn-s" onClick={() => setStep(0)}>Back</button><button className="btn btn-p" disabled={!mappedCount} onClick={() => setStep(2)}>Continue</button></>
      ) : step === 2 ? (
        <><button className="btn btn-s" onClick={() => setStep(1)}>Back</button><button className="btn btn-p" disabled={importing} onClick={startImport}><Icon name="upload" size={15} />Start import</button></>
      ) : result ? (
        <button className="btn btn-p" onClick={onClose}>Done</button>
      ) : null}
    >
      {step === 0 && (
        <>
          <FileDropZone
            onFile={onPickFile}
            onReject={(msg) => toast(msg, 'er')}
            hint=".xlsx, .csv or .json — up to 50 MB"
            disabled={parsing || importing}
          />
          {parsing ? <div className="hint" style={{ marginTop: 11 }}>Reading file…</div> : null}
          {file && parsed && (
            <div className="vr ok" style={{ marginTop: 11 }}>
              <Icon name="check" size={15} /><span><b>{file.name}</b> · {parsed.rows.length} row{parsed.rows.length === 1 ? '' : 's'} · {parsed.headers.length} column{parsed.headers.length === 1 ? '' : 's'}</span>
            </div>
          )}
          <div className="hint" style={{ marginTop: 11 }}>Columns are matched automatically against the {kind} schema. Anything unmatched can be mapped by hand on the next step.</div>
        </>
      )}
      {step === 1 && parsed && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--text-3)', marginBottom: 13 }}>
            Map each column from your file to a {kind} field. {mappedCount} of {parsed.headers.length} mapped
            {/* The field count is shown because the previous complaint was that
                fields were MISSING from this list, and a number is the only way
                to see at a glance that they are not. */}
            {' '}· {fields.length} {kind} fields available.
          </p>
          {parsed.headers.map((h) => (
            <div className="mp" key={h}>
              <div className="mp-s">{h}</div>
              <div className="mp-a"><Icon name="arrowR" size={15} /></div>
              <select className="in in-xs" value={mapping[h] || SKIP} onChange={(e) => setMapping((m) => ({ ...m, [h]: e.target.value }))}>
                {fields.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                <option value={SKIP}>{SKIP}</option>
              </select>
            </div>
          ))}
        </>
      )}
      {step === 2 && parsed && (
        <>
          <div className="vr ok"><Icon name="check" size={15} /><span><b>{file?.name}</b> ready to import — {parsed.rows.length} row{parsed.rows.length === 1 ? '' : 's'}, {mappedCount} column{mappedCount === 1 ? '' : 's'} mapped</span></div>
          <div className="hint" style={{ marginTop: 11 }}>Validation runs on the server once the import starts. Dates are normalised to ISO before write; existing records matched on reference are updated, not duplicated.</div>
        </>
      )}
      {step === 3 && (
        <div style={{ textAlign: 'center', padding: '16px 0 6px' }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{importing ? 'Importing…' : 'Done'}</h3>
          {result ? (
            <p style={{ fontSize: 12, color: 'var(--text-4)' }}>
              {result.imported} imported{result.skipped ? `, ${result.skipped} skipped` : ''}{result.errors.length ? `, ${result.errors.length} error${result.errors.length === 1 ? '' : 's'}` : ''}.
            </p>
          ) : progress ? (
            <>
              {/* The live count is the headline, because "is it still moving"
                  is the question this screen exists to answer. It counts rows
                  actually written, so it can legitimately trail the bar when the
                  server skips duplicates; the skipped tally below explains the gap. */}
              <p style={{ fontSize: 19, fontWeight: 700, color: 'var(--text)', lineHeight: 1.3 }}>
                {progress.imported.toLocaleString()}
                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-4)' }}>
                  {' '}/ {progress.total.toLocaleString()} added
                </span>
              </p>
              <p style={{ fontSize: 12, color: 'var(--text-4)' }}>
                {progress.skipped ? `${progress.skipped.toLocaleString()} skipped, ` : ''}
                {progress.errors ? `${progress.errors.toLocaleString()} error${progress.errors === 1 ? '' : 's'}, ` : ''}
                batch {Math.min(progress.batch + 1, progress.totalBatches)} of {progress.totalBatches}
              </p>
            </>
          ) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>Keep this window open until it finishes.</p>}
          {/* Width tracks rows PROCESSED, not rows written: a file that is half
              duplicates still has half its work done, and a bar that stalled at
              50% on a successful import would be the same lie as no bar at all. */}
          <div className="pt">
            <i style={{ width: (importing ? (progress && progress.total ? Math.round((progress.sent / progress.total) * 100) : 0) : 100) + '%' }} />
          </div>
          {importing ? (
            <p style={{ fontSize: 11.5, color: 'var(--text-4)' }}>
              {progress?.eta ? fmtEta(progress.eta) : 'Working out how long this will take…'}
              {' '}Keep this window open until it finishes.
            </p>
          ) : null}
        </div>
      )}
    </Modal>
  );
}
