import { useState, useMemo } from 'react';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import { useToast } from '../context/ToastContext';
import { parseFile } from '../lib/importParse';
import * as importApi from '../api/import';

const STEPS = ['Upload', 'Map fields', 'Review', 'Import'];
const SKIP = '— Skip this column —';

// Guesses a target field for a source column name by loose substring match —
// starting point only; the mapping step lets the user correct any of it.
function autoMap(headers, fields) {
  const map = {};
  headers.forEach((h) => {
    const norm = h.toLowerCase().replace(/[^a-z0-9]/g, '');
    const hit = fields.find(([key, label]) => {
      const kn = key.toLowerCase().replace(/[^a-z0-9]/g, '');
      const ln = label.toLowerCase().replace(/[^a-z0-9]/g, '');
      return norm === kn || norm === ln || norm.includes(kn) || kn.includes(norm);
    });
    map[h] = hit ? hit[0] : SKIP;
  });
  return map;
}

export default function ImportWizard({ kind, onClose }) {
  const toast = useToast();
  const fields = importApi.TARGET_FIELDS[kind] || importApi.TARGET_FIELDS.bookings;
  const [step, setStep] = useState(0);
  const [file, setFile] = useState(null);
  const [parsed, setParsed] = useState(null); // { headers, rows }
  const [mapping, setMapping] = useState({});
  const [parsing, setParsing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);

  async function onPickFile(e) {
    const f = e.target.files && e.target.files[0];
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
    try {
      const rows = buildRows();
      const res = await importApi.run(kind, rows);
      setResult(res);
      toast(res.imported + ' ' + kind + ' imported' + (res.skipped ? `, ${res.skipped} skipped` : ''), 'ok');
    } catch (err) {
      setImporting(false);
      setStep(2);
      toast(err.response?.data?.detail || 'Import failed — check the file and try again', 'er');
      return;
    }
    setImporting(false);
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
          <label className="dz" style={{ cursor: 'pointer', display: 'block' }}>
            <input type="file" accept=".xlsx,.xls,.csv,.json" style={{ display: 'none' }} onChange={onPickFile} />
            <div className="dz-i"><Icon name="upload" size={20} /></div>
            <h3>Drop a file, or click to browse</h3>
            <p>.xlsx, .csv or .json — up to 50 MB</p>
          </label>
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
          <p style={{ fontSize: 12.5, color: 'var(--text-3)', marginBottom: 13 }}>Map each column from your file to a {kind} field. {mappedCount} of {parsed.headers.length} mapped.</p>
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
          ) : <p style={{ fontSize: 12, color: 'var(--text-4)' }}>Keep this window open until it finishes.</p>}
          <div className="pt"><i style={{ width: (importing ? 60 : 100) + '%' }} /></div>
        </div>
      )}
    </Modal>
  );
}
