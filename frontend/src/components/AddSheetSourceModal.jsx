import { useState } from 'react';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import * as sheetSourcesApi from '../api/sheetSources';
import { useToast } from '../context/ToastContext';

const SHEET_TYPES = ['bookings', 'events', 'delegates', 'revenue', 'pipeline', 'attendance', 'custom'];
const SHEET_TYPE_LABEL = {
  bookings: 'Bookings', events: 'Events', delegates: 'Delegates', revenue: 'Revenue',
  pipeline: 'Pipeline', attendance: 'Attendance', custom: 'Custom',
};
const FREQUENCIES = [
  { value: 'manual', label: 'Manual Only' },
  { value: 'hourly', label: 'Every Hour' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
];

// Real backend: POST /api/reports/sources/ (create) and POST /api/reports/sources/list-worksheets/
// (live tab detection) — see backend/reports/views.py + services/sync.py. Both already exist and
// work against the configured Google Sheets service account; nothing here is a placeholder.
export default function AddSheetSourceModal({ onClose, onSaved }) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [type, setType] = useState('custom');
  const [url, setUrl] = useState('');
  const [worksheet, setWorksheet] = useState('Sheet1');
  const [tabs, setTabs] = useState(null);
  const [detecting, setDetecting] = useState(false);
  const [frequency, setFrequency] = useState('manual');
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [description, setDescription] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);

  async function detectTabs() {
    if (!url.trim()) { toast('Paste a Google Sheet URL first', 'er'); return; }
    setDetecting(true);
    try {
      const worksheets = await sheetSourcesApi.listWorksheets(url.trim());
      if (!worksheets.length) { toast('No worksheets found in that sheet', 'wn'); return; }
      setTabs(worksheets);
      if (!worksheets.includes(worksheet)) setWorksheet(worksheets[0]);
      toast('Found ' + worksheets.length + ' worksheet' + (worksheets.length === 1 ? '' : 's'), 'ok');
    } catch {
      toast('Could not detect worksheets — check the URL and sharing permissions', 'er');
    } finally {
      setDetecting(false);
    }
  }

  async function create() {
    if (saving) return;
    const trimmedName = name.trim(), trimmedUrl = url.trim();
    if (!trimmedName) { toast('Name is required', 'er'); return; }
    if (!trimmedUrl) { toast('Google Sheet URL is required', 'er'); return; }
    setSaving(true);
    try {
      await sheetSourcesApi.addSource({
        name: trimmedName, url: trimmedUrl, type, worksheet: worksheet.trim() || 'Sheet1',
        frequency, syncEnabled, description: description.trim(), notes: notes.trim(),
      });
    } catch (err) {
      const detail = err.response?.data;
      const fieldError = detail && typeof detail === 'object' ? Object.values(detail)[0] : null;
      toast(Array.isArray(fieldError) ? String(fieldError[0]) : 'Could not add the sheet — check the URL and try again', 'er');
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
    toast(trimmedName + ' connected as a sheet source', 'ok');
    onSaved?.();
  }

  return (
    <Modal title="Add Google Sheet Source" sub="Connect a Google Sheet as a live data source for reports and sync." onClose={onClose}
      footer={<><button className="btn btn-s" onClick={onClose}>Cancel</button><button className="btn btn-p" disabled={saving} onClick={create}><Icon name="link" size={15} />{saving ? 'Creating…' : 'Create Source'}</button></>}>
      <div className="fg">
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Name<span className="req">*</span></label>
          <input className="in" placeholder="e.g. Summit Bookings 2025" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="fd">
          <label className="fd-l">Type</label>
          <select className="in" value={type} onChange={(e) => setType(e.target.value)}>
            {SHEET_TYPES.map((t) => <option key={t} value={t}>{SHEET_TYPE_LABEL[t]}</option>)}
          </select>
        </div>
        <div className="fd">
          <label className="fd-l">Sync Frequency</label>
          <select className="in" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Google Sheet URL<span className="req">*</span></label>
          <input className="in" placeholder="https://docs.google.com/spreadsheets/d/…" value={url}
            onChange={(e) => { setUrl(e.target.value); setTabs(null); }} />
          <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>Paste the full Google Sheets URL — the Sheet ID will be extracted automatically.</span>
        </div>
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Worksheet / Tab Name</label>
          <div style={{ display: 'flex', gap: 7 }}>
            {tabs ? (
              <select className="in" style={{ flex: 1 }} value={worksheet} onChange={(e) => setWorksheet(e.target.value)}>
                {tabs.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            ) : (
              <input className="in" style={{ flex: 1 }} placeholder="Sheet1" value={worksheet} onChange={(e) => setWorksheet(e.target.value)} />
            )}
            <button type="button" className="btn btn-s btn-sm" style={{ flexShrink: 0 }} disabled={detecting} onClick={detectTabs}>
              <Icon name="refresh" size={13} />{detecting ? 'Detecting…' : 'Detect Tabs'}
            </button>
          </div>
        </div>
        <div className="fd full" style={{ gridColumn: '1/-1', flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" id="sheet-sync-enabled" checked={syncEnabled} onChange={(e) => setSyncEnabled(e.target.checked)} />
          <label className="fd-l" htmlFor="sheet-sync-enabled" style={{ margin: 0 }}>Sync Enabled</label>
        </div>
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Description</label>
          <textarea className="in" rows={2} placeholder="What data does this sheet contain?" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Notes</label>
          <textarea className="in" rows={2} placeholder="Admin notes…" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
