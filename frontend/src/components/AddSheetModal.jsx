import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal';
import { Icon } from '../lib/icons';
import * as gsyncApi from '../api/googleSync';
import * as sheetSourcesApi from '../api/sheetSources';
import { useToast } from '../context/ToastContext';

// Real backend, two calls behind one form:
//   POST /api/google-sync/targets/              the push, module + columns + tab
//   POST /api/reports/sources/                  the registry entry for that sheet
//   POST /api/reports/sources/list-worksheets/  live tab detection
// The catalogue at /api/google-sync/catalog/ is the only source of columns, so
// this form cannot offer one the runner would reject.

const EMPTY = {
  name: '', spreadsheet_id: '', tab_name: '', module: '', columns: [], is_enabled: true,
  sheet_type: 'custom', frequency: 'manual', description: '', notes: '',
};

const SHEET_TYPES = [
  ['bookings', 'Bookings'], ['events', 'Events'], ['delegates', 'Delegates'],
  ['revenue', 'Revenue'], ['pipeline', 'Pipeline'], ['attendance', 'Attendance'],
  ['custom', 'Custom'],
];
const FREQUENCIES = [
  ['manual', 'Manual only'], ['hourly', 'Every hour'], ['daily', 'Daily'], ['weekly', 'Weekly'],
];

// .lnk only carries colour, so a <button> using it needs the chrome removed.
const linkBtn = (off) => ({
  border: 0, background: 'none', padding: 0, font: 'inherit', fontSize: 11,
  cursor: off ? 'default' : 'pointer', opacity: off ? 0.45 : 1,
});

/**
 * Add a sheet in one step; register the spreadsheet and create the push that
 * fills it.
 *
 * There used to be two buttons here, Add Sheet and New push, and they built two
 * unrelated records; a reports source is read FROM, a push target is written TO,
 * and neither knew the other existed. One form now writes both, so a sheet added
 * on this page is a sheet the Push button can actually fill.
 *
 * `modules` is the catalogue payload. Column order is PICK order, not catalogue
 * order, because that is the order they land in the sheet and a person choosing
 * "email, then name" means the sheet to read that way.
 *
 * Editing touches the push only; the registry entry is written once, when the
 * sheet is added, and nothing later re-reads it.
 */
export default function AddSheetModal({ target, modules, onClose, onSaved }) {
  const toast = useToast();
  const editing = !!target;

  const [form, setForm] = useState(() => (target ? {
    ...EMPTY,
    name: target.name || '',
    spreadsheet_id: target.spreadsheet_id || '',
    tab_name: target.tab_name || '',
    module: target.module || '',
    columns: [...(target.columns || [])],
    is_enabled: target.is_enabled !== false,
  } : { ...EMPTY, module: modules[0]?.key || '' }));
  const [tabs, setTabs] = useState(null);
  const [detecting, setDetecting] = useState(false);
  const [filter, setFilter] = useState('');
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  const current = useMemo(
    () => modules.find((m) => m.key === form.module) || null,
    [modules, form.module],
  );

  // A column key only means anything inside its module, so switching module
  // cannot carry the old selection across.
  useEffect(() => {
    setForm((f) => (current && f.columns.some((k) => !current.columns.some((c) => c.key === k))
      ? { ...f, columns: [] } : f));
  }, [current]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const cols = current?.columns || [];
    return q ? cols.filter((c) => c.label.toLowerCase().includes(q) || c.key.includes(q)) : cols;
  }, [current, filter]);

  const labelFor = (key) => current?.columns.find((c) => c.key === key)?.label || key;

  // An empty filter result has nothing to take, so treat it as "nothing left".
  const allVisibleSelected = visible.length === 0 || visible.every((c) => form.columns.includes(c.key));
  const someVisibleSelected = visible.some((c) => form.columns.includes(c.key));

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: null }));
  }

  function toggle(key) {
    setForm((f) => ({
      ...f,
      columns: f.columns.includes(key)
        ? f.columns.filter((k) => k !== key)
        : [...f.columns, key],
    }));
    setErrors((e) => ({ ...e, columns: null }));
  }

  // Select all / clear act on what the filter is showing, so a filtered list is
  // a way to take a whole group at once. New keys go on the end in catalogue
  // order, which keeps the pick-order rule intact for anything already chosen.
  function selectAllVisible() {
    setForm((f) => ({
      ...f,
      columns: [...f.columns, ...visible.map((c) => c.key).filter((k) => !f.columns.includes(k))],
    }));
    setErrors((e) => ({ ...e, columns: null }));
  }

  function clearVisible() {
    const drop = new Set(visible.map((c) => c.key));
    setForm((f) => ({ ...f, columns: f.columns.filter((k) => !drop.has(k)) }));
  }

  // The tab a push writes does not have to exist yet, so detection is an aid,
  // never a gate; a name typed by hand is still accepted.
  async function detectTabs() {
    const url = form.spreadsheet_id.trim();
    if (!url) { toast('Paste a Google Sheet URL first', 'er'); return; }
    setDetecting(true);
    try {
      const worksheets = await sheetSourcesApi.listWorksheets(url);
      if (!worksheets.length) { toast('No worksheets found in that sheet', 'wn'); return; }
      setTabs(worksheets);
      toast('Found ' + worksheets.length + ' worksheet' + (worksheets.length === 1 ? '' : 's'), 'ok');
    } catch {
      toast('Could not detect worksheets, check the URL and sharing permissions', 'er');
    } finally {
      setDetecting(false);
    }
  }

  async function save() {
    if (saving) return;
    const body = {
      name: form.name.trim(),
      spreadsheet_id: form.spreadsheet_id.trim(),
      tab_name: form.tab_name.trim(),
      module: form.module,
      columns: form.columns,
      is_enabled: form.is_enabled,
    };
    const local = {};
    if (!body.name) local.name = 'A name is required';
    if (!body.spreadsheet_id) local.spreadsheet_id = 'A sheet URL or ID is required';
    if (!body.tab_name) local.tab_name = 'A tab name is required';
    if (!body.columns.length) local.columns = 'Pick at least one column';
    if (Object.keys(local).length) { setErrors(local); return; }

    setSaving(true);
    try {
      if (editing) await gsyncApi.updateTarget(target.id, body);
      else await gsyncApi.createTarget(body);
    } catch (err) {
      const detail = err.response?.data;
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        // DRF returns {field: [message]}; non_field_errors carries the unique
        // (sheet, tab) clash, which is the one a person hits most often.
        const mapped = {};
        Object.entries(detail).forEach(([k, v]) => {
          mapped[k] = Array.isArray(v) ? String(v[0]) : String(v);
        });
        setErrors(mapped);
        toast(mapped.non_field_errors || 'Could not save, check the highlighted fields', 'er');
      } else {
        toast('Could not save the sheet', 'er');
      }
      setSaving(false);
      return;
    }

    // The registry entry is the secondary record; the push already exists and
    // runs without it, so a rejection here is a warning rather than a failure.
    let registryWarning = '';
    if (!editing) {
      try {
        await sheetSourcesApi.addSource({
          name: body.name, url: body.spreadsheet_id, worksheet: body.tab_name,
          type: form.sheet_type, frequency: form.frequency, syncEnabled: form.is_enabled,
          description: form.description.trim(), notes: form.notes.trim(),
        });
      } catch {
        registryWarning = body.name + ' created, but it could not be added to the sheet registry';
      }
    }

    setSaving(false);
    onClose();
    if (registryWarning) toast(registryWarning, 'wn');
    else toast(body.name + (editing ? ' updated' : ' added'), 'ok');
    onSaved?.();
  }

  const err = (field) => (errors[field]
    ? <span style={{ fontSize: 10.5, color: 'var(--red)' }}>{errors[field]}</span> : null);

  return (
    <Modal size="lg"
      title={editing ? 'Edit sheet' : 'Add Google Sheet'}
      sub="Pick a module and the columns you want, and they are written to one tab of one spreadsheet."
      onClose={onClose}
      footer={<>
        <button className="btn btn-s" onClick={onClose}>Cancel</button>
        <button className="btn btn-p" disabled={saving} onClick={save}>
          <Icon name="sheet" size={15} />{saving ? 'Saving…' : (editing ? 'Save changes' : 'Add sheet')}
        </button>
      </>}>
      <div className="fg">
        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Name<span className="req">*</span></label>
          <input className="in" placeholder="e.g. Delegate contacts" value={form.name}
            onChange={(e) => set('name', e.target.value)} />
          {err('name')}
        </div>

        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">Google Sheet URL or ID<span className="req">*</span></label>
          <div style={{ display: 'flex', gap: 7 }}>
            <input className="in" style={{ flex: 1 }} placeholder="https://docs.google.com/spreadsheets/d/…"
              value={form.spreadsheet_id}
              onChange={(e) => { set('spreadsheet_id', e.target.value); setTabs(null); }} />
            <button type="button" className="btn btn-s btn-sm" style={{ flexShrink: 0 }}
              disabled={detecting} onClick={detectTabs}>
              <Icon name="refresh" size={13} />{detecting ? 'Detecting…' : 'Detect Tabs'}
            </button>
          </div>
          <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>
            Paste the full URL, the ID is taken out of it. The service account must have Editor access.
          </span>
          {err('spreadsheet_id')}
        </div>

        <div className="fd">
          <label className="fd-l">Tab<span className="req">*</span></label>
          <input className="in" list={tabs ? 'detected-tabs' : undefined} placeholder="e.g. Delegate contacts"
            value={form.tab_name} onChange={(e) => set('tab_name', e.target.value)} />
          {tabs ? (
            <datalist id="detected-tabs">
              {tabs.map((t) => <option key={t} value={t} />)}
            </datalist>
          ) : null}
          <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>Created if it does not exist. Replaced in full on every push.</span>
          {err('tab_name')}
          {err('non_field_errors')}
        </div>

        <div className="fd">
          <label className="fd-l">Module<span className="req">*</span></label>
          <select className="in" value={form.module} onChange={(e) => set('module', e.target.value)}>
            {modules.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
          {current?.description
            ? <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>{current.description}</span> : null}
          {err('module')}
        </div>

        {editing ? null : (
          <>
            <div className="fd">
              <label className="fd-l">Sheet type</label>
              <select className="in" value={form.sheet_type} onChange={(e) => set('sheet_type', e.target.value)}>
                {SHEET_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>How the sheet is filed in the registry.</span>
            </div>
            <div className="fd">
              <label className="fd-l">Sync frequency</label>
              <select className="in" value={form.frequency} onChange={(e) => set('frequency', e.target.value)}>
                {FREQUENCIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
          </>
        )}

        <div className="fd full" style={{ gridColumn: '1/-1' }}>
          <label className="fd-l">
            Columns<span className="req">*</span>
            <span className="dim" style={{ fontWeight: 400, marginLeft: 6 }}>
              {form.columns.length} of {current?.columns.length || 0} selected
            </span>
            <span style={{ float: 'right', display: 'inline-flex', gap: 10, fontWeight: 400 }}>
              <button type="button" className="lnk" disabled={allVisibleSelected}
                style={linkBtn(allVisibleSelected)} onClick={selectAllVisible}>
                {filter.trim() ? `Select all ${visible.length} matching` : 'Select all'}
              </button>
              <button type="button" className="lnk" disabled={!someVisibleSelected}
                style={linkBtn(!someVisibleSelected)} onClick={clearVisible}>
                {filter.trim() ? 'Clear matching' : 'Clear all'}
              </button>
            </span>
          </label>

          {form.columns.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 7 }}>
              {form.columns.map((k, i) => (
                <span key={k} className="tg bg-neutral" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  <span className="mono dim" style={{ fontSize: 10 }}>{i + 1}</span>
                  {labelFor(k)}
                  <button type="button" aria-label={'Remove ' + labelFor(k)}
                    style={{ border: 0, background: 'none', cursor: 'pointer', padding: 0, display: 'flex', color: 'inherit' }}
                    onClick={() => toggle(k)}><Icon name="x" size={11} /></button>
                </span>
              ))}
            </div>
          ) : null}

          <input className="in in-s" placeholder="Filter columns…" value={filter}
            onChange={(e) => setFilter(e.target.value)} style={{ marginBottom: 6 }} />

          <div style={{
            maxHeight: 220, overflowY: 'auto', border: '1px solid var(--border)',
            borderRadius: 'var(--r-md)', padding: 8,
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 4,
          }}>
            {visible.length ? visible.map((c) => (
              <label key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, cursor: 'pointer', padding: '3px 4px' }}>
                <input type="checkbox" checked={form.columns.includes(c.key)} onChange={() => toggle(c.key)} />
                <span>{c.label}</span>
              </label>
            )) : (
              <span className="dim" style={{ fontSize: 12 }}>No column matches that filter.</span>
            )}
          </div>
          <span style={{ fontSize: 10.5, color: 'var(--text-4)' }}>
            Columns appear in the sheet in the order you pick them.
          </span>
          {err('columns')}
        </div>

        {editing ? null : (
          <>
            <div className="fd full" style={{ gridColumn: '1/-1' }}>
              <label className="fd-l">Description</label>
              <textarea className="in" rows={2} placeholder="What data does this sheet carry?"
                value={form.description} onChange={(e) => set('description', e.target.value)} />
            </div>
            <div className="fd full" style={{ gridColumn: '1/-1' }}>
              <label className="fd-l">Notes</label>
              <textarea className="in" rows={2} placeholder="Admin notes…"
                value={form.notes} onChange={(e) => set('notes', e.target.value)} />
            </div>
          </>
        )}

        <div className="fd full" style={{ gridColumn: '1/-1', flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <input type="checkbox" id="sheet-target-enabled" checked={form.is_enabled}
            onChange={(e) => set('is_enabled', e.target.checked)} />
          <label className="fd-l" htmlFor="sheet-target-enabled" style={{ margin: 0 }}>Enabled</label>
        </div>
      </div>
    </Modal>
  );
}
