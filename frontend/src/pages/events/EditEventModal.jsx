import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Modal from '../../components/Modal';
import { Icon } from '../../lib/icons';
import { NumField } from '../../components/UI';
import { Av, EvBadge } from '../../components/Badge';
import { avc, ini } from '../../lib/helpers';
import { OWNER_FIELDS, ownerOf } from '../../lib/owners';
import { EVENT_STATUSES, YES_NO, VR1_STATUS, SALES_CHECK_OPTIONS } from '../../lib/constants';
import * as usersApi from '../../api/users';
import { useFetch } from '../../hooks/useFetch';
import { useToast } from '../../context/ToastContext';
import { useConfirm } from '../../context/ConfirmContext';
import * as eventsApi from '../../api/events';

// The row list lives in lib/owners.js now. It was written out by hand here, in
// EditEventModal, in the Events table and in the drawer's Teams tab — four copies
// of one list, and the two here were POSITIONAL, so a label and a key could drift
// apart silently and the form would write the wrong column.
//
// The selects below deliberately keep reading form values RAW rather than through
// ownerOf(): an inherited name is the owning team's answer, and writing it into
// the event would freeze "whoever leads Telemarketing" into one person's name on
// the next unrelated save.
const OWNER_LABELS = OWNER_FIELDS.map((f) => f.label);
const OWNER_KEYS = OWNER_FIELDS.map((f) => f.key);

/**
 * `owner` is either a plain name or an ownerOf() result — see the twin of this
 * function in bookings/EditBookingModal.jsx. Chips whose column is blank on
 * every event now show the owning team's lead, italicised and attributed in the
 * tooltip rather than passed off as this event's own value.
 */
function ownerChip(roleLabel, owner) {
  const map = { Sales: ['--green-bg', '--green-tx'], SCA: ['--blue-bg', '--blue-tx'], Telemarketing: ['--violet-bg', '--violet-tx'], 'Market Research': ['--cyan-bg', '--cyan-tx'], SpEx: ['--violet-bg', '--violet-tx'] };
  const c = map[roleLabel] || ['--n-75', '--text-3'];
  const personName = typeof owner === 'string' ? owner : (owner && owner.name) || '';
  const inherited = typeof owner === 'object' && owner && owner.inherited;
  const team = (typeof owner === 'object' && owner && owner.team) || '';
  if (!personName || personName === '—') return null;
  return (
    <span key={roleLabel} title={inherited ? `${roleLabel}: inherited from ${team || 'the owning team'} — no value set on this event` : undefined} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '3px 11px 3px 3px', borderRadius: 999, background: `var(${c[0]})` }}>
      <Av name={personName} size="xs" />
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
        <span style={{ fontSize: 8, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.06em', color: `var(${c[1]})` }}>{roleLabel}</span>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text)', fontStyle: inherited ? 'italic' : undefined }}>{personName}</span>
      </span>
    </span>
  );
}

export default function EditEventModal({ event: ev, onClose, onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const nav = useNavigate();
  const { data: allUsers } = useFetch(usersApi.list, [], { initialData: [] });
  const pool = (allUsers || []).filter((u) => u.status === 'active');
  const [form, setForm] = useState({
    event_code: ev.event_code, name: ev.name, edition: ev.edition, status: ev.status,
    event_date: ev.event_date, end_date: ev.end_date, website_live_date: ev.website_live_date, location: ev.location,
    website: ev.website || '', web_bookings_enabled: ev.web_bookings_enabled || 'No', nearest_related: ev.nearest_related || '', vr1_status: ev.vr1_status || 'Not Sent',
    event_type: ev.event_type, capacity: ev.capacity, sales_check: ev.sales_check,
    email_marketing_name: ev.email_marketing_name || '', branding_name: ev.branding_name || '',
    annualisation: ev.annualisation || 'Annual', date_format: ev.date_format || 'DD-MM-YYYY',
    related_event_1: ev.related_event_1 || '', related_event_2: ev.related_event_2 || '', related_event_3: ev.related_event_3 || '',
    upcoming_event_1: ev.upcoming_event_1 || '', upcoming_event_2: ev.upcoming_event_2 || '', upcoming_event_3: ev.upcoming_event_3 || '',
    sales_team: ev.sales_team || '', sales_lead: ev.sales_lead, tele_team: ev.tele_team, mr_senior: ev.mr_senior, mr_junior: ev.mr_junior, spex_lead: ev.spex_lead, event_mgmt: ev.event_mgmt,
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save() {
    if (!form.event_code.trim() || !form.name.trim()) { toast('Event code and name are required', 'er'); return; }
    await eventsApi.update(ev.id, { ...form, capacity: +form.capacity || ev.capacity });
    onClose(); toast(form.event_code + ' updated', 'ok'); onSaved();
  }
  async function del() {
    onClose();
    const ok = await confirm({ title: 'Delete ' + ev.event_code + '?', danger: true, ok: 'Delete event', sub: 'This removes the event from the catalogue. Bookings that reference it are not deleted.', body: <p style={{ fontSize: 12.5, color: 'var(--text-3)' }}>This cannot be undone.</p> });
    if (ok) { await eventsApi.remove(ev.id); toast(ev.event_code + ' deleted', 'ok'); nav('/events'); }
  }

  return (
    <Modal size="full" onClose={onClose}
      header={
        <div className="md-h">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, flexWrap: 'wrap' }}>
            <span className="av av-lg" style={{ background: avc(ev.event_code) }}>{ini(ev.event_code)}</span>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                <h2 style={{ fontSize: 17 }}>Edit Event</h2><EvBadge value={ev.status} /><span className="tg bg-neutral">{ev.event_type}</span>
              </div>
              <p>{ev.event_code} · {ev.name}</p>
            </div>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {ownerChip('SCA', ev.sales_team)}{ownerChip('Sales', ownerOf(ev, 'sales_lead'))}{ownerChip('SpEx', ownerOf(ev, 'spex_lead'))}{ownerChip('Market Research', ownerOf(ev, 'mr_senior'))}
            </div>
          </div>
          {/* See the identical fix/comment in bookings/EditBookingModal.jsx — same
              header shape, same top-vs-center misalignment. */}
          <button className="dr-x" aria-label="Close" style={{ marginLeft: 8, alignSelf: 'center' }} onClick={onClose}><Icon name="x" size={15} /></button>
        </div>
      }
      footJustify="space-between"
      footer={<>
        <button className="btn btn-g" style={{ color: 'var(--red)' }} onClick={del}><Icon name="trash" size={14} />Delete event</button>
        <div style={{ display: 'flex', gap: 7 }}>
          <button className="btn btn-s" onClick={onClose}>Cancel</button>
          <button className="btn btn-p" onClick={save}><Icon name="check" size={15} />Save changes</button>
        </div>
      </>}
    >
      <div className="fs">
        <div className="fs-t"><Icon name="calendar" size={13} />Identification</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event code<span className="req">*</span></label><input className="in mono" value={form.event_code} onChange={set('event_code')} /></div>
          <div className="fd"><label className="fd-l">Edition</label><input className="in" value={form.edition} onChange={set('edition')} /></div>
          <div className="fd"><label className="fd-l">Status</label><select className="in" value={form.status} onChange={set('status')}>{EVENT_STATUSES.map((s) => <option key={s}>{s}</option>)}</select></div>
          <div className="fd full" style={{ gridColumn: '1/-1' }}><label className="fd-l">Official event name<span className="req">*</span></label><input className="in" value={form.name} onChange={set('name')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="globe" size={13} />Schedule &amp; location</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Start date</label><input className="in" type="date" value={form.event_date} onChange={set('event_date')} /></div>
          <div className="fd"><label className="fd-l">End date</label><input className="in" type="date" value={form.end_date} onChange={set('end_date')} /></div>
          <div className="fd"><label className="fd-l">Website live</label><input className="in" type="date" value={form.website_live_date} onChange={set('website_live_date')} /></div>
          <div className="fd" style={{ gridColumn: '1/-1' }}><label className="fd-l">Location</label><input className="in" value={form.location} onChange={set('location')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="globe" size={13} />Web presence</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Website</label><input className="in" type="url" placeholder="https://example.com" value={form.website} onChange={set('website')} /></div>
          <div className="fd"><label className="fd-l">Web bookings</label><select className="in" value={form.web_bookings_enabled} onChange={set('web_bookings_enabled')}>{YES_NO.map((o) => <option key={o}>{o}</option>)}</select></div>
          <div className="fd"><label className="fd-l">Nearest related event</label><input className="in" value={form.nearest_related} onChange={set('nearest_related')} /></div>
          <div className="fd"><label className="fd-l">VR1 sent status</label><select className="in" value={form.vr1_status} onChange={set('vr1_status')}>{VR1_STATUS.map((o) => <option key={o}>{o}</option>)}</select></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="target" size={13} />Classification &amp; capacity</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event type</label><input className="in" value={form.event_type} onChange={set('event_type')} /></div>
          <div className="fd"><label className="fd-l">Capacity</label><NumField min={0} value={form.capacity} onChange={set('capacity')} /></div>
          <div className="fd"><label className="fd-l">Sales check</label><select className="in" value={form.sales_check} onChange={set('sales_check')}>{SALES_CHECK_OPTIONS.map((o) => <option key={o}>{o}</option>)}</select></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="target" size={13} />Naming &amp; metadata</div>
        <div className="fg c4">
          <div className="fd"><label className="fd-l">Event name for email marketing</label><input className="in" value={form.email_marketing_name} onChange={set('email_marketing_name')} /></div>
          <div className="fd"><label className="fd-l">Event name for branding</label><input className="in" value={form.branding_name} onChange={set('branding_name')} /></div>
          <div className="fd"><label className="fd-l">Annualisation</label><input className="in" value={form.annualisation} onChange={set('annualisation')} /></div>
          <div className="fd"><label className="fd-l">Date format</label><input className="in" value={form.date_format} onChange={set('date_format')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="link" size={13} />Related &amp; upcoming events</div>
        <div className="fg c3">
          <div className="fd"><label className="fd-l">Related event 1</label><input className="in" value={form.related_event_1} onChange={set('related_event_1')} /></div>
          <div className="fd"><label className="fd-l">Related event 2</label><input className="in" value={form.related_event_2} onChange={set('related_event_2')} /></div>
          <div className="fd"><label className="fd-l">Related event 3</label><input className="in" value={form.related_event_3} onChange={set('related_event_3')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 1</label><input className="in" value={form.upcoming_event_1} onChange={set('upcoming_event_1')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 2</label><input className="in" value={form.upcoming_event_2} onChange={set('upcoming_event_2')} /></div>
          <div className="fd"><label className="fd-l">Upcoming event 3</label><input className="in" value={form.upcoming_event_3} onChange={set('upcoming_event_3')} /></div>
        </div>
      </div>
      <div className="fs">
        <div className="fs-t"><Icon name="users" size={13} />Team ownership</div>
        <div className="fg c4">
          {OWNER_KEYS.map((k, i) => (
            <div className="fd" key={k}>
              <label className="fd-l">{OWNER_LABELS[i]}</label>
              <select className="in" value={form[k] || '—'} onChange={set(k)}>
                <option value="—">— Unassigned —</option>
                {/* The stored name, when it is not one of the active users. These
                    columns are free text and most of them arrived from the events
                    CSV, so a name belonging to a left or inactive user is common.
                    Without this option the select renders blank and the next save
                    replaces a real owner with whatever was clicked first. */}
                {form[k] && form[k] !== '—' && !pool.some((u) => u.name === form[k]) && <option>{form[k]}</option>}
                {pool.map((u) => <option key={u.id}>{u.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
